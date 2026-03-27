"""Tests for BYOK Claude API key management (04-01).

Covers database schema, Pydantic models, and service functions for
storing, retrieving, validating, and managing user-provided Anthropic API keys.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.db.schema import metadata, user_api_keys, users  # noqa: E402
from musicmind.security.encryption import EncryptionService  # noqa: E402

TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for BYOK tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def encryption() -> EncryptionService:
    """EncryptionService using the test Fernet key."""
    return EncryptionService(TEST_FERNET_KEY)


@pytest.fixture
def test_user_id() -> str:
    """Deterministic test user ID for BYOK tests."""
    return "test-user-id-byok-01"


async def _insert_test_user(engine: AsyncEngine, user_id: str) -> None:
    """Insert a minimal user row so FK constraints don't block inserts."""
    async with engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id=user_id,
                email=f"{user_id}@test.example.com",
                password_hash="hashed",
                display_name="Test User",
            )
        )


# ── Task 1: Schema and Pydantic Model Tests ─────────────────────────────────


class TestUserApiKeysTable:
    """Tests for user_api_keys table definition in schema.py."""

    def test_table_exists_in_metadata(self) -> None:
        """user_api_keys table exists in metadata."""
        assert "user_api_keys" in metadata.tables

    def test_table_columns(self) -> None:
        """user_api_keys has all required columns."""
        col_names = [c.name for c in user_api_keys.columns]
        assert "user_id" in col_names
        assert "service" in col_names
        assert "api_key_encrypted" in col_names
        assert "created_at" in col_names
        assert "updated_at" in col_names

    def test_composite_primary_key(self) -> None:
        """user_api_keys has composite PK on (user_id, service)."""
        pk_cols = [c.name for c in user_api_keys.primary_key.columns]
        assert "user_id" in pk_cols
        assert "service" in pk_cols

    def test_user_id_foreign_key(self) -> None:
        """user_id column references users.id."""
        fks = list(user_api_keys.c.user_id.foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"

    def test_api_key_encrypted_not_nullable(self) -> None:
        """api_key_encrypted column is not nullable."""
        assert user_api_keys.c.api_key_encrypted.nullable is False


class TestPydanticSchemas:
    """Tests for Pydantic request/response models in schemas.py."""

    def test_store_key_request_validates_min_length(self) -> None:
        """StoreKeyRequest requires api_key with min_length=1."""
        from musicmind.api.claude.schemas import StoreKeyRequest

        # Valid key
        req = StoreKeyRequest(api_key="sk-ant-api03-test")
        assert req.api_key == "sk-ant-api03-test"

        # Empty key should fail validation
        with pytest.raises(Exception):  # ValidationError
            StoreKeyRequest(api_key="")

    def test_key_status_response_fields(self) -> None:
        """KeyStatusResponse has configured, masked_key, service fields."""
        from musicmind.api.claude.schemas import KeyStatusResponse

        resp = KeyStatusResponse(configured=True, masked_key="sk-ant-...xyz1")
        assert resp.configured is True
        assert resp.masked_key == "sk-ant-...xyz1"
        assert resp.service == "anthropic"

    def test_key_status_response_defaults(self) -> None:
        """KeyStatusResponse defaults: masked_key=None, service=anthropic."""
        from musicmind.api.claude.schemas import KeyStatusResponse

        resp = KeyStatusResponse(configured=False)
        assert resp.masked_key is None
        assert resp.service == "anthropic"

    def test_validate_key_response_fields(self) -> None:
        """ValidateKeyResponse has valid and error fields."""
        from musicmind.api.claude.schemas import ValidateKeyResponse

        resp = ValidateKeyResponse(valid=True)
        assert resp.valid is True
        assert resp.error is None

        resp2 = ValidateKeyResponse(valid=False, error="Invalid API key")
        assert resp2.valid is False
        assert resp2.error == "Invalid API key"

    def test_cost_estimate_response_fields(self) -> None:
        """CostEstimateResponse has model, estimated_cost_per_message, prices."""
        from musicmind.api.claude.schemas import CostEstimateResponse

        resp = CostEstimateResponse(
            model="claude-sonnet-4-20250514",
            estimated_cost_per_message="$0.01-0.05",
            input_token_price="$3.00 / 1M tokens",
            output_token_price="$15.00 / 1M tokens",
        )
        assert resp.model == "claude-sonnet-4-20250514"
        assert "$" in resp.estimated_cost_per_message
        assert "input" not in resp.model  # sanity check


# ── Task 2: Service Function Tests ──────────────────────────────────────────


class TestMaskApiKey:
    """Tests for mask_api_key pure function."""

    def test_mask_standard_key(self) -> None:
        """mask_api_key returns 'sk-ant-...{last4}' format."""
        from musicmind.api.claude.service import mask_api_key

        result = mask_api_key("sk-ant-api03-abcdefghijklmnop")
        assert result == "sk-ant-...mnop"

    def test_mask_short_key(self) -> None:
        """mask_api_key returns '****' for very short keys."""
        from musicmind.api.claude.service import mask_api_key

        result = mask_api_key("short")
        assert result == "****"


class TestStoreApiKey:
    """Tests for store_api_key async function."""

    async def test_store_encrypts_and_inserts(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
        test_user_id: str,
    ) -> None:
        """store_api_key encrypts key and inserts row."""
        from musicmind.api.claude.service import store_api_key

        await _insert_test_user(test_engine, test_user_id)
        await store_api_key(
            test_engine, encryption, user_id=test_user_id, api_key="sk-ant-api03-test1234"
        )

        # Verify row exists with encrypted key
        async with test_engine.begin() as conn:
            result = await conn.execute(
                sa.select(user_api_keys).where(
                    user_api_keys.c.user_id == test_user_id,
                )
            )
            row = result.first()

        assert row is not None
        assert encryption.decrypt(row.api_key_encrypted) == "sk-ant-api03-test1234"

    async def test_store_overwrites_existing(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
        test_user_id: str,
    ) -> None:
        """Calling store_api_key again for same user overwrites the key (D-06)."""
        from musicmind.api.claude.service import store_api_key

        await _insert_test_user(test_engine, test_user_id)
        await store_api_key(
            test_engine, encryption, user_id=test_user_id, api_key="first-key"
        )
        await store_api_key(
            test_engine, encryption, user_id=test_user_id, api_key="second-key"
        )

        async with test_engine.begin() as conn:
            result = await conn.execute(
                sa.select(user_api_keys).where(
                    user_api_keys.c.user_id == test_user_id,
                )
            )
            rows = result.fetchall()

        # Should be exactly 1 row, not 2
        assert len(rows) == 1
        assert encryption.decrypt(rows[0].api_key_encrypted) == "second-key"


class TestGetApiKeyStatus:
    """Tests for get_api_key_status async function."""

    async def test_no_key_returns_not_configured(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
        test_user_id: str,
    ) -> None:
        """get_api_key_status returns configured=False when no key stored."""
        from musicmind.api.claude.service import get_api_key_status

        result = await get_api_key_status(
            test_engine, encryption, user_id=test_user_id
        )
        assert result["configured"] is False
        assert result["masked_key"] is None
        assert result["service"] == "anthropic"

    async def test_key_stored_returns_configured(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
        test_user_id: str,
    ) -> None:
        """get_api_key_status returns configured=True with masked key (D-02)."""
        from musicmind.api.claude.service import get_api_key_status, store_api_key

        await _insert_test_user(test_engine, test_user_id)
        await store_api_key(
            test_engine, encryption, user_id=test_user_id, api_key="sk-ant-api03-abcdef1234"
        )

        result = await get_api_key_status(
            test_engine, encryption, user_id=test_user_id
        )
        assert result["configured"] is True
        assert result["masked_key"] == "sk-ant-...1234"
        assert result["service"] == "anthropic"


class TestGetDecryptedApiKey:
    """Tests for get_decrypted_api_key async function."""

    async def test_returns_plaintext_key(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
        test_user_id: str,
    ) -> None:
        """get_decrypted_api_key returns plaintext key for valid user."""
        from musicmind.api.claude.service import get_decrypted_api_key, store_api_key

        await _insert_test_user(test_engine, test_user_id)
        await store_api_key(
            test_engine, encryption, user_id=test_user_id, api_key="sk-ant-api03-plaintext"
        )

        result = await get_decrypted_api_key(
            test_engine, encryption, user_id=test_user_id
        )
        assert result == "sk-ant-api03-plaintext"

    async def test_returns_none_for_no_key(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
        test_user_id: str,
    ) -> None:
        """get_decrypted_api_key returns None when no key stored."""
        from musicmind.api.claude.service import get_decrypted_api_key

        result = await get_decrypted_api_key(
            test_engine, encryption, user_id=test_user_id
        )
        assert result is None


class TestDeleteApiKey:
    """Tests for delete_api_key async function."""

    async def test_delete_removes_row(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
        test_user_id: str,
    ) -> None:
        """delete_api_key removes the row and returns True."""
        from musicmind.api.claude.service import delete_api_key, store_api_key

        await _insert_test_user(test_engine, test_user_id)
        await store_api_key(
            test_engine, encryption, user_id=test_user_id, api_key="sk-ant-to-delete"
        )

        result = await delete_api_key(test_engine, user_id=test_user_id)
        assert result is True

        # Verify row is gone
        async with test_engine.begin() as conn:
            res = await conn.execute(
                sa.select(user_api_keys).where(
                    user_api_keys.c.user_id == test_user_id,
                )
            )
            assert res.first() is None

    async def test_delete_nonexistent_returns_false(
        self,
        test_engine: AsyncEngine,
        test_user_id: str,
    ) -> None:
        """delete_api_key returns False if no key exists."""
        from musicmind.api.claude.service import delete_api_key

        result = await delete_api_key(test_engine, user_id=test_user_id)
        assert result is False


class TestValidateAnthropicKey:
    """Tests for validate_anthropic_key async function."""

    async def test_valid_key_returns_true(self) -> None:
        """validate_anthropic_key returns valid=True on success (D-03)."""
        from musicmind.api.claude.service import validate_anthropic_key

        mock_response = MagicMock()
        mock_client = AsyncMock()
        mock_client.messages = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch(
            "musicmind.api.claude.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            result = await validate_anthropic_key("sk-ant-valid-key")

        assert result["valid"] is True
        assert result["error"] is None

    async def test_invalid_key_returns_false(self) -> None:
        """validate_anthropic_key returns valid=False on AuthenticationError."""
        import anthropic

        from musicmind.api.claude.service import validate_anthropic_key

        mock_client = AsyncMock()
        mock_client.messages = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401),
                body=None,
            )
        )

        with patch(
            "musicmind.api.claude.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            result = await validate_anthropic_key("sk-ant-invalid-key")

        assert result["valid"] is False
        assert "Invalid API key" in result["error"]


class TestEstimateChatCost:
    """Tests for estimate_chat_cost pure function."""

    def test_returns_cost_estimate(self) -> None:
        """estimate_chat_cost returns CostEstimateResponse with pricing (D-07, D-08)."""
        from musicmind.api.claude.service import estimate_chat_cost

        result = estimate_chat_cost()
        assert "model" in result
        assert "claude-sonnet" in result["model"]
        assert "estimated_cost_per_message" in result
        assert "$" in result["estimated_cost_per_message"]
        assert "input_token_price" in result
        assert "output_token_price" in result
