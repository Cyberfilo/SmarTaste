"""API router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from musicmind.api.health import router as health_router
from musicmind.auth.router import router as auth_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router)
