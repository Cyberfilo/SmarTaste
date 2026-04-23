"""Playlist API endpoints — fetch real playlists from connected services."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from musicmind.api.playlists.schemas import (
    PlaylistListResponse,
    PlaylistOut,
    PlaylistTrackOut,
    PlaylistTracksResponse,
)
from musicmind.api.playlists.service import PlaylistService
from musicmind.api.rate_limit import PLAYLISTS_LIMIT, limiter
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/playlists", tags=["playlists"])
playlist_service = PlaylistService()


@router.get("")
@limiter.limit(PLAYLISTS_LIMIT)
async def list_playlists(
    request: Request,
    service: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> PlaylistListResponse:
    """List the user's playlists from connected services.

    Params:
        service: Filter by service (spotify or apple_music). Omit for all.
    """
    try:
        playlists = await playlist_service.list_playlists(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Failed to fetch playlists")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch playlists",
        )

    items = [PlaylistOut(**p) for p in playlists]
    return PlaylistListResponse(playlists=items, total=len(items))


@router.get("/{playlist_id}/tracks")
@limiter.limit(PLAYLISTS_LIMIT)
async def get_playlist_tracks(
    request: Request,
    playlist_id: str,
    service: str = Query(description="Service: spotify or apple_music"),
    limit: int = Query(default=100, ge=1, le=300),
    current_user: dict = Depends(get_current_user),
) -> PlaylistTracksResponse:
    """Get tracks from a specific service playlist.

    Params:
        playlist_id: Service-specific playlist ID.
        service: Which service owns this playlist (spotify or apple_music).
        limit: Max tracks to return (default 100).
    """
    try:
        tracks = await playlist_service.get_playlist_tracks(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            playlist_id=playlist_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Failed to fetch playlist tracks")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch playlist tracks",
        )

    items = [PlaylistTrackOut(**t) for t in tracks]
    return PlaylistTracksResponse(
        playlist_id=playlist_id,
        service=service,
        items=items,
        total=len(items),
    )


@router.get("/{playlist_id}/recommendations")
@limiter.limit(PLAYLISTS_LIMIT)
async def get_playlist_recommendations(
    request: Request,
    playlist_id: str,
    service: str = Query(description="Service: spotify or apple_music"),
    limit: int = Query(default=10, ge=1, le=30),
    apple_only: bool = Query(
        default=True,
        description="Filter candidates to Apple Music (V 6.430 default).",
    ),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """V 6.430 — playlist-scoped recommendations with 0.8/0.2 blending.

    Scoring = playlist centroid ×0.8 + user taste ×0.2 across genre,
    audio traits, CLAP, and MERT spaces. Candidates are pulled from the
    worker-populated pool (no 22s fresh-discovery re-run). `apple_only`
    filter is on by default because the add-to-playlist action is
    currently Apple-only.
    """
    try:
        result = await playlist_service.get_playlist_recommendations(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            playlist_id=playlist_id,
            limit=limit,
            apple_only=apple_only,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Failed to get playlist recommendations")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get recommendations",
        )

    return result
