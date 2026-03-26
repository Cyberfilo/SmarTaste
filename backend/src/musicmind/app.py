"""FastAPI application factory with async lifespan managing engine and encryption."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from musicmind.api.router import api_router
from musicmind.config import Settings
from musicmind.db.engine import create_engine
from musicmind.security.encryption import EncryptionService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and tear down application resources."""
    settings = Settings()
    engine = create_engine(settings.database_url, echo=settings.debug)
    encryption = EncryptionService(settings.fernet_key)

    app.state.engine = engine
    app.state.settings = settings
    app.state.encryption = encryption

    yield

    await engine.dispose()


app = FastAPI(title="MusicMind", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("musicmind.app:app", host="0.0.0.0", port=8000, reload=True)
