"""Tests for auth service, schemas, and dependencies."""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest

TEST_SECRET = "test-secret-key-for-unit-tests-only"


# ── Password Hashing ─────────────────────────────────────────────────────────


class TestPasswordHashing:
    """Verify bcrypt password hashing and verification."""

    def test_hash_password_returns_bcrypt_hash(self) -> None:
        """hash_password returns a string starting with $2b$."""
        from musicmind.auth.service import hash_password

        hashed = hash_password("testpass123")
        assert hashed.startswith("$2b$")

    def test_verify_password_correct(self) -> None:
        """verify_password returns True for matching password."""
        from musicmind.auth.service import hash_password, verify_password

        hashed = hash_password("testpass123")
        assert verify_password("testpass123", hashed) is True

    def test_verify_password_wrong(self) -> None:
        """verify_password returns False for wrong password."""
        from musicmind.auth.service import hash_password, verify_password

        hashed = hash_password("testpass123")
        assert verify_password("wrongpass", hashed) is False


# ── JWT Access Token ──────────────────────────────────────────────────────────


class TestAccessToken:
    """Verify JWT access token creation."""

    def test_create_access_token_has_correct_claims(self) -> None:
        """create_access_token returns JWT with sub, email, type=access, exp."""
        from musicmind.auth.service import create_access_token

        token = create_access_token("user-123", "test@example.com", secret_key=TEST_SECRET)
        payload = jwt.decode(token, TEST_SECRET, algorithms=["HS256"])

        assert payload["sub"] == "user-123"
        assert payload["email"] == "test@example.com"
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload
        assert payload["exp"] > payload["iat"]

    def test_create_access_token_custom_delta(self) -> None:
        """create_access_token with custom expires_delta respects the delta."""
        from musicmind.auth.service import create_access_token

        token = create_access_token(
            "user-123",
            "test@example.com",
            secret_key=TEST_SECRET,
            expires_delta=timedelta(minutes=5),
        )
        payload = jwt.decode(token, TEST_SECRET, algorithms=["HS256"])

        # Should expire in ~5 minutes (with small tolerance)
        expected_delta = 5 * 60
        actual_delta = payload["exp"] - payload["iat"]
        assert abs(actual_delta - expected_delta) < 5  # within 5 seconds tolerance


# ── JWT Refresh Token ─────────────────────────────────────────────────────────


class TestRefreshToken:
    """Verify JWT refresh token creation."""

    def test_create_refresh_token_returns_tuple(self) -> None:
        """create_refresh_token returns (token_str, token_id)."""
        from musicmind.auth.service import create_refresh_token

        token_str, token_id = create_refresh_token("user-123", secret_key=TEST_SECRET)
        assert isinstance(token_str, str)
        assert isinstance(token_id, str)

    def test_create_refresh_token_has_correct_claims(self) -> None:
        """create_refresh_token JWT has type=refresh and jti=token_id."""
        from musicmind.auth.service import create_refresh_token

        token_str, token_id = create_refresh_token("user-123", secret_key=TEST_SECRET)
        payload = jwt.decode(token_str, TEST_SECRET, algorithms=["HS256"])

        assert payload["sub"] == "user-123"
        assert payload["type"] == "refresh"
        assert payload["jti"] == token_id
        assert "exp" in payload
        assert "iat" in payload


# ── get_current_user Dependency ───────────────────────────────────────────────


def _make_request(cookies: dict[str, str] | None = None, jwt_secret: str = TEST_SECRET):
    """Build a minimal mock Request with cookies and app.state.settings."""
    request = MagicMock()
    request.cookies = cookies or {}
    settings = SimpleNamespace(jwt_secret_key=jwt_secret)
    request.app.state.settings = settings
    return request


class TestGetCurrentUser:
    """Verify get_current_user FastAPI dependency."""

    @pytest.mark.asyncio
    async def test_valid_access_token(self) -> None:
        """get_current_user with valid access token returns user info."""
        from musicmind.auth.dependencies import get_current_user
        from musicmind.auth.service import create_access_token

        token = create_access_token("user-42", "user@test.com", secret_key=TEST_SECRET)
        request = _make_request(cookies={"access_token": token})

        result = await get_current_user(request)
        assert result == {"user_id": "user-42", "email": "user@test.com"}

    @pytest.mark.asyncio
    async def test_no_cookie_raises_401(self) -> None:
        """get_current_user with no cookie raises HTTPException 401."""
        from fastapi import HTTPException

        from musicmind.auth.dependencies import get_current_user

        request = _make_request(cookies={})

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_raises_401(self) -> None:
        """get_current_user with expired token raises HTTPException 401."""
        from fastapi import HTTPException

        from musicmind.auth.dependencies import get_current_user
        from musicmind.auth.service import create_access_token

        token = create_access_token(
            "user-42",
            "user@test.com",
            secret_key=TEST_SECRET,
            expires_delta=timedelta(seconds=-1),
        )
        # Small sleep to ensure expiry
        time.sleep(0.1)
        request = _make_request(cookies={"access_token": token})

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_token_type_raises_401(self) -> None:
        """get_current_user with refresh token (type != access) raises 401."""
        from fastapi import HTTPException

        from musicmind.auth.dependencies import get_current_user
        from musicmind.auth.service import create_refresh_token

        token_str, _ = create_refresh_token("user-42", secret_key=TEST_SECRET)
        request = _make_request(cookies={"access_token": token_str})

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request)
        assert exc_info.value.status_code == 401


# ── Pydantic Schemas ─────────────────────────────────────────────────────────


class TestSchemas:
    """Verify auth Pydantic request/response models."""

    def test_signup_request_valid(self) -> None:
        """SignupRequest validates email and password with min_length=8."""
        from musicmind.auth.schemas import SignupRequest

        req = SignupRequest(email="test@example.com", password="password123")
        assert req.email == "test@example.com"
        assert req.password == "password123"
        assert req.display_name is None

    def test_signup_request_short_password_rejected(self) -> None:
        """SignupRequest rejects password shorter than 8 characters."""
        from pydantic import ValidationError

        from musicmind.auth.schemas import SignupRequest

        with pytest.raises(ValidationError):
            SignupRequest(email="test@example.com", password="short")

    def test_login_request_valid(self) -> None:
        """LoginRequest accepts email and password."""
        from musicmind.auth.schemas import LoginRequest

        req = LoginRequest(email="test@example.com", password="password123")
        assert req.email == "test@example.com"
        assert req.password == "password123"
