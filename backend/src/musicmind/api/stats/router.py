"""Listening stats API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from musicmind.api.stats.schemas import (
    StatArtistEntry,
    StatGenreEntry,
    StatTrackEntry,
    TimelineEntry,
    TimelineResponse,
    TopArtistsResponse,
    TopGenresResponse,
    TopTracksResponse,
)
from musicmind.api.stats.service import StatsService
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["stats"])
stats_service = StatsService()

VALID_PERIODS = ("month", "6months", "alltime")


@router.get("/tracks")
async def get_top_tracks(
    request: Request,
    period: str = Query(default="month"),
    service: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
) -> TopTracksResponse:
    """Get top tracks for the authenticated user by time period.

    Params:
        period: Time period -- month, 6months, or alltime (default: month).
        service: Target service (spotify or apple_music). Auto-detected if omitted.
        limit: Maximum items to return (default 20, max 50).
    """
    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid period. Must be one of: month, 6months, alltime",
        )

    try:
        result = await stats_service.get_top_tracks(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            period=period,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching top tracks")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch top tracks",
        )

    items = [
        StatTrackEntry(
            rank=item.get("rank", i + 1),
            name=item.get("name", ""),
            artist_name=item.get("artist_name", ""),
            album_name=item.get("album_name", ""),
            play_count_estimate=item.get("play_count_estimate"),
        )
        for i, item in enumerate(result.get("items", []))
    ]

    return TopTracksResponse(
        service=result.get("service", ""),
        period=result.get("period", period),
        items=items,
        total=result.get("total", len(items)),
    )


@router.get("/artists")
async def get_top_artists(
    request: Request,
    period: str = Query(default="month"),
    service: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
) -> TopArtistsResponse:
    """Get top artists for the authenticated user by time period.

    Params:
        period: Time period -- month, 6months, or alltime (default: month).
        service: Target service (spotify or apple_music). Auto-detected if omitted.
        limit: Maximum items to return (default 20, max 50).
    """
    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid period. Must be one of: month, 6months, alltime",
        )

    try:
        result = await stats_service.get_top_artists(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            period=period,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching top artists")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch top artists",
        )

    items = [
        StatArtistEntry(
            rank=item.get("rank", i + 1),
            name=item.get("name", ""),
            genres=item.get("genres", []),
            score=item.get("score"),
        )
        for i, item in enumerate(result.get("items", []))
    ]

    return TopArtistsResponse(
        service=result.get("service", ""),
        period=result.get("period", period),
        items=items,
        total=result.get("total", len(items)),
    )


@router.get("/genres")
async def get_top_genres(
    request: Request,
    period: str = Query(default="month"),
    service: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
) -> TopGenresResponse:
    """Get top genres for the authenticated user by time period.

    Params:
        period: Time period -- month, 6months, or alltime (default: month).
        service: Target service (spotify or apple_music). Auto-detected if omitted.
        limit: Maximum items to return (default 20, max 50).
    """
    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid period. Must be one of: month, 6months, alltime",
        )

    try:
        result = await stats_service.get_top_genres(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            period=period,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching top genres")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch top genres",
        )

    items = [
        StatGenreEntry(
            rank=item.get("rank", i + 1),
            genre=item.get("genre", ""),
            track_count=item.get("track_count", 0),
            artist_count=item.get("artist_count", 0),
        )
        for i, item in enumerate(result.get("items", []))
    ]

    return TopGenresResponse(
        service=result.get("service", ""),
        period=result.get("period", period),
        items=items,
        total=result.get("total", len(items)),
    )


@router.get("/timeline")
async def get_timeline(
    request: Request,
    service: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
) -> TimelineResponse:
    """Get a chronological timeline of songs (most recent first).

    Params:
        service: Target service (spotify or apple_music). Auto-detected if omitted.
        limit: Maximum items to return (default 50, max 200).
    """
    try:
        result = await stats_service.get_timeline(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching timeline")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch timeline",
        )

    items = [
        TimelineEntry(
            catalog_id=item.get("catalog_id", ""),
            name=item.get("name", ""),
            artist_name=item.get("artist_name", ""),
            album_name=item.get("album_name", ""),
            genre_names=item.get("genre_names", []),
            date=item.get("date"),
            date_type=item.get("date_type", "unknown"),
            artwork_url=item.get("artwork_url", ""),
        )
        for item in result.get("items", [])
    ]

    return TimelineResponse(
        service=result.get("service", ""),
        items=items,
        total=result.get("total", len(items)),
    )
