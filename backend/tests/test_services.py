"""Integration tests for service connection endpoints (SVCN-01 through SVCN-06)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_SPOTIFY_CLIENT_ID", "test-spotify-client-id")
os.environ.setdefault("MUSICMIND_SPOTIFY_CLIENT_SECRET", "test-spotify-secret")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

import sqlalchemy as sa  # noqa: E402

from musicmind.api.services.service import (  # noqa: E402
    generate_pkce_pair,
    refresh_spotify_token,
    upsert_service_connection,
)
from musicmind.app import app  # noqa: E402
from musicmind.config import Settings  # noqa: E402
from musicmind.db.schema import metadata, service_connections, users  # noqa: E402
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


async def _insert_test_user(engine: AsyncEngine, user_id: str) -> None:
    """Insert a minimal user row so FK constraints don't block service_connections inserts."""
    async with engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id=user_id,
                email=f"{user_id}@test.example.com",
                password_hash="hashed",
                display_name="Test User",
            )
        )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for service tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def test_settings() -> Settings:
    """Settings with Spotify configured and Apple Music unconfigured (default)."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=TEST_FERNET_KEY,
        jwt_secret_key=JWT_SECRET,
        debug=True,
        spotify_client_id="test-spotify-client-id",
        spotify_client_secret="test-spotify-secret",
        spotify_redirect_uri="http://127.0.0.1:8000/api/services/spotify/callback",
    )


@pytest.fixture
def encryption() -> EncryptionService:
    """EncryptionService using the test Fernet key."""
    return EncryptionService(TEST_FERNET_KEY)


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
def auth_cookies(test_user_id: str) -> dict[str, str]:
    """Valid JWT access_token cookie for test_user_id."""
    import jwt

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": test_user_id,
            "email": "test@example.com",
            "iat": now,
            "exp": now + timedelta(minutes=30),
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"access_token": token}


@pytest.fixture
def test_user_id() -> str:
    """Deterministic test user ID."""
    return "test-user-id-services-01"


# ── SVCN-01: Spotify OAuth PKCE ──────────────────────────────────────────────


async def test_spotify_connect_returns_authorize_url(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """POST /api/services/spotify/connect returns 200 with Spotify authorize URL (SVCN-01)."""
    resp = await _authenticated_post(
        client, "/api/services/spotify/connect", auth_cookies=auth_cookies
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "authorize_url" in data
    url = data["authorize_url"]
    assert "accounts.spotify.com" in url
    assert "code_challenge_method=S256" in url
    assert "client_id=test-spotify-client-id" in url


async def test_spotify_connect_without_client_id_returns_400(
    test_engine: AsyncEngine,
    auth_cookies: dict[str, str],
) -> None:
    """POST /api/services/spotify/connect without Spotify config returns 400."""
    # Use _override to bypass env var by passing explicit None
    settings_no_spotify = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=TEST_FERNET_KEY,
        jwt_secret_key=JWT_SECRET,
        debug=True,
    )
    # Force spotify_client_id to None regardless of env vars
    object.__setattr__(settings_no_spotify, "spotify_client_id", None)

    app.state.engine = test_engine
    app.state.settings = settings_no_spotify
    app.state.encryption = EncryptionService(TEST_FERNET_KEY)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await _authenticated_post(
            c, "/api/services/spotify/connect", auth_cookies=auth_cookies
        )
    assert resp.status_code == 400
    assert "Spotify not configured" in resp.json()["detail"]


async def test_spotify_callback_stores_tokens(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """Spotify callback with valid state+code exchanges tokens and stores them (SVCN-01)."""
    await _insert_test_user(test_engine, test_user_id)

    # Step 1: Initiate connect to get PKCE state into session
    connect_resp = await _authenticated_post(
        client, "/api/services/spotify/connect", auth_cookies=auth_cookies
    )
    assert connect_resp.status_code == 200, connect_resp.text

    # Step 2: Mock Spotify token exchange and profile endpoints
    mock_token_data = {
        "access_token": "sp_access_token",
        "refresh_token": "sp_refresh_token",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": "user-read-private user-read-email",
    }
    mock_profile_data = {
        "id": "spotify_user_123",
        "email": "test@spotify.com",
        "display_name": "Test User",
    }

    # The router imports exchange_spotify_code and fetch_spotify_user_profile
    # directly, so we patch at the router's namespace.
    with (
        patch(
            "musicmind.api.services.router.exchange_spotify_code",
            new_callable=AsyncMock,
            return_value=mock_token_data,
        ),
        patch(
            "musicmind.api.services.router.fetch_spotify_user_profile",
            new_callable=AsyncMock,
            return_value=mock_profile_data,
        ),
    ):
        # Re-initiate connect inside the mock context so state is stored in session
        # and the callback mock is active for the same session.
        connect_resp2 = await _authenticated_post(
            client, "/api/services/spotify/connect", auth_cookies=auth_cookies
        )
        assert connect_resp2.status_code == 200
        authorize_url = connect_resp2.json()["authorize_url"]

        # Parse state from authorize URL
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(authorize_url)
        state = parse_qs(parsed.query)["state"][0]

        # Step 3: Call callback with matching state
        callback_resp = await client.get(
            f"/api/services/spotify/callback?code=test_auth_code&state={state}",
            cookies=dict(client.cookies),
        )
    assert callback_resp.status_code == 200, callback_resp.text
    data = callback_resp.json()
    assert data["message"] == "Spotify connected"
    assert data["service_user_id"] == "spotify_user_123"

    # Verify tokens stored in DB
    async with test_engine.begin() as conn:
        result = await conn.execute(
            sa.select(service_connections).where(
                sa.and_(
                    service_connections.c.user_id == test_user_id,
                    service_connections.c.service == "spotify",
                )
            )
        )
        row = result.first()
    assert row is not None
    assert encryption.decrypt(row.access_token_encrypted) == "sp_access_token"
    assert encryption.decrypt(row.refresh_token_encrypted) == "sp_refresh_token"
    assert row.service_user_id == "spotify_user_123"


async def test_spotify_callback_rejects_bad_state(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """Callback with mismatched state returns 400 (CSRF protection, SVCN-01)."""
    # Initiate connect to populate session
    connect_resp = await _authenticated_post(
        client, "/api/services/spotify/connect", auth_cookies=auth_cookies
    )
    assert connect_resp.status_code == 200

    # Call callback with WRONG state
    callback_resp = await client.get(
        "/api/services/spotify/callback?code=any_code&state=tampered_state_value",
        cookies=dict(client.cookies),
    )
    assert callback_resp.status_code == 400
    assert "Invalid state" in callback_resp.json()["detail"]


# ── SVCN-02: Apple Music ──────────────────────────────────────────────────────


async def test_apple_developer_token_endpoint(
    test_engine: AsyncEngine,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/services/apple-music/developer-token returns valid JWT (SVCN-02)."""
    # Generate a fresh ES256 key pair for the test
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem_bytes = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())

    with tempfile.NamedTemporaryFile(suffix=".p8", delete=False) as tmp:
        tmp.write(pem_bytes)
        tmp_path = tmp.name

    apple_settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=TEST_FERNET_KEY,
        jwt_secret_key=JWT_SECRET,
        debug=True,
        apple_team_id="TEST_TEAM_ID",
        apple_key_id="TEST_KEY_ID",
        apple_private_key_path=tmp_path,
    )
    app.state.engine = test_engine
    app.state.settings = apple_settings
    app.state.encryption = EncryptionService(TEST_FERNET_KEY)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(
            "/api/services/apple-music/developer-token",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "developer_token" in data
    token = data["developer_token"]
    # Token should be a non-empty string with JWT structure (3 dot-separated parts)
    assert isinstance(token, str)
    assert token.count(".") == 2

    import os

    os.unlink(tmp_path)


async def test_apple_developer_token_not_configured_returns_400(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/services/apple-music/developer-token without Apple config returns 400 (SVCN-02)."""
    # Default client fixture has apple config as None
    resp = await client.get(
        "/api/services/apple-music/developer-token",
        cookies=auth_cookies,
    )
    assert resp.status_code == 400
    assert "Apple Music not configured" in resp.json()["detail"]


async def test_apple_music_connect_stores_token(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """POST /api/services/apple-music/connect stores encrypted token in DB (SVCN-02)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await _authenticated_post(
        client,
        "/api/services/apple-music/connect",
        json={"music_user_token": "apple-music-user-token-xyz"},
        auth_cookies=auth_cookies,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "Apple Music connected"

    # Verify token stored in DB
    async with test_engine.begin() as conn:
        result = await conn.execute(
            sa.select(service_connections).where(
                sa.and_(
                    service_connections.c.user_id == test_user_id,
                    service_connections.c.service == "apple_music",
                )
            )
        )
        row = result.first()
    assert row is not None
    assert encryption.decrypt(row.access_token_encrypted) == "apple-music-user-token-xyz"
    assert row.service_user_id is None  # Apple Music has no user ID
    assert row.token_expires_at is None  # Apple Music has no expiry


# ── SVCN-03: Disconnect ────────────────────────────────────────────────────────


async def test_disconnect_removes_connection(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """DELETE /api/services/spotify after connect removes the connection row (SVCN-03)."""
    await _insert_test_user(test_engine, test_user_id)

    # Manually insert a Spotify connection
    await upsert_service_connection(
        test_engine,
        encryption,
        user_id=test_user_id,
        service="spotify",
        access_token="some_access_token",
        refresh_token="some_refresh_token",
        expires_in=3600,
        service_user_id="spotify_user_abc",
    )

    resp = await _authenticated_delete(
        client,
        "/api/services/spotify",
        auth_cookies=auth_cookies,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "spotify disconnected"

    # Verify row is gone
    async with test_engine.begin() as conn:
        result = await conn.execute(
            sa.select(service_connections).where(
                sa.and_(
                    service_connections.c.user_id == test_user_id,
                    service_connections.c.service == "spotify",
                )
            )
        )
        row = result.first()
    assert row is None


async def test_disconnect_not_connected_returns_404(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """DELETE /api/services/spotify without prior connection returns 404 (SVCN-03)."""
    resp = await _authenticated_delete(
        client,
        "/api/services/spotify",
        auth_cookies=auth_cookies,
    )
    assert resp.status_code == 404
    assert "Service not connected" in resp.json()["detail"]


async def test_disconnect_invalid_service_returns_400(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """DELETE /api/services/tidal (invalid service) returns 400."""
    resp = await _authenticated_delete(
        client,
        "/api/services/tidal",
        auth_cookies=auth_cookies,
    )
    assert resp.status_code == 400


# ── SVCN-04: List Connections ─────────────────────────────────────────────────


async def test_list_connections_shows_status(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/services returns connected spotify and not_connected apple (SVCN-04)."""
    await _insert_test_user(test_engine, test_user_id)

    # Insert only Spotify connection
    await upsert_service_connection(
        test_engine,
        encryption,
        user_id=test_user_id,
        service="spotify",
        access_token="valid_token",
        refresh_token=None,
        expires_in=3600,
        service_user_id="spotify_user_id",
    )

    resp = await client.get("/api/services", cookies=auth_cookies)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "services" in data
    services_by_name = {s["service"]: s for s in data["services"]}

    assert "spotify" in services_by_name
    assert "apple_music" in services_by_name
    assert services_by_name["spotify"]["status"] == "connected"
    assert services_by_name["apple_music"]["status"] == "not_connected"
    assert services_by_name["apple_music"]["service_user_id"] is None


async def test_list_connections_empty_when_no_connections(
    client: AsyncClient,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/services with no connections returns both services as not_connected (SVCN-04)."""
    resp = await client.get("/api/services", cookies=auth_cookies)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    services_by_name = {s["service"]: s for s in data["services"]}
    assert services_by_name["spotify"]["status"] == "not_connected"
    assert services_by_name["apple_music"]["status"] == "not_connected"


# ── SVCN-05: Expired Token Detection ─────────────────────────────────────────


async def test_list_connections_shows_expired_spotify(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/services with expired token_expires_at shows 'expired' for Spotify (SVCN-05)."""
    await _insert_test_user(test_engine, test_user_id)

    # Insert a Spotify connection with an expired token
    past_expiry = datetime.now(UTC) - timedelta(hours=1)
    async with test_engine.begin() as conn:
        await conn.execute(
            service_connections.insert().values(
                user_id=test_user_id,
                service="spotify",
                access_token_encrypted=encryption.encrypt("old_token"),
                refresh_token_encrypted=None,
                token_expires_at=past_expiry,
                service_user_id="spotify_user_123",
                connected_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )

    resp = await client.get("/api/services", cookies=auth_cookies)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    services_by_name = {s["service"]: s for s in data["services"]}
    assert services_by_name["spotify"]["status"] == "expired"


# ── SVCN-06: Spotify Token Refresh ────────────────────────────────────────────


async def test_spotify_token_refresh_returns_new_token(
) -> None:
    """refresh_spotify_token returns new token data when Spotify responds successfully (SVCN-06)."""
    from unittest.mock import MagicMock

    mock_response_data = {
        "access_token": "new_access_token_xyz",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "user-read-private user-read-email",
    }

    # httpx Response.json() is a plain sync method, use MagicMock for it
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = mock_response_data

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("musicmind.api.services.service.httpx.AsyncClient", return_value=mock_client):
        result = await refresh_spotify_token(
            refresh_token_value="old_refresh_token",
            client_id="test-client-id",
        )

    assert result is not None
    assert result["access_token"] == "new_access_token_xyz"
    assert result["expires_in"] == 3600


async def test_spotify_token_refresh_returns_none_on_failure(
) -> None:
    """refresh_spotify_token returns None when Spotify API rejects the refresh token (SVCN-06)."""
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.status_code = 400

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("musicmind.api.services.service.httpx.AsyncClient", return_value=mock_client):
        result = await refresh_spotify_token(
            refresh_token_value="invalid_refresh_token",
            client_id="test-client-id",
        )

    assert result is None


async def test_spotify_token_refresh_updates_db(
    test_engine: AsyncEngine,
    test_user_id: str,
    encryption: EncryptionService,
) -> None:
    """After refresh, upsert_service_connection updates encrypted tokens in DB (SVCN-06)."""
    await _insert_test_user(test_engine, test_user_id)

    # Insert initial Spotify connection
    await upsert_service_connection(
        test_engine,
        encryption,
        user_id=test_user_id,
        service="spotify",
        access_token="original_access_token",
        refresh_token="original_refresh_token",
        expires_in=3600,
        service_user_id="spotify_user_123",
    )

    # Simulate token refresh: upsert with new tokens
    await upsert_service_connection(
        test_engine,
        encryption,
        user_id=test_user_id,
        service="spotify",
        access_token="refreshed_access_token",
        refresh_token="original_refresh_token",
        expires_in=3600,
        service_user_id="spotify_user_123",
    )

    async with test_engine.begin() as conn:
        result = await conn.execute(
            sa.select(service_connections).where(
                sa.and_(
                    service_connections.c.user_id == test_user_id,
                    service_connections.c.service == "spotify",
                )
            )
        )
        row = result.first()

    assert row is not None
    assert encryption.decrypt(row.access_token_encrypted) == "refreshed_access_token"


# ── Unit Tests ────────────────────────────────────────────────────────────────


def test_pkce_pair_generation() -> None:
    """generate_pkce_pair returns a valid verifier and S256 challenge pair."""
    code_verifier, code_challenge = generate_pkce_pair()

    # Verifier must be 43-128 chars (base64url alphabet)
    assert 43 <= len(code_verifier) <= 128
    # Challenge must be non-empty base64url string (no padding =)
    assert len(code_challenge) > 0
    assert "=" not in code_challenge
    # Challenge must be different from verifier
    assert code_verifier != code_challenge

    # Verify the S256 relationship
    import base64
    import hashlib

    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert code_challenge == expected


async def test_unauthenticated_request_returns_401(
    client: AsyncClient,
) -> None:
    """GET /api/services without auth cookie returns 401."""
    resp = await client.get("/api/services")
    assert resp.status_code == 401
