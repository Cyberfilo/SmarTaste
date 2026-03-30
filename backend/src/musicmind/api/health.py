"""Health check endpoint verifying database connectivity."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from musicmind import __version__

router = APIRouter()


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Health check verifying database connectivity."""
    engine = request.app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected", "version": __version__}
    except Exception as exc:
        return {"status": "unhealthy", "database": str(exc)}
