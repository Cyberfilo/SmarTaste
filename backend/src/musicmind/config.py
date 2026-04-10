"""Application settings loaded from environment variables."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings

# Sandbox mode: when started with --sandbox, use preloaded defaults
SANDBOX_MODE = os.environ.get("MUSICMIND_SANDBOX", "false").lower() == "true"

_SANDBOX_FERNET_KEY = "sAJgv89oolXMmdtREth-fgeK3GwYi5l2qbH7qIsLKJE="
_SANDBOX_JWT_SECRET = "sandbox-jwt-secret-not-for-production"


def _default_database_url() -> str:
    """Resolve database URL from environment, supporting Railway's DATABASE_URL."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        # Railway/Heroku provide postgresql:// but asyncpg needs postgresql+asyncpg://
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    return "postgresql+asyncpg://musicmind:musicmind@localhost:5432/musicmind"


class Settings(BaseSettings):
    """SmarTaste application settings.

    All settings are loaded from environment variables with the MUSICMIND_ prefix.
    Example: MUSICMIND_DATABASE_URL sets database_url.

    Also reads DATABASE_URL (Railway/Heroku convention) as fallback for database_url.

    Sandbox mode (MUSICMIND_SANDBOX=true or --sandbox flag):
    Pre-fills required secrets with development defaults so the app starts
    without manual configuration. Do NOT use sandbox defaults in production.
    """

    database_url: str = _default_database_url()
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
    # Production: https://music.menghi.dev/api/services/spotify/callback
    # Local: http://localhost:3000/api/services/spotify/callback
    spotify_redirect_uri: str = "http://localhost:3000/api/services/spotify/callback"

    # Frontend URL for redirects after OAuth callbacks
    frontend_url: str = "http://localhost:3000"

    # Additional CORS origins (comma-separated), merged with default localhost origins
    cors_origins: str = ""

    # Apple Music
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    apple_private_key_path: str | None = None
    # Base64-encoded .p8 key contents (alternative to file path for cloud deploys)
    apple_private_key_b64: str | None = None

    # Logging database (separate PostgreSQL for request/enrichment logs)
    logs_database_url: str | None = None

    # OpenAI API key for AI-generated track captions and recommendation explanations
    openai_api_key: str | None = None

    # Modal GPU worker endpoint URL for Tier 2 enrichment (CLAP + MERT)
    modal_endpoint_url: str | None = None

    # Staging mode: auto-resets DB on startup (clean slate for testing).
    # Set MUSICMIND_STAGING=true on the staging Railway environment.
    staging: bool = False

    model_config = {"env_prefix": "MUSICMIND_", "env_file": ".env"}

    def model_post_init(self, __context: object) -> None:
        """Validate required secrets and fix database URLs after loading from env.

        Railway/Heroku provide postgresql:// but asyncpg needs postgresql+asyncpg://.
        """
        if not self.sandbox and not self.staging:
            if not self.fernet_key:
                raise ValueError(
                    "MUSICMIND_FERNET_KEY is required in production. "
                    "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                )
            if not self.jwt_secret_key:
                raise ValueError(
                    "MUSICMIND_JWT_SECRET_KEY is required in production. "
                    "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
                )
        if self.database_url.startswith("postgresql://"):
            object.__setattr__(
                self,
                "database_url",
                self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1),
            )
        if self.logs_database_url and self.logs_database_url.startswith("postgresql://"):
            object.__setattr__(
                self,
                "logs_database_url",
                self.logs_database_url.replace("postgresql://", "postgresql+asyncpg://", 1),
            )
