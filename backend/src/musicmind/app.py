"""SmarTaste FastAPI application factory with async lifespan managing engine and encryption."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
from fastapi import FastAPI

# Uvicorn configures its own loggers but leaves `musicmind.*` at the Python
# default WARNING — which silently swallows orchestration + worker logs on
# Railway. Force INFO on the root musicmind namespace so those lines show up.
_mm_logger = logging.getLogger("musicmind")
_mm_logger.setLevel(logging.INFO)
if not _mm_logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    _mm_logger.addHandler(_h)
_mm_logger.propagate = False
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from musicmind import __version__
from musicmind.api.middleware import RequestLoggingMiddleware
from musicmind.api.rate_limit import limiter
from musicmind.api.router import api_router
from musicmind.config import Settings
from musicmind.db.engine import create_engine
from musicmind.security.encryption import EncryptionService

_settings = Settings()


async def _dispatch_startup_work(engine, settings) -> None:
    """Inspect DB state on boot and dispatch worker tasks as background work.

    Backend is the coordinator; worker is the executor. This runs during
    the FastAPI lifespan AFTER the app is ready to serve requests — it
    uses asyncio.create_task so boot never blocks on enrichment.

    Order of operations:
    1. Library enrichment gaps → fire worker's _fill_library_gaps
    2. ISRC backfill gaps → fire _backfill_isrcs
    3. GPU (CLAP/MERT) gaps → fire _backfill_gpu_embeddings + _global
    4. Cobweb + discovery → fire _run_cobweb_cycle
    """
    import asyncio
    import logging

    log = logging.getLogger("musicmind.startup")

    # Count each kind of gap so we log a useful summary.
    async with engine.begin() as conn:
        try:
            lib_gaps = (await conn.execute(sa.text("""
                SELECT count(*) FROM song_metadata_cache s
                WHERE (s.library_id IS NOT NULL OR s.date_added_to_library IS NOT NULL)
                  AND NOT EXISTS (
                    SELECT 1 FROM audio_features_cache af
                    WHERE af.catalog_id = s.catalog_id AND af.user_id = s.user_id
                      AND af.energy IS NOT NULL
                  )
            """))).scalar() or 0
        except Exception:
            lib_gaps = -1

        try:
            isrc_gaps = (await conn.execute(sa.text("""
                SELECT count(*) FROM song_metadata_cache
                WHERE (isrc IS NULL OR isrc = '') AND isrc IS DISTINCT FROM '__NO_ISRC__'
            """))).scalar() or 0
        except Exception:
            isrc_gaps = -1

        try:
            gpu_gaps = (await conn.execute(sa.text("""
                SELECT count(*) FROM audio_embeddings_global
                WHERE clap_embedding IS NULL AND embedding IS NOT NULL
            """))).scalar() or 0
        except Exception:
            gpu_gaps = -1

    # Build a natural-language plan and print what the backend is asking
    # the worker to do. Railway log viewer shows these as readable narration.
    plan: list[str] = []
    if lib_gaps > 0:
        plan.append(f"enrich {lib_gaps} library songs missing audio features")
    if isrc_gaps > 0:
        plan.append(f"look up {isrc_gaps} missing ISRCs via Deezer + MusicBrainz")
    if gpu_gaps > 0:
        plan.append(f"fill {gpu_gaps} global CLAP/MERT embeddings on the GPU")
    plan.append("run a cobweb + discovery cycle to grow the candidate pool")

    log.info("── Backend booted. Plan for the worker: ──")
    for i, step in enumerate(plan, 1):
        log.info("  %d. %s", i, step)

    # V 6.370: backend no longer dispatches enrichment work. It inspects
    # the DB and logs the natural-language plan above; the worker is the
    # single executor. Removes the backend/worker race where both
    # processes ran the same backfill functions in parallel on boot,
    # doubling API quota / GPU spend and occasionally stepping on each
    # other's Postgres transactions.
    #
    # The worker reads the DB itself on startup and runs two strict
    # drains (USER-LINKED → DISCOVERED) before entering the main cycle.
    # We persist the plan to worker_status.detail as a JSON blob so the
    # admin dashboard can show expected work even before the worker has
    # updated its own phase on the same row.
    try:
        import json as _json

        plan_payload = {
            "lib_gaps": lib_gaps,
            "isrc_gaps": isrc_gaps,
            "gpu_gaps": gpu_gaps,
            "plan": plan,
        }
        async with engine.begin() as conn:
            await conn.execute(sa.text("""
                INSERT INTO worker_status (id, phase, detail, updated_at)
                VALUES (1, 'startup_plan_ready', :detail, now())
                ON CONFLICT (id) DO UPDATE SET
                  phase = 'startup_plan_ready',
                  detail = EXCLUDED.detail,
                  updated_at = now()
            """), {"detail": _json.dumps(plan_payload)[:1000]})
    except Exception:
        log.debug("Could not write startup plan to worker_status", exc_info=True)

    log.info(
        "── Worker will execute the plan (USER-LINKED drain → "
        "DISCOVERED drain → main cycle). Backend is inspector-only. ──"
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and tear down application resources."""
    engine = create_engine(_settings.database_url, echo=_settings.debug)
    encryption = EncryptionService(_settings.fernet_key)

    app.state.engine = engine
    app.state.settings = _settings
    app.state.encryption = encryption

    # Staging mode: auto-reset DB on startup for clean testing
    # Safety: require BOTH staging flag AND explicit confirmation env var
    if _settings.staging and os.environ.get("MUSICMIND_CONFIRM_RESET", "") == "yes":
        import logging as _log
        _log.getLogger(__name__).warning("STAGING MODE: resetting database...")
        try:
            async with engine.begin() as conn:
                # Read and execute the reset SQL
                from pathlib import Path
                sql_path = Path(__file__).resolve().parents[3] / "scripts" / "reset-staging-db.sql"
                if not sql_path.exists():
                    sql_path = Path("/app/scripts/reset-staging-db.sql")
                if sql_path.exists():
                    sql = sql_path.read_text()
                    for stmt in sql.split(";"):
                        stmt = stmt.strip()
                        if stmt and not stmt.startswith("--"):
                            await conn.execute(sa.text(stmt))
                    _log.getLogger(__name__).warning("STAGING: database reset complete")
                else:
                    _log.getLogger(__name__).warning("STAGING: reset SQL not found at %s", sql_path)
        except Exception:
            _log.getLogger(__name__).exception("STAGING: database reset failed")

    # Install admin log handler for live log streaming
    from musicmind.api.admin.log_stream import install_admin_handler
    install_admin_handler()

    # Initialize logging database if configured
    log_writer = None
    logs_engine = None
    if _settings.logs_database_url:
        try:
            from musicmind.db.logs import (
                DatabaseLogHandler,
                LogWriter,
                create_logs_engine,
                init_logs_schema,
                reset_logs_tables,
            )

            logs_engine = create_logs_engine(_settings.logs_database_url)
            await init_logs_schema(logs_engine)
            # V 6.392: clear prior deploy's logs — keep only current deploy.
            await reset_logs_tables(logs_engine)
            log_writer = LogWriter(logs_engine)
            log_writer.start()
            app.state.log_writer = log_writer
            app.state.logs_engine = logs_engine

            # Forward all Python logs (WARNING+ global, INFO+ musicmind) to DB
            import logging as _logging
            db_handler = DatabaseLogHandler(log_writer)
            db_handler.setFormatter(_logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            _logging.getLogger().addHandler(db_handler)
            _logging.getLogger(__name__).info(
                "Logging database connected (all logs forwarded)"
            )
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Failed to connect logging database, continuing without", exc_info=True,
            )

    # ── Backend orchestration: verify state + trigger worker if gaps exist ──
    # Backend is the coordinator; worker is the executor. On boot, we inspect
    # what needs to happen and dispatch worker functions as background tasks.
    # Worker process continues polling in parallel — both share the DB, so
    # duplicate work is serialized by Postgres. Fire-and-forget: boot should
    # not block on enrichment.
    try:
        await _dispatch_startup_work(engine, _settings)
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).exception(
            "Backend startup orchestration failed (non-fatal)"
        )

    yield

    if log_writer:
        await log_writer.stop()
    if logs_engine:
        await logs_engine.dispose()
    await engine.dispose()


app = FastAPI(title="SmarTaste", version=__version__, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(api_router)

# Request logging — every request logged with timing, status, user_id
app.add_middleware(RequestLoggingMiddleware)

# CORS — allow frontend origins to make credentialed requests
# Default localhost origins + any extra from MUSICMIND_CORS_ORIGINS env var
_cors_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://music.menghi.dev",
]
if _settings.cors_origins:
    _cors_origins.extend(
        origin.strip() for origin in _settings.cors_origins.split(",") if origin.strip()
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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
