"""Admin API endpoints — live logs, enrichment progress, system status.

All endpoints require admin role (is_admin=true on user).
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import StreamingResponse

from musicmind.api.admin.log_stream import get_recent_logs, subscribe, unsubscribe
from musicmind.api.admin.progress import get_enrichment_progress
from musicmind.auth.dependencies import get_current_user
from musicmind.db.schema import users

import sqlalchemy as sa

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Dependency that checks admin role. Raises 403 if not admin."""
    engine = request.app.state.engine
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(users.c.is_admin).where(
                users.c.id == current_user["user_id"]
            )
        )
        row = result.first()

    if not row or not row.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


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
                except asyncio.TimeoutError:
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

    engine = request.app.state.engine
    settings = request.app.state.settings

    async with engine.begin() as conn:
        user_count = (await conn.execute(
            sa.select(sa.func.count()).select_from(users)
        )).scalar() or 0

    progress = await get_enrichment_progress(engine)
    total_songs = sum(u["total_songs"] for u in progress)
    total_enriched = sum(u["enriched_songs"] for u in progress)

    return {
        "version": __version__,
        "users": user_count,
        "total_songs": total_songs,
        "total_enriched": total_enriched,
        "enrichment_pct": round(
            total_enriched / total_songs * 100, 1
        ) if total_songs > 0 else 0,
        "soundstat_configured": bool(settings.soundstat_api_key),
    }
