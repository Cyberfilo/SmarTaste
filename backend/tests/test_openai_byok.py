"""Tests for BYOK OpenAI API key management (12-01).

Covers service functions and HTTP integration tests for storing,
retrieving, validating, and managing user-provided OpenAI API keys.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.app import app  # noqa: E402
from musicmind.config import Settings  # noqa: E402
from musicmind.db.schema import metadata, user_api_keys, users  # noqa: E402
from musicmind.security.encryption import EncryptionService  # noqa: E402

JWT_SECRET = "test-jwt-secret-key-for-testing-only"
TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="
TEST_USER_ID = "test-user-openai-01"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_jwt(user_id: str) -> str:
    """Create a JWT access token for the given user."""
    import jwt

    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "email": "openai-test@example.com",
            "iat": now,
            "exp": now + timedelta(minutes=30),
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


async def _get_csrf_token(client: AsyncClient) -> str:
    """GET /health to obtain a csrftoken cookie."""
    resp = await client.get("/health")
    return resp.cookies.get("csrftoken") or client.cookies.get("csrftoken", "")


async def _authenticated_post(
    client: AsyncClient,
    url: str,
    *,
    json_data: dict | None = None,
    auth_cookies: dict[str, str],
) -> None:
    """POST with CSRF token and auth cookies."""
    csrf_token = await _get_csrf_token(client)
    all_cookies = {"csrftoken": csrf_token, **auth_cookies}
    return await client.post(
        url, json=json_data, headers={"x-csrf-token": csrf_token}, cookies=all_cookies
    )


async def _authenticated_get(
    client: AsyncClient,
    url: str,
    *,
    auth_cookies: dict[str, str],
) -> None:
    """GET with auth cookies."""
    return await client.get(url, cookies=auth_cookies)


async def _authenticated_delete(
    client: AsyncClient,
    url: str,
    *,
    auth_cookies: dict[str, str],
) -> None:
    """DELETE with CSRF token and auth cookies."""
    csrf_token = await _get_csrf_token(client)
    all_cookies = {"csrftoken": csrf_token, **auth_cookies}
    return await client.delete(
        url, headers={"x-csrf-token": csrf_token}, cookies=all_cookies
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for OpenAI BYOK tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id=TEST_USER_ID,
                email="openai-test@example.com",
                password_hash="hashed",
                display_name="OpenAI Test User",
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
def encryption() -> EncryptionService:
    """EncryptionService using the test Fernet key."""
    return EncryptionService(TEST_FERNET_KEY)


@pytest.fixture
def test_settings() -> Settings:
    """Settings for integration tests."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=TEST_FERNET_KEY,
        jwt_secret_key=JWT_SECRET,
        debug=True,
    )


