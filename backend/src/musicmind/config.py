"""Application settings loaded from environment variables."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings

# Sandbox mode: when started with --sandbox, use preloaded defaults
SANDBOX_MODE = os.environ.get("MUSICMIND_SANDBOX", "false").lower() == "true"

_SANDBOX_FERNET_KEY = "sAJgv89oolXMmdtREth-fgeK3GwYi5l2qbH7qIsLKJE="
_SANDBOX_JWT_SECRET = "sandbox-jwt-secret-not-for-production"


class Settings(BaseSettings):
    """MusicMind Web application settings.

    All settings are loaded from environment variables with the MUSICMIND_ prefix.
    Example: MUSICMIND_DATABASE_URL sets database_url.

    Sandbox mode (MUSICMIND_SANDBOX=true or --sandbox flag):
    Pre-fills required secrets with development defaults so the app starts
    without manual configuration. Do NOT use sandbox defaults in production.
    """

    database_url: str = "postgresql+asyncpg://musicmind:musicmind@localhost:5432/musicmind"
    fernet_key: str = _SANDBOX_FERNET_KEY if SANDBOX_MODE else ""
    jwt_secret_key: str = _SANDBOX_JWT_SECRET if SANDBOX_MODE else ""
    jwt_algorithm: str = "HS256"
    debug: bool = SANDBOX_MODE
    log_level: str = "DEBUG" if SANDBOX_MODE else "INFO"
    sandbox: bool = SANDBOX_MODE

    # Spotify OAuth
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    # Redirect URI MUST go through the frontend proxy (same domain as session cookie).
    # For tunnel: https://live.menghi.dev/api/services/spotify/callback
    # For local: http://localhost:3000/api/services/spotify/callback
    spotify_redirect_uri: str = "http://localhost:3000/api/services/spotify/callback"

    # Frontend URL for redirects after OAuth callbacks
    frontend_url: str = "http://localhost:3000"

    # Apple Music
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    apple_private_key_path: str | None = None

    model_config = {"env_prefix": "MUSICMIND_", "env_file": ".env"}
