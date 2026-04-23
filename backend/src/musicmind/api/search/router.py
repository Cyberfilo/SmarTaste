"""Natural language music search API."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from musicmind.api.rate_limit import SEARCH_LIMIT, limiter
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
@limiter.limit(SEARCH_LIMIT)
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


@router.get("/songs")
@limiter.limit(SEARCH_LIMIT)
async def songs_autocomplete(
    request: Request,
    q: str = Query(..., min_length=1, max_length=120, description="Song/artist query"),
    limit: int = Query(default=10, ge=1, le=25),
    service: str | None = Query(
        default=None,
        description="Filter by service_source (apple_music, spotify). Default: all.",
    ),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Text-based song autocomplete over `global_song_cache`.

    V 6.420 — powers the playlist-chat song-mention autocomplete. Returns
    enrichment (mood_tags, tempo, energy, valence, genres) alongside the
    basics so the frontend can render inline artwork-as-character cards.

    Relevance order: exact title → title prefix → exact artist → artist
    prefix → substring. Most recently cached wins ties.
    """
    service_norm = service.lower() if service else None
    if service_norm and service_norm not in {"apple_music", "spotify"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="service must be one of: apple_music, spotify",
        )

    svc = _get_service()
    results = await svc.songs_autocomplete(
        request.app.state.engine,
        query=q,
        limit=limit,
        service=service_norm,
    )

    # Touch current_user so rate limit/auth dependency isn't unused; also logs
    # the searching user for future relevance personalization.
    _ = current_user["user_id"]

    return {
        "query": q,
        "results": results,
        "total": len(results),
    }
