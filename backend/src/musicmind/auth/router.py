"""Auth API endpoints: signup, login, logout, refresh, me."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from musicmind.auth.dependencies import get_current_user
from musicmind.auth.schemas import LoginRequest, SignupRequest
from musicmind.auth.service import (
    clear_auth_cookies,
    create_access_token,
    create_refresh_token,
    hash_password,
    set_auth_cookies,
    verify_password,
)
from musicmind.db.schema import refresh_tokens, users

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(request: Request, response: Response, body: SignupRequest) -> dict:
    """Register a new user account."""
    engine = request.app.state.engine
    settings = request.app.state.settings

    password_hash = hash_password(body.password)
    user_id = str(uuid.uuid7())
    display_name = body.display_name or body.email.split("@")[0]

    access_token = create_access_token(
        user_id, body.email, secret_key=settings.jwt_secret_key
    )
    refresh_token, token_id = create_refresh_token(
        user_id, secret_key=settings.jwt_secret_key
    )

    async with engine.begin() as conn:
        # Check for duplicate email, create user, and store refresh token atomically
        result = await conn.execute(
            sa.select(users.c.id).where(users.c.email == body.email)
        )
        if result.first() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Account creation failed",
            )

        await conn.execute(
            users.insert().values(
                id=user_id,
                email=body.email,
                password_hash=password_hash,
                display_name=display_name,
            )
        )
        await conn.execute(
            refresh_tokens.insert().values(
                id=token_id,
                user_id=user_id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )

    set_auth_cookies(response, access_token, refresh_token, secure=not settings.debug)
    logger.info("User signed up: %s", user_id)

    return {"user_id": user_id, "email": body.email, "message": "Account created"}


@router.post("/login")
async def login(request: Request, response: Response, body: LoginRequest) -> dict:
    """Authenticate with email and password."""
    engine = request.app.state.engine
    settings = request.app.state.settings

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                users.c.id,
                users.c.email,
                users.c.password_hash,
                users.c.display_name,
            ).where(users.c.email == body.email)
        )
        user = result.first()

        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

        access_token = create_access_token(
            user.id, user.email, secret_key=settings.jwt_secret_key
        )
        refresh_token, token_id = create_refresh_token(
            user.id, secret_key=settings.jwt_secret_key
        )

        await conn.execute(
            refresh_tokens.insert().values(
                id=token_id,
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )

    set_auth_cookies(response, access_token, refresh_token, secure=not settings.debug)
    logger.info("User logged in: %s", user.id)

    return {"user_id": user.id, "email": user.email, "message": "Login successful"}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    """Log out: clear cookies and revoke refresh token."""
    engine = request.app.state.engine
    settings = request.app.state.settings
    token = request.cookies.get("refresh_token")

    if token:
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=["HS256"],
                options={"verify_exp": False},
            )
            jti = payload.get("jti")
            if jti:
                async with engine.begin() as conn:
                    await conn.execute(
                        refresh_tokens.update()
                        .where(refresh_tokens.c.id == jti)
                        .values(revoked=True)
                    )
                logger.info("Refresh token revoked: %s", jti)
        except jwt.InvalidTokenError:
            logger.warning("Invalid refresh token during logout, clearing cookies anyway")

    clear_auth_cookies(response)
    return {"message": "Logged out"}


@router.post("/refresh")
async def refresh(request: Request, response: Response) -> dict:
    """Rotate refresh token and issue a new access token."""
    engine = request.app.state.engine
    settings = request.app.state.settings
    token = request.cookies.get("refresh_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token",
        )

    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=["HS256"]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    jti = payload.get("jti")
    user_id = payload["sub"]

    # Single atomic transaction: validate old token, revoke it, fetch user, insert new token
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(refresh_tokens.c.id, refresh_tokens.c.revoked).where(
                refresh_tokens.c.id == jti
            )
        )
        db_token = result.first()

        if db_token is None or db_token.revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        # Revoke old token
        await conn.execute(
            refresh_tokens.update()
            .where(refresh_tokens.c.id == jti)
            .values(revoked=True)
        )

        # Fetch email from DB for new access token
        result = await conn.execute(
            sa.select(users.c.email).where(users.c.id == user_id)
        )
        user = result.first()

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        new_access_token = create_access_token(
            user_id, user.email, secret_key=settings.jwt_secret_key
        )
        new_refresh_token, new_token_id = create_refresh_token(
            user_id, secret_key=settings.jwt_secret_key
        )

        await conn.execute(
            refresh_tokens.insert().values(
                id=new_token_id,
                user_id=user_id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )

    set_auth_cookies(
        response, new_access_token, new_refresh_token, secure=not settings.debug
    )
    logger.info("Token refreshed for user: %s", user_id)

    return {"user_id": user_id, "message": "Token refreshed"}


@router.get("/me")
async def me(
    request: Request, current_user: dict = Depends(get_current_user)
) -> dict:
    """Return the authenticated user's profile."""
    engine = request.app.state.engine

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                users.c.id, users.c.email, users.c.display_name
            ).where(users.c.id == current_user["user_id"])
        )
        user = result.first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return {
        "user_id": user.id,
        "email": user.email,
        "display_name": user.display_name,
    }
