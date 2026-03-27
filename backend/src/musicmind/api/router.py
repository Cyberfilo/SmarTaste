"""API router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from musicmind.api.claude.router import router as claude_router
from musicmind.api.health import router as health_router
from musicmind.api.services.router import router as services_router
from musicmind.api.stats.router import router as stats_router
from musicmind.api.taste.router import router as taste_router
from musicmind.auth.router import router as auth_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router)
api_router.include_router(services_router)
api_router.include_router(claude_router)
api_router.include_router(taste_router)
api_router.include_router(stats_router)
