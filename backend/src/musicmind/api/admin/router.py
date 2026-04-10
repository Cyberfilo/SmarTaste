"""Admin API endpoints + static dashboard — enrichment progress, system status.

Protected by ADMIN_SECRET header or is_admin user flag.
Dashboard served at /admin as static HTML.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import HTMLResponse, StreamingResponse

from musicmind.api.admin.log_stream import get_recent_logs, subscribe, unsubscribe
from musicmind.api.admin.progress import (
    cleanup_orphaned_features,
    get_enrichment_breakdown,
    get_enrichment_progress,
)
from musicmind.db.schema import users

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Static dashboard served at /admin (outside /api/admin prefix)
_DASHBOARD_HTML = (Path(__file__).parent / "dashboard.html").read_text()
dashboard_router = APIRouter(tags=["admin"], include_in_schema=False)


@dashboard_router.get("/admin")
async def admin_dashboard() -> HTMLResponse:
    """Serve the static admin dashboard."""
    return HTMLResponse(_DASHBOARD_HTML)


ADMIN_SECRET = os.environ.get("MUSICMIND_ADMIN_SECRET") or None


async def require_admin(request: Request) -> None:
    """Check admin access via shared secret header or is_admin user flag."""
    # Option 1: X-Admin-Secret header (used by admin dashboard service)
    secret = request.headers.get("x-admin-secret", "")
    if ADMIN_SECRET and secret == ADMIN_SECRET:
        return

    # Option 2: Authenticated user with is_admin flag
    try:
        from musicmind.auth.dependencies import get_current_user

        current_user = await get_current_user(request)
        engine = request.app.state.engine
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(users.c.is_admin).where(
                    users.c.id == current_user["user_id"]
                )
            )
            row = result.first()
        if row and row.is_admin:
            return
    except Exception:
        pass

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access required",
    )


@router.get("/logs")
async def get_logs(
    limit: int = 100,
    _admin: dict = Depends(require_admin),
) -> list[dict]:
    """Get recent log entries (last N from buffer)."""
    return get_recent_logs(limit)


@router.get("/logs/stream")
async def stream_logs(
    request: Request,
    _admin: dict = Depends(require_admin),
) -> StreamingResponse:
    """Live log stream via Server-Sent Events.

    Sends recent logs first, then streams new entries in real-time.
    """

    async def event_generator():
        queue = subscribe()
        try:
            # Send recent logs first
            for entry in get_recent_logs(50):
                yield f"data: {json.dumps(entry)}\n\n"

            # Stream new logs
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(entry)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/progress")
async def get_progress(
    request: Request,
    _admin: dict = Depends(require_admin),
) -> dict:
    """Get enrichment progress for all users."""
    progress = await get_enrichment_progress(request.app.state.engine)
    return {"users": progress}


@router.get("/status")
async def get_system_status(
    request: Request,
    _admin: dict = Depends(require_admin),
) -> dict:
    """Get system health + enrichment summary."""
    from musicmind import __version__
    from musicmind.db.schema import (
        audio_embeddings,
        audio_features_cache,
        service_connections,
        song_metadata_cache,
        user_calibration,
    )

    engine = request.app.state.engine

    # Fast direct counts — no expensive per-user subqueries
    async with engine.begin() as conn:
        user_count = (await conn.execute(
            sa.select(sa.func.count()).select_from(users)
        )).scalar() or 0

        connected_users = (await conn.execute(
            sa.select(sa.func.count(sa.distinct(service_connections.c.user_id)))
        )).scalar() or 0

        total_songs = (await conn.execute(
            sa.select(sa.func.count()).select_from(song_metadata_cache)
        )).scalar() or 0

        # Enriched = audio_features_cache rows with real features
        existing_ids = sa.select(song_metadata_cache.c.catalog_id).correlate(None)
        total_enriched = (await conn.execute(
            sa.select(sa.func.count()).select_from(audio_features_cache).where(
                sa.and_(
                    audio_features_cache.c.energy.isnot(None),
                    audio_features_cache.c.catalog_id.in_(existing_ids),
                )
            )
        )).scalar() or 0

        gpu_count = (await conn.execute(
            sa.select(sa.func.count()).select_from(audio_embeddings).where(
                audio_embeddings.c.clap_embedding.isnot(None)
            )
        )).scalar() or 0

        calibrated_users = (await conn.execute(
            sa.select(sa.func.count(sa.distinct(user_calibration.c.user_id)))
        )).scalar() or 0

    return {
        "version": __version__,
        "total_users": user_count,
        "connected_users": connected_users,
        "calibrated_users": calibrated_users,
        "total_songs": total_songs,
        "total_enriched": total_enriched,
        "enrichment_pct": round(
            min(total_enriched / total_songs, 1.0) * 100, 1
        ) if total_songs > 0 else 0,
        "gpu_embeddings": gpu_count,
    }


@router.get("/errors")
async def get_recent_errors(
    request: Request,
    limit: int = 50,
    _admin: dict = Depends(require_admin),
) -> dict:
    """Get recent errors from the logging database."""
    logs_engine = getattr(request.app.state, "logs_engine", None)
    if not logs_engine:
        return {"errors": [], "source": "unavailable"}

    try:
        from musicmind.db.logs import request_logs

        async with logs_engine.begin() as conn:
            result = await conn.execute(
                sa.select(request_logs)
                .where(request_logs.c.status_code >= 500)
                .order_by(request_logs.c.timestamp.desc())
                .limit(limit)
            )
            rows = result.fetchall()

        errors = [
            {
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "method": row.method,
                "path": row.path,
                "status_code": row.status_code,
                "duration_ms": row.duration_ms,
                "user_id": row.user_id,
                "error_detail": row.error_detail,
            }
            for row in rows
        ]
        return {"errors": errors, "source": "logs_db"}
    except Exception:
        logger.warning("Failed to fetch errors from logs DB")
        return {"errors": [], "source": "error"}


@router.get("/request-stats")
async def get_request_stats(
    request: Request,
    _admin: dict = Depends(require_admin),
) -> dict:
    """Get request statistics from the logging database."""
    logs_engine = getattr(request.app.state, "logs_engine", None)
    if not logs_engine:
        return {"stats": {}, "source": "unavailable"}

    try:
        from musicmind.db.logs import request_logs

        async with logs_engine.begin() as conn:
            # Total requests today
            total_q = await conn.execute(
                sa.select(sa.func.count()).select_from(request_logs).where(
                    request_logs.c.timestamp >= sa.func.current_date()
                )
            )
            total_today = total_q.scalar() or 0

            # Errors today
            errors_q = await conn.execute(
                sa.select(sa.func.count()).select_from(request_logs).where(
                    sa.and_(
                        request_logs.c.timestamp >= sa.func.current_date(),
                        request_logs.c.status_code >= 500,
                    )
                )
            )
            errors_today = errors_q.scalar() or 0

            # Avg response time today
            avg_q = await conn.execute(
                sa.select(sa.func.avg(request_logs.c.duration_ms)).where(
                    request_logs.c.timestamp >= sa.func.current_date()
                )
            )
            avg_duration = round(avg_q.scalar() or 0, 1)

            # Slow requests today (>5s)
            slow_q = await conn.execute(
                sa.select(sa.func.count()).select_from(request_logs).where(
                    sa.and_(
                        request_logs.c.timestamp >= sa.func.current_date(),
                        request_logs.c.duration_ms > 5000,
                    )
                )
            )
            slow_today = slow_q.scalar() or 0

        return {
            "stats": {
                "total_today": total_today,
                "errors_today": errors_today,
                "avg_duration_ms": avg_duration,
                "slow_requests_today": slow_today,
            },
            "source": "logs_db",
        }
    except Exception:
        logger.warning("Failed to fetch request stats from logs DB")
        return {"stats": {}, "source": "error"}


@router.get("/recent-songs")
async def get_recent_songs(
    request: Request,
    limit: int = 20,
    _admin: None = Depends(require_admin),
) -> dict:
    """Get most recently cached songs with enrichment status (green/red)."""
    from musicmind.db.schema import audio_features_cache, song_metadata_cache

    engine = request.app.state.engine

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                song_metadata_cache.c.catalog_id,
                song_metadata_cache.c.name,
                song_metadata_cache.c.artist_name,
                song_metadata_cache.c.user_id,
                song_metadata_cache.c.service_source,
                song_metadata_cache.c.fetched_at,
                song_metadata_cache.c.library_id,
            )
            .order_by(song_metadata_cache.c.fetched_at.desc())
            .limit(limit)
        )
        songs = result.fetchall()

        # Check enrichment status for each song
        catalog_ids = [s.catalog_id for s in songs]
        enriched_q = await conn.execute(
            sa.select(audio_features_cache.c.catalog_id).where(
                sa.and_(
                    audio_features_cache.c.catalog_id.in_(catalog_ids),
                    audio_features_cache.c.energy.isnot(None),
                )
            )
        )
        enriched_ids = {r.catalog_id for r in enriched_q}

    items = []
    for s in songs:
        items.append({
            "catalog_id": s.catalog_id,
            "name": s.name,
            "artist_name": s.artist_name,
            "service": s.service_source,
            "source": "library" if s.library_id else "worker",
            "enriched": s.catalog_id in enriched_ids,
            "fetched_at": s.fetched_at.isoformat() if s.fetched_at else None,
        })

    return {"songs": items}


@router.get("/enrichment-breakdown")
async def get_enrichment_breakdown_endpoint(
    request: Request,
    _admin: None = Depends(require_admin),
) -> dict:
    """Get enrichment pipeline breakdown: unenriched / partial / fully enriched."""
    breakdown = await get_enrichment_breakdown(request.app.state.engine)
    return breakdown


@router.get("/diagnostics")
async def get_diagnostics(
    request: Request,
    _admin: None = Depends(require_admin),
) -> dict:
    """Smart enrichment diagnostics with per-user, per-stage breakdown.

    Returns detailed enrichment state for each user including:
    - Per-stage counts (audio/embeddings/AI) with library vs non-library split
    - Failed vs pending vs complete breakdown with failure reasons
    - GPU embedding coverage (CLAP + MERT)
    - AI enrichment coverage (captions + classifier labels)
    - Cobweb expansion stats
    - Cross-referenced failure analysis from logs DB
    - Actionable insights (what needs attention)
    """
    from musicmind.api.admin.diagnostics import get_enrichment_diagnostics

    logs_engine = getattr(request.app.state, "logs_engine", None)
    return await get_enrichment_diagnostics(
        request.app.state.engine, logs_engine=logs_engine,
    )


@router.get("/db-capacity")
async def get_db_capacity(
    request: Request,
    _admin: None = Depends(require_admin),
) -> dict:
    """Get database size and capacity usage for main and logs DBs."""
    databases: list[dict] = []

    # Main database
    engine = request.app.state.engine
    try:
        async with engine.begin() as conn:
            result = await conn.execute(sa.text("SELECT pg_database_size(current_database())"))
            size_bytes = result.scalar() or 0
        databases.append({
            "name": "Main DB",
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 1),
            "size_gb": round(size_bytes / (1024 * 1024 * 1024), 2),
        })
    except Exception:
        logger.warning("Failed to get main DB size")

    # Logs database
    logs_engine = getattr(request.app.state, "logs_engine", None)
    if logs_engine:
        try:
            async with logs_engine.begin() as conn:
                result = await conn.execute(
                    sa.text("SELECT pg_database_size(current_database())")
                )
                size_bytes = result.scalar() or 0
            databases.append({
                "name": "Logs DB",
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / (1024 * 1024), 1),
                "size_gb": round(size_bytes / (1024 * 1024 * 1024), 2),
            })
        except Exception:
            logger.warning("Failed to get logs DB size")

    return {"databases": databases}


@router.get("/worker-status")
async def get_worker_status(
    request: Request,
    _admin: None = Depends(require_admin),
) -> dict:
    """Get worker status: live heartbeat from main DB + recent logs from logs DB."""
    from musicmind.db.schema import worker_status

    # Live heartbeat from main DB
    engine = request.app.state.engine
    heartbeat = None
    try:
        async with engine.begin() as conn:
            row = (await conn.execute(
                sa.select(worker_status).where(worker_status.c.id == 1)
            )).first()
            if row:
                heartbeat = {
                    "phase": row.phase,
                    "detail": row.detail,
                    "progress_current": row.progress_current,
                    "progress_total": row.progress_total,
                    "cycle": row.cycle,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                }
    except Exception:
        pass  # Table might not exist yet

    # Recent activity from logs DB
    logs_engine = getattr(request.app.state, "logs_engine", None)
    if not logs_engine:
        return {
            "available": heartbeat is not None,
            "heartbeat": heartbeat,
            "reason": "logs DB not connected",
        }

    try:
        from musicmind.db.logs import enrichment_logs

        async with logs_engine.begin() as conn:
            # Last enrichment timestamp
            last_q = await conn.execute(
                sa.select(enrichment_logs.c.timestamp)
                .order_by(enrichment_logs.c.timestamp.desc())
                .limit(1)
            )
            last_row = last_q.first()
            last_activity = last_row.timestamp.isoformat() if last_row else None

            # Total enriched today
            today_q = await conn.execute(
                sa.select(sa.func.count()).select_from(enrichment_logs).where(
                    sa.and_(
                        enrichment_logs.c.timestamp >= sa.func.current_date(),
                        enrichment_logs.c.result == "success",
                    )
                )
            )
            enriched_today = today_q.scalar() or 0

            # Failures today
            fail_q = await conn.execute(
                sa.select(sa.func.count()).select_from(enrichment_logs).where(
                    sa.and_(
                        enrichment_logs.c.timestamp >= sa.func.current_date(),
                        enrichment_logs.c.result != "success",
                    )
                )
            )
            failures_today = fail_q.scalar() or 0

            # Recent enrichment entries (last 10)
            recent_q = await conn.execute(
                sa.select(enrichment_logs)
                .order_by(enrichment_logs.c.timestamp.desc())
                .limit(10)
            )
            recent = [
                {
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                    "user_id": row.user_id[:8] if row.user_id else None,
                    "catalog_id": row.catalog_id,
                    "stage": row.stage,
                    "result": row.result,
                    "duration_ms": row.duration_ms,
                }
                for row in recent_q
            ]

            # Stages breakdown today
            stages_q = await conn.execute(
                sa.select(
                    enrichment_logs.c.stage,
                    sa.func.count().label("count"),
                ).where(
                    enrichment_logs.c.timestamp >= sa.func.current_date()
                ).group_by(enrichment_logs.c.stage)
            )
            stages = {row.stage: row.count for row in stages_q}

        return {
            "available": True,
            "heartbeat": heartbeat,
            "last_activity": last_activity,
            "enriched_today": enriched_today,
            "failures_today": failures_today,
            "stages_today": stages,
            "recent": recent,
        }
    except Exception:
        logger.warning("Failed to get worker status", exc_info=True)
        return {
            "available": heartbeat is not None,
            "heartbeat": heartbeat,
            "reason": "logs query failed",
        }


@router.post("/cleanup-orphans")
async def cleanup_orphans(
    request: Request,
    _admin: None = Depends(require_admin),
) -> dict:
    """Delete orphaned audio_features_cache rows with no matching song.

    These occur when songs are deleted from song_metadata_cache but their
    audio features rows are left behind. The audio data is already preserved
    in audio_features_global by ISRC.
    """
    result = await cleanup_orphaned_features(request.app.state.engine)
    return result


@router.post("/reindex/{user_id}")
async def reindex_user(
    request: Request,
    user_id: str,
    _admin: None = Depends(require_admin),
) -> dict:
    """Smart reindex: preserve enriched data, re-scan library, re-enrich gaps.

    Steps:
    1. Promote per-user audio features to global cache (by ISRC)
    2. Clear per-user song_metadata_cache + audio_features_cache
    3. Reset indexing status to trigger fresh library scan
    4. The indexer will re-fetch library from Apple Music/Spotify
    5. Audio enrichment will find existing data in global cache (instant)

    No API calls are wasted: audio features are preserved in audio_features_global.
    Only the library re-scan calls the music service API.
    """
    from musicmind.db.schema import (
        audio_features_cache,
        song_metadata_cache,
        user_indexing_status,
    )

    engine = request.app.state.engine

    # Verify user exists
    async with engine.begin() as conn:
        user_row = (await conn.execute(
            sa.select(users.c.id, users.c.email).where(users.c.id == user_id)
        )).first()
    if not user_row:
        raise HTTPException(status_code=404, detail="User not found")

    # Step 1: Promote per-user audio features to global ISRC cache
    promoted = 0
    async with engine.begin() as conn:
        # Get all per-user features that have ISRC
        af_rows = (await conn.execute(
            sa.select(audio_features_cache, song_metadata_cache.c.isrc).where(
                sa.and_(
                    audio_features_cache.c.user_id == user_id,
                    audio_features_cache.c.energy.isnot(None),
                    audio_features_cache.c.catalog_id == song_metadata_cache.c.catalog_id,
                    song_metadata_cache.c.user_id == user_id,
                    song_metadata_cache.c.isrc.isnot(None),
                    song_metadata_cache.c.isrc != "",
                )
            )
        )).fetchall()

        for row in af_rows:
            try:
                await conn.execute(sa.text(
                    "INSERT INTO audio_features_global"
                    " (isrc, tempo, energy, brightness, danceability,"
                    "  acousticness, valence_proxy, beat_strength,"
                    "  key, scale, instrumentalness, loudness,"
                    "  feature_source, analyzed_at)"
                    " VALUES (:isrc, :tempo, :energy, :brightness,"
                    "  :danceability, :acousticness, :valence_proxy,"
                    "  :beat_strength, :key, :scale, :instrumentalness,"
                    "  :loudness, :feature_source, now())"
                    " ON CONFLICT (isrc) DO NOTHING"
                ), {
                    "isrc": row.isrc,
                    "tempo": row.tempo, "energy": row.energy,
                    "brightness": row.brightness,
                    "danceability": row.danceability,
                    "acousticness": row.acousticness,
                    "valence_proxy": row.valence_proxy,
                    "beat_strength": row.beat_strength,
                    "key": row.key, "scale": row.scale,
                    "instrumentalness": row.instrumentalness,
                    "loudness": row.loudness,
                    "feature_source": (
                        row.feature_source if isinstance(row.feature_source, str)
                        else json.dumps(row.feature_source or {})
                    ),
                })
                promoted += 1
            except Exception:
                pass

    # Step 2: Clear per-user data
    async with engine.begin() as conn:
        del_af = await conn.execute(
            sa.delete(audio_features_cache).where(
                audio_features_cache.c.user_id == user_id
            )
        )
        del_songs = await conn.execute(
            sa.delete(song_metadata_cache).where(
                song_metadata_cache.c.user_id == user_id
            )
        )

    # Step 3: Reset indexing status
    async with engine.begin() as conn:
        await conn.execute(
            sa.delete(user_indexing_status).where(
                user_indexing_status.c.user_id == user_id
            )
        )

    logger.info(
        "Reindex for %s: promoted %d features to global, cleared %d songs + %d features",
        user_row.email, promoted, del_songs.rowcount, del_af.rowcount,
    )

    # Step 4: Build taste profile (fetches library) then run full indexing
    async def _reindex_pipeline() -> None:
        try:
            # First: fetch library via taste profile (populates song_metadata_cache)
            from musicmind.api.taste.service import TasteService
            logger.info("Reindex %s: fetching library via taste profile...", user_row.email)
            await TasteService().get_profile(
                engine, request.app.state.encryption, request.app.state.settings,
                user_id=user_id, force_refresh=True,
            )
            # Then: run the full enrichment pipeline (audio + embeddings + AI)
            from musicmind.indexer import run_indexing
            logger.info("Reindex %s: starting enrichment pipeline...", user_row.email)
            await run_indexing(
                engine, request.app.state.encryption, request.app.state.settings,
                user_id=user_id,
            )
            logger.info("Reindex %s: complete", user_row.email)
        except Exception:
            logger.exception("Reindex pipeline failed for %s", user_row.email)

    asyncio.ensure_future(_reindex_pipeline())

    return {
        "status": "reindex_started",
        "user": user_row.email,
        "promoted_to_global": promoted,
        "songs_cleared": del_songs.rowcount,
        "features_cleared": del_af.rowcount,
        "message": (
            f"Promoted {promoted} audio features to global cache. "
            f"Cleared {del_songs.rowcount} songs. "
            "Indexing will re-fetch library and reuse cached data."
        ),
    }
