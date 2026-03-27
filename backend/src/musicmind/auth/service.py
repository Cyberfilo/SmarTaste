"""Auth service: password hashing, JWT token creation, and cookie helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Response

ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_access_token(
    user_id: str,
    email: str,
    *,
    secret_key: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT access token."""
    now = datetime.now(timezone.utc)
    delta = expires_delta if expires_delta is not None else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + delta,
        "type": "access",
    }
    return jwt.encode(payload, secret_key, algorithm=JWT_ALGORITHM)


def create_refresh_token(
    user_id: str,
    *,
    secret_key: str,
    expires_delta: timedelta | None = None,
) -> tuple[str, str]:
    """Create a refresh token. Returns (jwt_token, token_id) for DB storage."""
    token_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    delta = expires_delta if expires_delta is not None else timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "jti": token_id,
        "iat": now,
        "exp": now + delta,
        "type": "refresh",
    }
    token = jwt.encode(payload, secret_key, algorithm=JWT_ALGORITHM)
    return token, token_id


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    *,
    secure: bool = True,
) -> None:
    """Set access and refresh tokens as httpOnly cookies."""
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/api/auth/refresh",
    )


def clear_auth_cookies(response: Response) -> None:
    """Clear auth cookies on logout."""
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/api/auth/refresh")
