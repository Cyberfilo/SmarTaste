"""Natural language music search API."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from musicmind.api.rate_limit import limiter
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])

_search_service = None


def _get_service():
    global _search_service
    if _search_service is None:
        from musicmind.api.search.service import SearchService
        _search_service = SearchService()
    return _search_service


@router.get("")
@limiter.limit("30/minute")
async def semantic_search(
    request: Request,
    q: str = Query(..., min_length=2, max_length=200, description="Search query"),
    limit: int = Query(default=10, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Search your music catalog with natural language.

    Examples: "aggressive drill energy", "chill rainy day vibes",
    "something like Baby Gang but more melodic"
    """
    service = _get_service()
    settings = request.app.state.settings

    if not settings.modal_endpoint_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Semantic search requires GPU worker (CLAP). Not configured.",
        )

    results = await service.semantic_search(
        request.app.state.engine,
        settings,
        user_id=current_user["user_id"],
        query=q,
        limit=limit,
    )

    return {
        "query": q,
        "results": results,
        "total": len(results),
    }
