"""Service connection helpers for Spotify and Apple Music OAuth flows.

Provides PKCE generation, Spotify token exchange/refresh, Apple Developer Token
generation, Apple Music health check, and database operations for service connections.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt as pyjwt
import sqlalchemy as sa

from musicmind.db.schema import service_connections
from musicmind.security.encryption import EncryptionService

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"
SPOTIFY_SCOPES = (
    "user-read-private user-read-email user-library-read "
    "user-read-recently-played user-top-read"
)
APPLE_MUSIC_STOREFRONT_URL = "https://api.music.apple.com/v1/me/storefront"
APPLE_TOKEN_EXPIRY_SECONDS = 15_777_000  # ~6 months


# ── Pure Functions ───────────────────────────────────────────────────────────


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge pair.

    Returns:
        Tuple of (code_verifier, code_challenge) for Spotify OAuth PKCE flow.
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_spotify_authorize_url(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
) -> str:
    """Build the Spotify OAuth authorize URL with PKCE parameters.

    Args:
        client_id: Spotify application client ID.
        redirect_uri: Registered redirect URI.
        code_challenge: S256-hashed PKCE code challenge.
        state: Random state parameter for CSRF protection.

    Returns:
        Full Spotify authorize URL ready for user redirect.
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": SPOTIFY_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    return f"{SPOTIFY_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def generate_apple_developer_token(
    team_id: str,
    key_id: str,
    private_key_path: str,
) -> str:
    """Generate an ES256-signed Apple Developer Token for MusicKit.

    Ported from src/musicmind/auth.py _generate_developer_token().

    Args:
        team_id: Apple Developer Team ID.
        key_id: MusicKit private key ID.
        private_key_path: Path to the .p8 private key file.

    Returns:
        Signed JWT developer token valid for ~6 months.
    """
    private_key = Path(private_key_path).expanduser().read_text().strip()
    now = int(time.time())
    payload = {
        "iss": team_id,
        "iat": now,
        "exp": now + APPLE_TOKEN_EXPIRY_SECONDS,
    }
    headers = {
        "alg": "ES256",
        "kid": key_id,
    }
    return pyjwt.encode(payload, private_key, algorithm="ES256", headers=headers)


# ── Async Spotify HTTP Operations ────────────────────────────────────────────


async def exchange_spotify_code(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
) -> dict:
    """Exchange a Spotify authorization code for access and refresh tokens.

    Uses PKCE flow -- no client_secret is sent.

    Args:
        code: Authorization code from Spotify callback.
        code_verifier: Original PKCE code verifier.
        redirect_uri: Same redirect URI used in authorize request.
        client_id: Spotify application client ID.

    Returns:
        Token response dict with access_token, refresh_token, expires_in, scope.
    """
    form_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    logger.info("Exchanging Spotify authorization code for tokens")
    async with httpx.AsyncClient() as client:
        resp = await client.post(SPOTIFY_TOKEN_URL, data=form_data)
        resp.raise_for_status()
        return resp.json()


async def refresh_spotify_token(
    refresh_token_value: str,
    client_id: str,
) -> dict | None:
    """Refresh a Spotify access token using a refresh token.

    Uses PKCE flow -- no client_secret is sent.

    Args:
        refresh_token_value: Current refresh token.
        client_id: Spotify application client ID.

    Returns:
        New token response dict on success, or None if refresh token is invalid.
    """
    form_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value,
        "client_id": client_id,
    }
    logger.info("Refreshing Spotify access token")
    async with httpx.AsyncClient() as client:
        resp = await client.post(SPOTIFY_TOKEN_URL, data=form_data)
        if resp.is_success:
            return resp.json()
        logger.warning("Spotify token refresh failed with status %d", resp.status_code)
        return None


