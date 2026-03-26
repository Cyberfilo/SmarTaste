"""Application settings loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """MusicMind Web application settings.

    All settings are loaded from environment variables with the MUSICMIND_ prefix.
    Example: MUSICMIND_DATABASE_URL sets database_url.
    """

    database_url: str = "postgresql+asyncpg://musicmind:musicmind@localhost:5432/musicmind"
    fernet_key: str  # No default -- MUST be set via MUSICMIND_FERNET_KEY
    debug: bool = False
    log_level: str = "INFO"

    model_config = {"env_prefix": "MUSICMIND_", "env_file": ".env"}
