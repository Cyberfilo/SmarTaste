"""Integration tests for auth endpoints."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator

import httpx
import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app (app.py reads Settings at import time)
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.app import app  # noqa: E402
from musicmind.config import Settings  # noqa: E402
from musicmind.db.schema import metadata  # noqa: E402

JWT_SECRET = "test-jwt-secret-key-for-testing-only"


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_csrf_token(client: AsyncClient) -> str:
    """GET /health to obtain a csrftoken cookie value."""
    resp = await client.get("/health")
    # Response cookies only contain new cookies; if csrftoken was already in the
    # client jar the middleware won't re-set it, so fall back to the client jar.
    return resp.cookies.get("csrftoken") or client.cookies.get("csrftoken", "")


async def _csrf_post(
    client: AsyncClient,
    url: str,
    *,
    json: dict | None = None,
    cookies: dict | None = None,
) -> httpx.Response:
    """POST with CSRF token: first GET to obtain csrftoken, then POST with it."""
    csrf_token = await _get_csrf_token(client)
    headers = {"x-csrf-token": csrf_token}

    all_cookies = {"csrftoken": csrf_token}
    if cookies:
        all_cookies.update(cookies)

    return await client.post(url, json=json, headers=headers, cookies=all_cookies)


async def _signup_user(
    client: AsyncClient,
    email: str = "test@example.com",
    password: str = "securepassword123",
    display_name: str | None = None,
) -> httpx.Response:
    """Sign up a test user with CSRF handling."""
    body: dict = {"email": email, "password": password}
    if display_name is not None:
        body["display_name"] = display_name
    return await _csrf_post(client, "/api/auth/signup", json=body)


async def _login_user(
    client: AsyncClient,
    email: str = "test@example.com",
    password: str = "securepassword123",
) -> httpx.Response:
    """Log in a test user with CSRF handling."""
    return await _csrf_post(
        client,
        "/api/auth/login",
        json={"email": email, "password": password},
    )


def _extract_cookies(resp: httpx.Response) -> dict[str, str]:
    """Extract cookie values from a response's Set-Cookie headers."""
    cookies: dict[str, str] = {}
    for header in resp.headers.get_list("set-cookie"):
        parts = header.split(";")[0]
        if "=" in parts:
            name, value = parts.split("=", 1)
            cookies[name.strip()] = value.strip()
    return cookies


