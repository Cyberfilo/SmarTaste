"""Integration tests for BYOK Claude API key management endpoints (BYOK-01 through BYOK-04)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.api.claude.service import mask_api_key  # noqa: E402
from musicmind.app import app  # noqa: E402
from musicmind.config import Settings  # noqa: E402
from musicmind.db.schema import metadata, users  # noqa: E402
from musicmind.security.encryption import EncryptionService  # noqa: E402

JWT_SECRET = "test-jwt-secret-key-for-testing-only"
TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_csrf_token(client: AsyncClient) -> str:
    """GET /health to obtain a csrftoken cookie."""
    resp = await client.get("/health")
    return resp.cookies.get("csrftoken") or client.cookies.get("csrftoken", "")


async def _authenticated_post(
    client: AsyncClient,
    url: str,
    *,
    json: dict | None = None,
    auth_cookies: dict[str, str],
) -> httpx.Response:
    """POST with CSRF token and auth cookies."""
    csrf_token = await _get_csrf_token(client)
    all_cookies = {"csrftoken": csrf_token, **auth_cookies}
    return await client.post(
        url, json=json, headers={"x-csrf-token": csrf_token}, cookies=all_cookies
    )


async def _authenticated_delete(
    client: AsyncClient,
    url: str,
    *,
    auth_cookies: dict[str, str],
) -> httpx.Response:
    """DELETE with CSRF token and auth cookies."""
    csrf_token = await _get_csrf_token(client)
    all_cookies = {"csrftoken": csrf_token, **auth_cookies}
    return await client.delete(url, headers={"x-csrf-token": csrf_token}, cookies=all_cookies)


async def _authenticated_get(
    client: AsyncClient,
    url: str,
    *,
    auth_cookies: dict[str, str],
) -> httpx.Response:
    """GET with auth cookies."""
    return await client.get(url, cookies=auth_cookies)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def engine_and_tables() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine with all tables and a test user."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    # Insert a test user so FK constraints on user_api_keys are satisfied
    async with engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id="test-user-claude-01",
                email="claude-test@example.com",
                password_hash="hashed",
                display_name="Claude Test User",
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
def test_settings() -> Settings:
    """Settings for Claude BYOK tests."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=TEST_FERNET_KEY,
        jwt_secret_key=JWT_SECRET,
        debug=True,
    )


