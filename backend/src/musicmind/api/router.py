"""API router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from musicmind.api.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
