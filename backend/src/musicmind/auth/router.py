"""Auth API endpoints: signup, login, logout, refresh, me."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import sqlalchemy as sa
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status

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
from musicmind.api.rate_limit import AUTH_LIMIT, limiter
from musicmind.db.schema import refresh_tokens, users

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_LIMIT)
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
@limiter.limit(AUTH_LIMIT)
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
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the authenticated user's profile.

    Also triggers background library sync: checks for un-enriched songs
    and queues audio feature extraction if needed.
    """
    engine = request.app.state.engine

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                users.c.id, users.c.email, users.c.display_name, users.c.is_admin
            ).where(users.c.id == current_user["user_id"])
        )
        user = result.first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Queue background library sync + enrichment (non-blocking)
    settings = getattr(request.app.state, "settings", None)
    encryption = getattr(request.app.state, "encryption", None)
    if settings and encryption:
        background_tasks.add_task(
            _background_sync_library,
            engine=engine,
            settings=settings,
            encryption=encryption,
            user_id=user.id,
        )

    return {
        "user_id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": bool(user.is_admin),
    }


async def _background_sync_library(
    *,
    engine,
    settings,
    encryption,
    user_id: str,
) -> None:
    """Check for un-enriched songs and run enrichment if needed.

    Called as a background task on every /me request.
    Lightweight: checks DB first, only hits external APIs if gaps exist.
    """
    try:
        from musicmind.api.services.service import get_user_connections
        from musicmind.db.schema import audio_features_cache, song_metadata_cache

        # Check if user has connected services
        connections = await get_user_connections(engine, user_id=user_id)
        if not connections:
            return

        # Count songs vs enriched songs
        async with engine.begin() as conn:
            song_count_result = await conn.execute(
                sa.select(sa.func.count()).select_from(song_metadata_cache).where(
                    song_metadata_cache.c.user_id == user_id
                )
            )
            total_songs = song_count_result.scalar() or 0

            enriched_count_result = await conn.execute(
                sa.select(sa.func.count()).select_from(audio_features_cache).where(
                    audio_features_cache.c.user_id == user_id
                )
            )
            enriched_songs = enriched_count_result.scalar() or 0

        if total_songs == 0:
            # No songs cached yet — trigger a profile build which fetches + enriches
            from musicmind.api.taste.service import TasteService

            taste_svc = TasteService()
            try:
                await taste_svc.get_profile(
                    engine, encryption, settings,
                    user_id=user_id, force_refresh=True,
                )
            except Exception:
                logger.debug("Background profile build failed for %s", user_id)
            return

        # If >10% of songs lack enrichment, run enrichment on un-enriched ones
        gap = total_songs - enriched_songs
        if gap <= 0:
            return

        logger.info(
            "Background sync: %d/%d songs need enrichment for user %s",
            gap, total_songs, user_id,
        )

        # Fetch un-enriched songs from cache
        async with engine.begin() as conn:
            # Get songs that don't have audio features yet
            enriched_ids_q = sa.select(audio_features_cache.c.catalog_id).where(
                audio_features_cache.c.user_id == user_id
            )
            result = await conn.execute(
                sa.select(song_metadata_cache).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == user_id,
                        song_metadata_cache.c.catalog_id.notin_(enriched_ids_q),
                    )
                ).limit(10)  # Small batch per /me call (runs every page load)
            )
            rows = result.fetchall()

        if not rows:
            return

        import json as _json

        tracks = []
        for row in rows:
            genres = row.genre_names
            if isinstance(genres, str):
                try:
                    genres = _json.loads(genres)
                except (ValueError, TypeError):
                    genres = []
            tracks.append({
                "catalog_id": row.catalog_id,
                "name": row.name or "",
                "artist_name": row.artist_name or "",
                "isrc": row.isrc or "",
                "service_source": getattr(row, "service_source", ""),
                "genre_names": genres,
            })

        from musicmind.engine.enrichment.orchestrator import enrich_tracks

        await enrich_tracks(
            engine, tracks, user_id=user_id,
            soundstat_api_key=settings.soundstat_api_key,
        )

    except Exception:
        logger.debug("Background library sync failed for user %s", user_id)
