"""FastAPI application factory with async lifespan managing engine and encryption."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette_csrf import CSRFMiddleware

from musicmind.api.router import api_router
from musicmind.config import Settings
from musicmind.db.engine import create_engine
from musicmind.security.encryption import EncryptionService

_settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and tear down application resources."""
    engine = create_engine(_settings.database_url, echo=_settings.debug)
    encryption = EncryptionService(_settings.fernet_key)

    app.state.engine = engine
    app.state.settings = _settings
    app.state.encryption = encryption

    yield

    await engine.dispose()


app = FastAPI(title="MusicMind", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
app.add_middleware(
    CSRFMiddleware,
    secret=_settings.jwt_secret_key,
    sensitive_cookies={"access_token", "refresh_token"},
    cookie_name="csrftoken",
    header_name="x-csrf-token",
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("musicmind.app:app", host="0.0.0.0", port=8000, reload=True)