@pytest.fixture
async def client(
    test_engine: AsyncEngine, test_settings: Settings
) -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient with test DB and settings overrides."""
    app.state.engine = test_engine
    app.state.settings = test_settings
    app.state.encryption = EncryptionService(TEST_FERNET_KEY)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_cookies() -> dict[str, str]:
    """Valid JWT access_token cookie for the test user."""
    return {"access_token": _make_jwt(TEST_USER_ID)}


# ── Service Function Tests ───────────────────────────────────────────────────


class TestOpenAIMaskApiKey:
    """Tests for mask_api_key pure function."""

    def test_mask_standard_key(self) -> None:
        """mask_api_key returns 'sk-...{last4}' format."""
        from musicmind.api.openai.service import mask_api_key

        result = mask_api_key("sk-proj-abcdefghijklmnop")
        assert result == "sk-...mnop"

    def test_mask_short_key(self) -> None:
        """mask_api_key returns '****' for very short keys."""
        from musicmind.api.openai.service import mask_api_key

        result = mask_api_key("short")
        assert result == "****"


class TestOpenAIEstimateCost:
    """Tests for estimate_chat_cost pure function."""

    def test_returns_gpt4o_pricing(self) -> None:
        """estimate_chat_cost returns GPT-4o pricing info."""
        from musicmind.api.openai.service import estimate_chat_cost

        result = estimate_chat_cost()
        assert result["model"] == "gpt-4o"
        assert "$" in result["estimated_cost_per_message"]
        assert "2.50" in result["input_token_price"]
        assert "10.00" in result["output_token_price"]


class TestOpenAIStoreApiKey:
    """Tests for store_api_key with service='openai'."""

    async def test_store_encrypts_and_inserts(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
    ) -> None:
        """store_api_key encrypts and inserts with service='openai'."""
        from musicmind.api.openai.service import store_api_key

        await store_api_key(
            test_engine, encryption, user_id=TEST_USER_ID, api_key="sk-proj-test1234"
        )

        async with test_engine.begin() as conn:
            result = await conn.execute(
                sa.select(user_api_keys).where(
                    sa.and_(
                        user_api_keys.c.user_id == TEST_USER_ID,
                        user_api_keys.c.service == "openai",
                    )
                )
            )
            row = result.first()

        assert row is not None
        assert encryption.decrypt(row.api_key_encrypted) == "sk-proj-test1234"


class TestOpenAIGetApiKeyStatus:
    """Tests for get_api_key_status with service='openai'."""

    async def test_no_key_returns_not_configured(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
    ) -> None:
        """get_api_key_status returns configured=False when no key stored."""
        from musicmind.api.openai.service import get_api_key_status

        result = await get_api_key_status(
            test_engine, encryption, user_id=TEST_USER_ID
        )
        assert result["configured"] is False
        assert result["masked_key"] is None
        assert result["service"] == "openai"

    async def test_key_stored_returns_configured(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
    ) -> None:
        """get_api_key_status returns configured=True with masked key."""
        from musicmind.api.openai.service import get_api_key_status, store_api_key

        await store_api_key(
            test_engine, encryption, user_id=TEST_USER_ID, api_key="sk-proj-abcdef1234"
        )

        result = await get_api_key_status(
            test_engine, encryption, user_id=TEST_USER_ID
        )
        assert result["configured"] is True
        assert result["masked_key"] == "sk-...1234"
        assert result["service"] == "openai"


class TestOpenAIDeleteApiKey:
    """Tests for delete_api_key with service='openai'."""

    async def test_delete_removes_row(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
    ) -> None:
        """delete_api_key removes the row and returns True."""
        from musicmind.api.openai.service import delete_api_key, store_api_key

        await store_api_key(
            test_engine, encryption, user_id=TEST_USER_ID, api_key="sk-to-delete"
        )
        result = await delete_api_key(test_engine, user_id=TEST_USER_ID)
        assert result is True

    async def test_delete_nonexistent_returns_false(
        self,
        test_engine: AsyncEngine,
    ) -> None:
        """delete_api_key returns False if no key exists."""
        from musicmind.api.openai.service import delete_api_key

        result = await delete_api_key(test_engine, user_id=TEST_USER_ID)
        assert result is False


class TestValidateOpenAIKey:
    """Tests for validate_openai_key async function."""

    async def test_valid_key_returns_true(self) -> None:
        """validate_openai_key returns valid=True on success."""
        from musicmind.api.openai.service import validate_openai_key

        mock_response = MagicMock()
        mock_client = AsyncMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch(
            "musicmind.api.openai.service.openai.AsyncOpenAI",
            return_value=mock_client,
        ):
            result = await validate_openai_key("sk-valid-key")

        assert result["valid"] is True
        assert result["error"] is None

    async def test_invalid_key_returns_false(self) -> None:
        """validate_openai_key returns valid=False on AuthenticationError."""
        import openai

        from musicmind.api.openai.service import validate_openai_key

        mock_client = AsyncMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=openai.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401),
                body=None,
            )
        )

        with patch(
            "musicmind.api.openai.service.openai.AsyncOpenAI",
            return_value=mock_client,
        ):
            result = await validate_openai_key("sk-invalid-key")

        assert result["valid"] is False
        assert "Invalid API key" in result["error"]


# ── HTTP Integration Tests ───────────────────────────────────────────────────


class TestOpenAIKeyEndpoints:
    """Integration tests for /api/openai/key/* endpoints."""

    async def test_store_openai_key(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """POST /api/openai/key stores a key (201)."""
        resp = await _authenticated_post(
            client,
            "/api/openai/key",
            json_data={"api_key": "sk-proj-test-key-12345"},
            auth_cookies=auth_cookies,
        )
        assert resp.status_code == 201
        assert resp.json()["message"] == "API key stored"

    async def test_key_status_configured(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """GET /api/openai/key/status returns configured=true after storing."""
        await _authenticated_post(
            client,
            "/api/openai/key",
            json_data={"api_key": "sk-proj-status-test"},
            auth_cookies=auth_cookies,
        )
        resp = await _authenticated_get(
            client, "/api/openai/key/status", auth_cookies=auth_cookies
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["service"] == "openai"

    async def test_key_status_not_configured(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """GET /api/openai/key/status returns configured=false when no key."""
        resp = await _authenticated_get(
            client, "/api/openai/key/status", auth_cookies=auth_cookies
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is False

    async def test_validate_key(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """POST /api/openai/key/validate validates stored key (mocked)."""
        # Store a key first
        await _authenticated_post(
            client,
            "/api/openai/key",
            json_data={"api_key": "sk-proj-validate-me"},
            auth_cookies=auth_cookies,
        )
        with patch(
            "musicmind.api.openai.router.validate_openai_key",
            new=AsyncMock(return_value={"valid": True, "error": None}),
        ):
            resp = await _authenticated_post(
                client,
                "/api/openai/key/validate",
                auth_cookies=auth_cookies,
            )
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    async def test_delete_key(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """DELETE /api/openai/key removes the key."""
        await _authenticated_post(
            client,
            "/api/openai/key",
            json_data={"api_key": "sk-proj-delete-me"},
            auth_cookies=auth_cookies,
        )
        resp = await _authenticated_delete(
            client, "/api/openai/key", auth_cookies=auth_cookies
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "API key removed"

    async def test_delete_nonexistent_key_returns_404(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """DELETE /api/openai/key returns 404 when no key."""
        resp = await _authenticated_delete(
            client, "/api/openai/key", auth_cookies=auth_cookies
        )
        assert resp.status_code == 404

    async def test_cost_estimate(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """GET /api/openai/key/cost returns pricing info."""
        resp = await _authenticated_get(
            client, "/api/openai/key/cost", auth_cookies=auth_cookies
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "gpt-4o"
        assert "$" in data["estimated_cost_per_message"]
