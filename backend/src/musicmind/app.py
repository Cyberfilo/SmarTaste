"""FastAPI application factory with async lifespan managing engine and encryption."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

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

# CORS — allow frontend origins to make credentialed requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://live.menghi.dev",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["set-cookie"],
)
# CSRF protection: SameSite=lax on all auth cookies provides equivalent
# protection to double-submit tokens when requests go through the same-origin
# Next.js proxy. The separate CSRFMiddleware is disabled because the proxy
# prevents the CSRF cookie from reaching the browser (Set-Cookie from backend
# is consumed by the proxy, not forwarded). JWT + SameSite=lax is sufficient.
#
# If deploying without the proxy (backend directly exposed), re-enable:
# app.add_middleware(CSRFMiddleware, secret=..., sensitive_cookies=...)
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.jwt_secret_key,
    same_site="lax",
    https_only=not _settings.debug,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("musicmind.app:app", host="0.0.0.0", port=8000, reload=True)