async def fetch_spotify_user_profile(access_token: str) -> dict:
    """Fetch the current Spotify user's profile.

    Args:
        access_token: Valid Spotify access token.

    Returns:
        User profile dict with id, email, display_name fields.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            SPOTIFY_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def check_apple_music_token(
    music_user_token: str,
    developer_token: str,
) -> bool:
    """Check whether an Apple Music User Token is still valid.

    Makes a lightweight request to the storefront endpoint. Returns False on
    both 401 and 403 (both indicate an expired or invalid token).

    Args:
        music_user_token: Apple Music User Token to validate.
        developer_token: Apple Developer Token for API access.

    Returns:
        True if the token is valid, False otherwise.
    """
    logger.info("Checking Apple Music token health")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                APPLE_MUSIC_STOREFRONT_URL,
                headers={
                    "Authorization": f"Bearer {developer_token}",
                    "Music-User-Token": music_user_token,
                },
            )
            if resp.status_code == 200:
                return True
            if resp.status_code in (401, 403):
                logger.warning("Apple Music token expired or invalid (status %d)", resp.status_code)
                return False
            logger.warning("Unexpected Apple Music API status %d", resp.status_code)
            return False
    except httpx.HTTPError:
        logger.exception("Apple Music health check failed with connection error")
        return False


# ── Async DB Operations ──────────────────────────────────────────────────────


async def upsert_service_connection(
    engine,
    encryption: EncryptionService,
    *,
    user_id: str,
    service: str,
    access_token: str,
    refresh_token: str | None,
    expires_in: int | None,
    service_user_id: str | None,
) -> None:
    """Insert or update a service connection for a user.

    Uses dialect-agnostic SELECT-then-INSERT/UPDATE within a transaction,
    compatible with both PostgreSQL and SQLite.

    Args:
        engine: SQLAlchemy async engine.
        encryption: EncryptionService for token encryption.
        user_id: MusicMind user ID.
        service: Service name (spotify or apple_music).
        access_token: Plaintext access token to encrypt and store.
        refresh_token: Plaintext refresh token to encrypt (or None).
        expires_in: Token lifetime in seconds (or None for non-expiring).
        service_user_id: User's ID on the external service.
    """
    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(service_connections.c.id).where(
                sa.and_(
                    service_connections.c.user_id == user_id,
                    service_connections.c.service == service,
                )
            )
        )
        row = existing.first()

        encrypted_access = encryption.encrypt(access_token)
        encrypted_refresh = encryption.encrypt(refresh_token) if refresh_token else None
        token_expires_at = (
            datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None
        )
        now = datetime.now(UTC)

        if row:
            logger.info("Updating existing %s connection for user %s", service, user_id)
            await conn.execute(
                service_connections.update()
                .where(service_connections.c.id == row.id)
                .values(
                    access_token_encrypted=encrypted_access,
                    refresh_token_encrypted=encrypted_refresh,
                    token_expires_at=token_expires_at,
                    service_user_id=service_user_id,
                    connected_at=now,
                )
            )
        else:
            logger.info("Creating new %s connection for user %s", service, user_id)
            await conn.execute(
                service_connections.insert().values(
                    user_id=user_id,
                    service=service,
                    access_token_encrypted=encrypted_access,
                    refresh_token_encrypted=encrypted_refresh,
                    token_expires_at=token_expires_at,
                    service_user_id=service_user_id,
                    connected_at=now,
                )
            )


async def delete_service_connection(
    engine,
    *,
    user_id: str,
    service: str,
) -> bool:
    """Delete a service connection for a user.

    Spotify has no revocation API, so we simply remove the stored tokens.

    Args:
        engine: SQLAlchemy async engine.
        user_id: MusicMind user ID.
        service: Service name to disconnect.

    Returns:
        True if a connection was deleted, False if no matching connection found.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            service_connections.delete().where(
                sa.and_(
                    service_connections.c.user_id == user_id,
                    service_connections.c.service == service,
                )
            )
        )
        deleted = result.rowcount > 0
        if deleted:
            logger.info("Deleted %s connection for user %s", service, user_id)
        else:
            logger.info("No %s connection found for user %s", service, user_id)
        return deleted


async def get_user_connections(
    engine,
    *,
    user_id: str,
) -> list[dict]:
    """Get all service connections for a user.

    Returns raw connection data; the router derives status from token_expires_at.

    Args:
        engine: SQLAlchemy async engine.
        user_id: MusicMind user ID.

    Returns:
        List of connection dicts with service, token, and metadata fields.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                service_connections.c.service,
                service_connections.c.access_token_encrypted,
                service_connections.c.refresh_token_encrypted,
                service_connections.c.token_expires_at,
                service_connections.c.service_user_id,
                service_connections.c.connected_at,
            ).where(service_connections.c.user_id == user_id)
        )
        rows = result.fetchall()
        return [
            {
                "service": row.service,
                "access_token_encrypted": row.access_token_encrypted,
                "refresh_token_encrypted": row.refresh_token_encrypted,
                "token_expires_at": row.token_expires_at,
                "service_user_id": row.service_user_id,
                "connected_at": row.connected_at,
            }
            for row in rows
        ]