async def _csrf_post_with_cookies(
    client: AsyncClient,
    url: str,
    *,
    json: dict | None = None,
    sensitive_cookies: dict[str, str],
) -> httpx.Response:
    """POST with both CSRF token and sensitive cookies (for authenticated requests)."""
    csrf_token = await _get_csrf_token(client)
    headers = {"x-csrf-token": csrf_token}
    all_cookies = {"csrftoken": csrf_token, **sensitive_cookies}
    return await client.post(url, json=json, headers=headers, cookies=all_cookies)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def test_settings() -> Settings:
    """Create Settings with test values for auth testing."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key="dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=",
        jwt_secret_key=JWT_SECRET,
        debug=True,
    )


@pytest.fixture
async def client(
    test_engine: AsyncEngine, test_settings: Settings
) -> AsyncIterator[AsyncClient]:
    """Create an httpx AsyncClient with test overrides for engine and settings."""
    app.state.engine = test_engine
    app.state.settings = test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── ACCT-01: Signup Tests ────────────────────────────────────────────────────


async def test_signup_creates_user(client: AsyncClient) -> None:
    """POST /api/auth/signup with valid email+password returns 201 with user_id and cookies."""
    resp = await _signup_user(client)
    assert resp.status_code == 201
    data = resp.json()
    assert "user_id" in data
    assert data["email"] == "test@example.com"
    assert data["message"] == "Account created"

    cookies = _extract_cookies(resp)
    assert "access_token" in cookies
    assert "refresh_token" in cookies


async def test_signup_duplicate_email(client: AsyncClient) -> None:
    """Signup twice with same email returns 400 with generic error."""
    resp1 = await _signup_user(client)
    assert resp1.status_code == 201

    # Second signup with same email -- no sensitive cookies in this request
    resp2 = await _signup_user(client)
    assert resp2.status_code == 400
    assert resp2.json()["detail"] == "Account creation failed"


async def test_signup_password_validation(client: AsyncClient) -> None:
    """POST /api/auth/signup with 7-char password returns 422."""
    resp = await _csrf_post(
        client,
        "/api/auth/signup",
        json={"email": "test@example.com", "password": "short"},
    )
    assert resp.status_code == 422


async def test_signup_sets_display_name_from_email(client: AsyncClient) -> None:
    """Signup without display_name defaults to email username."""
    resp = await _signup_user(client, email="alice@example.com")
    assert resp.status_code == 201

    access_token = _extract_cookies(resp).get("access_token", "")

    me_resp = await client.get("/api/auth/me", cookies={"access_token": access_token})
    assert me_resp.status_code == 200
    assert me_resp.json()["display_name"] == "alice"


# ── ACCT-02: Login and Token Tests ──────────────────────────────────────────


async def test_login_sets_cookies(client: AsyncClient) -> None:
    """POST /api/auth/login with valid credentials returns 200 with both cookies."""
    await _signup_user(client)
    resp = await _login_user(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Login successful"

    cookies = _extract_cookies(resp)
    assert "access_token" in cookies
    assert "refresh_token" in cookies


async def test_login_wrong_password(client: AsyncClient) -> None:
    """POST /api/auth/login with wrong password returns 401."""
    await _signup_user(client)
    resp = await _login_user(client, password="wrongpassword123")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


async def test_login_nonexistent_email(client: AsyncClient) -> None:
    """POST /api/auth/login with unknown email returns 401 with same generic error."""
    resp = await _login_user(client, email="nobody@example.com")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


async def test_refresh_token_flow(client: AsyncClient) -> None:
    """Login then POST /api/auth/refresh with refresh cookie returns new tokens."""
    await _signup_user(client)
    login_resp = await _login_user(client)
    login_cookies = _extract_cookies(login_resp)

    refresh_resp = await _csrf_post_with_cookies(
        client,
        "/api/auth/refresh",
        sensitive_cookies={
            "access_token": login_cookies["access_token"],
            "refresh_token": login_cookies["refresh_token"],
        },
    )
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["message"] == "Token refreshed"

    new_cookies = _extract_cookies(refresh_resp)
    assert "access_token" in new_cookies


async def test_expired_token_rejected(client: AsyncClient) -> None:
    """GET /api/auth/me with an expired access token returns 401."""
    now = datetime.now(UTC)
    expired_token = jwt.encode(
        {
            "sub": "fake-user-id",
            "email": "test@example.com",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    resp = await client.get("/api/auth/me", cookies={"access_token": expired_token})
    assert resp.status_code == 401


# ── ACCT-03: Logout Tests ───────────────────────────────────────────────────


async def test_logout_clears_session(client: AsyncClient) -> None:
    """POST /api/auth/logout clears cookies."""
    await _signup_user(client)
    login_resp = await _login_user(client)
    login_cookies = _extract_cookies(login_resp)

    logout_resp = await _csrf_post_with_cookies(
        client,
        "/api/auth/logout",
        sensitive_cookies={
            "access_token": login_cookies["access_token"],
            "refresh_token": login_cookies["refresh_token"],
        },
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["message"] == "Logged out"

    # Verify cookies are cleared (set to empty or max-age=0)
    set_cookies_raw = " ".join(logout_resp.headers.get_list("set-cookie"))
    assert "access_token" in set_cookies_raw
    assert "refresh_token" in set_cookies_raw


async def test_revoked_refresh_rejected(client: AsyncClient) -> None:
    """After logout, using the old refresh token returns 401."""
    await _signup_user(client)
    login_resp = await _login_user(client)
    login_cookies = _extract_cookies(login_resp)

    # Logout to revoke the refresh token
    await _csrf_post_with_cookies(
        client,
        "/api/auth/logout",
        sensitive_cookies={
            "access_token": login_cookies["access_token"],
            "refresh_token": login_cookies["refresh_token"],
        },
    )

    # Try to use the revoked refresh token
    refresh_resp = await _csrf_post_with_cookies(
        client,
        "/api/auth/refresh",
        sensitive_cookies={
            "access_token": login_cookies["access_token"],
            "refresh_token": login_cookies["refresh_token"],
        },
    )
    assert refresh_resp.status_code == 401


# ── ACCT-04: Security Property Tests ────────────────────────────────────────


async def test_cookie_security_flags(client: AsyncClient) -> None:
    """After login, verify access_token cookie is httpOnly."""
    await _signup_user(client)
    login_resp = await _login_user(client)

    set_cookies = login_resp.headers.get_list("set-cookie")
    access_cookie_headers = [c for c in set_cookies if c.startswith("access_token=")]
    assert len(access_cookie_headers) > 0
    access_header = access_cookie_headers[0].lower()
    assert "httponly" in access_header
    assert "samesite=lax" in access_header


async def test_csrf_protection(client: AsyncClient) -> None:
    """POST with sensitive cookies works without CSRF token.

    CSRF middleware is disabled — SameSite=lax cookies behind the
    same-origin Next.js proxy provide equivalent protection.
    """
    signup_resp = await _signup_user(client)
    login_cookies = _extract_cookies(signup_resp)

    # POST without CSRF token should succeed (CSRF middleware disabled)
    logout_resp = await client.post(
        "/api/auth/logout",
        cookies={
            "access_token": login_cookies["access_token"],
            "refresh_token": login_cookies["refresh_token"],
        },
    )
    assert logout_resp.status_code == 200


async def test_csrf_with_valid_token(client: AsyncClient) -> None:
    """POST with valid CSRF token and sensitive cookies succeeds."""
    await _signup_user(client)
    login_resp = await _login_user(client)
    login_cookies = _extract_cookies(login_resp)

    # POST with valid CSRF token should succeed
    logout_resp = await _csrf_post_with_cookies(
        client,
        "/api/auth/logout",
        sensitive_cookies={
            "access_token": login_cookies["access_token"],
            "refresh_token": login_cookies["refresh_token"],
        },
    )
    assert logout_resp.status_code == 200


# ── Protected Endpoint Tests ─────────────────────────────────────────────────


async def test_me_returns_user_info(client: AsyncClient) -> None:
    """Login, GET /api/auth/me with access_token cookie returns user info."""
    signup_resp = await _signup_user(client)
    signup_data = signup_resp.json()
    access_token = _extract_cookies(signup_resp).get("access_token", "")

    me_resp = await client.get("/api/auth/me", cookies={"access_token": access_token})
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["user_id"] == signup_data["user_id"]
    assert me_data["email"] == "test@example.com"
    assert "display_name" in me_data


async def test_me_unauthenticated(client: AsyncClient) -> None:
    """GET /api/auth/me without cookie returns 401."""
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
