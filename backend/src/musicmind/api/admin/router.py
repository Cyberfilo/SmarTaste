"""Admin API endpoints — live logs, enrichment progress, system status.

Protected by ADMIN_SECRET header or is_admin user flag.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import StreamingResponse

from musicmind.api.admin.log_stream import get_recent_logs, subscribe, unsubscribe
from musicmind.api.admin.progress import get_enrichment_breakdown, get_enrichment_progress
from musicmind.db.schema import users

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_SECRET = os.environ.get("MUSICMIND_ADMIN_SECRET", "")


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
        audio_features_global,
        listening_history,
        service_connections,
        user_calibration,
    )

    engine = request.app.state.engine
    settings = request.app.state.settings

    async with engine.begin() as conn:
        user_count = (await conn.execute(
            sa.select(sa.func.count()).select_from(users)
        )).scalar() or 0

        # Service connections count
        connections_count = (await conn.execute(
            sa.select(sa.func.count()).select_from(service_connections)
        )).scalar() or 0

        # Global ISRC cache size
        global_cache_count = (await conn.execute(
            sa.select(sa.func.count()).select_from(audio_features_global)
        )).scalar() or 0

        # Listening history entries
        history_count = (await conn.execute(
            sa.select(sa.func.count()).select_from(listening_history)
        )).scalar() or 0

        # Calibrated users
        calibrated_users = (await conn.execute(
            sa.select(sa.func.count(sa.distinct(user_calibration.c.user_id)))
        )).scalar() or 0

    progress = await get_enrichment_progress(engine)
    total_songs = sum(u["total_songs"] for u in progress)
    total_enriched = sum(u["enriched_songs"] for u in progress)

    return {
        "version": __version__,
        "users": user_count,
        "connections": connections_count,
        "calibrated_users": calibrated_users,
        "total_songs": total_songs,
        "total_enriched": total_enriched,
        "enrichment_pct": round(
            total_enriched / total_songs * 100, 1
        ) if total_songs > 0 else 0,
        "global_isrc_cache": global_cache_count,
        "listening_history_entries": history_count,
        "soundstat_configured": bool(settings.soundstat_api_key),
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
    """Get worker status from enrichment_logs in logs DB."""
    logs_engine = getattr(request.app.state, "logs_engine", None)
    if not logs_engine:
        return {"available": False, "reason": "logs DB not connected"}

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
            "last_activity": last_activity,
            "enriched_today": enriched_today,
            "failures_today": failures_today,
            "stages_today": stages,
            "recent": recent,
        }
    except Exception:
        logger.warning("Failed to get worker status", exc_info=True)
        return {"available": False, "reason": "query failed"}
