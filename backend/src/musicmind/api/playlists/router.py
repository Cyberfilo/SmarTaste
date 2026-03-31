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
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/playlists", tags=["playlists"])
playlist_service = PlaylistService()


@router.get("")
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