@pytest.fixture
async def client(
    engine_and_tables: AsyncEngine, test_settings: Settings
) -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient with test DB and settings overrides."""
    app.state.engine = engine_and_tables
    app.state.settings = test_settings
    app.state.encryption = EncryptionService(TEST_FERNET_KEY)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_cookies() -> dict[str, str]:
    """Valid JWT access_token cookie for the test user."""
    import jwt

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "test-user-claude-01",
            "email": "claude-test@example.com",
            "iat": now,
            "exp": now + timedelta(minutes=30),
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"access_token": token}


# ── BYOK-01: Store Key ──────────────────────────────────────────────────────


async def test_store_key_returns_201(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """POST /api/claude/key with valid api_key stores encrypted key (BYOK-01)."""
    resp = await _authenticated_post(
        client,
        "/api/claude/key",
        json={"api_key": "sk-ant-test-key-12345678"},
        auth_cookies=auth_cookies,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "message" in data
    assert data["message"] == "API key stored"

    # Verify key is actually stored by checking status
    status_resp = await _authenticated_get(
        client, "/api/claude/key/status", auth_cookies=auth_cookies
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["configured"] is True
    assert status_data["masked_key"] is not None
    assert "5678" in status_data["masked_key"]


async def test_store_key_unauthenticated_returns_401(
    client: AsyncClient,
) -> None:
    """POST /api/claude/key without auth returns 401 (BYOK-01)."""
    csrf_token = await _get_csrf_token(client)
    resp = await client.post(
        "/api/claude/key",
        json={"api_key": "sk-ant-test-key-12345678"},
        headers={"x-csrf-token": csrf_token},
        cookies={"csrftoken": csrf_token},
    )
    assert resp.status_code == 401


# ── BYOK-02: Validate Key ───────────────────────────────────────────────────


async def test_validate_key_success(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """POST /api/claude/key/validate with mocked Anthropic success returns valid=true (BYOK-02)."""
    # First store a key
    await _authenticated_post(
        client,
        "/api/claude/key",
        json={"api_key": "sk-ant-valid-key-abcdefgh"},
        auth_cookies=auth_cookies,
    )

    # Mock validate_anthropic_key at the router import level
    with patch(
        "musicmind.api.claude.router.validate_anthropic_key",
        new_callable=AsyncMock,
        return_value={"valid": True, "error": None},
    ):
        resp = await _authenticated_post(
            client,
            "/api/claude/key/validate",
            auth_cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["valid"] is True
    assert data["error"] is None


async def test_validate_key_invalid(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """POST /api/claude/key/validate with mocked AuthenticationError returns valid=false (BYOK-02)."""
    # First store a key
    await _authenticated_post(
        client,
        "/api/claude/key",
        json={"api_key": "sk-ant-invalid-key-12345678"},
        auth_cookies=auth_cookies,
    )

    with patch(
        "musicmind.api.claude.router.validate_anthropic_key",
        new_callable=AsyncMock,
        return_value={"valid": False, "error": "Invalid API key"},
    ):
        resp = await _authenticated_post(
            client,
            "/api/claude/key/validate",
            auth_cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["valid"] is False
    assert data["error"] == "Invalid API key"


async def test_validate_no_key_stored(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """POST /api/claude/key/validate with no key stored returns valid=false (BYOK-02)."""
    resp = await _authenticated_post(
        client,
        "/api/claude/key/validate",
        auth_cookies=auth_cookies,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["valid"] is False
    assert "No API key" in data["error"]


# ── BYOK-03: Update & Delete ────────────────────────────────────────────────


async def test_update_key_overwrites(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """POST /api/claude/key twice overwrites; GET status shows latest masked key (BYOK-03)."""
    # Store first key
    await _authenticated_post(
        client,
        "/api/claude/key",
        json={"api_key": "sk-ant-first-key-AAAA"},
        auth_cookies=auth_cookies,
    )

    # Store second key (should overwrite)
    await _authenticated_post(
        client,
        "/api/claude/key",
        json={"api_key": "sk-ant-second-key-BBBB"},
        auth_cookies=auth_cookies,
    )

    # Status should show the second key's mask
    status_resp = await _authenticated_get(
        client, "/api/claude/key/status", auth_cookies=auth_cookies
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["configured"] is True
    assert "BBBB" in status_data["masked_key"]


async def test_delete_key_removes(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """DELETE /api/claude/key removes key; subsequent GET status returns configured=false (BYOK-03)."""
    # Store a key
    await _authenticated_post(
        client,
        "/api/claude/key",
        json={"api_key": "sk-ant-delete-me-12345678"},
        auth_cookies=auth_cookies,
    )

    # Delete it
    resp = await _authenticated_delete(
        client,
        "/api/claude/key",
        auth_cookies=auth_cookies,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "API key removed"

    # Status should show not configured
    status_resp = await _authenticated_get(
        client, "/api/claude/key/status", auth_cookies=auth_cookies
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["configured"] is False


async def test_delete_no_key_returns_404(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """DELETE /api/claude/key with no key returns 404 (BYOK-03)."""
    resp = await _authenticated_delete(
        client,
        "/api/claude/key",
        auth_cookies=auth_cookies,
    )
    assert resp.status_code == 404
    assert "No API key" in resp.json()["detail"]


# ── BYOK-04: Cost Estimate ──────────────────────────────────────────────────


async def test_cost_estimate_returns_pricing(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/claude/key/cost returns model name and pricing strings (BYOK-04)."""
    resp = await _authenticated_get(
        client, "/api/claude/key/cost", auth_cookies=auth_cookies
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "model" in data
    assert "estimated_cost_per_message" in data
    assert "input_token_price" in data
    assert "output_token_price" in data
    assert "claude" in data["model"].lower() or "sonnet" in data["model"].lower()


# ── Status Tests ─────────────────────────────────────────────────────────────


async def test_status_no_key(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/claude/key/status with no key returns configured=false, masked_key=null."""
    resp = await _authenticated_get(
        client, "/api/claude/key/status", auth_cookies=auth_cookies
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["configured"] is False
    assert data["masked_key"] is None


# ── Unit Tests ───────────────────────────────────────────────────────────────


def test_mask_api_key_formats() -> None:
    """mask_api_key produces correct masked output for various key formats."""
    # Standard long key
    assert mask_api_key("sk-ant-api03-abcdefghijklmnopqrstuvwxyz") == "sk-ant-...wxyz"

    # Short key (< 8 chars)
    assert mask_api_key("short") == "****"
    assert mask_api_key("1234567") == "****"

    # Exactly 8 chars
    assert mask_api_key("12345678") == "sk-ant-...5678"

    # Very long key
    long_key = "sk-ant-" + "a" * 100 + "ZZZZ"
    assert mask_api_key(long_key) == "sk-ant-...ZZZZ"
