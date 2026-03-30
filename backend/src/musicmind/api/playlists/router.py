"""Playlist API endpoints — CRUD + AI-powered operations."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from musicmind.api.playlists.schemas import (
    AddTracksRequest,
    CreatePlaylistRequest,
    PlaylistDetailOut,
    PlaylistItemOut,
    PlaylistListResponse,
    PlaylistOut,
    UpdatePlaylistRequest,
)
from musicmind.api.playlists.service import PlaylistService
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/playlists", tags=["playlists"])
playlist_service = PlaylistService()


@router.get("")
async def list_playlists(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> PlaylistListResponse:
    """List all playlists for the authenticated user."""
    playlists = await playlist_service.list_playlists(
        request.app.state.engine,
        user_id=current_user["user_id"],
    )
    items = [PlaylistOut(**p) for p in playlists]
    return PlaylistListResponse(playlists=items, total=len(items))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_playlist(
    request: Request,
    body: CreatePlaylistRequest,
    current_user: dict = Depends(get_current_user),
) -> PlaylistOut:
    """Create a new playlist."""
    result = await playlist_service.create_playlist(
        request.app.state.engine,
        user_id=current_user["user_id"],
        name=body.name,
        description=body.description,
        ai_context=body.ai_context,
    )
    return PlaylistOut(**result)


@router.get("/{playlist_id}")
async def get_playlist(
    request: Request,
    playlist_id: int,
    current_user: dict = Depends(get_current_user),
) -> PlaylistDetailOut:
    """Get a playlist with all tracks."""
    result = await playlist_service.get_playlist(
        request.app.state.engine,
        user_id=current_user["user_id"],
        playlist_id=playlist_id,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playlist not found",
        )
    items = [PlaylistItemOut(**item) for item in result.pop("items", [])]
    return PlaylistDetailOut(**result, items=items)


@router.patch("/{playlist_id}")
async def update_playlist(
    request: Request,
    playlist_id: int,
    body: UpdatePlaylistRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Update playlist metadata (name, description, AI context)."""
    updated = await playlist_service.update_playlist(
        request.app.state.engine,
        user_id=current_user["user_id"],
        playlist_id=playlist_id,
        name=body.name,
        description=body.description,
        ai_context=body.ai_context,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playlist not found",
        )
    return {"updated": True}


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playlist(
    request: Request,
    playlist_id: int,
    current_user: dict = Depends(get_current_user),
) -> None:
    """Delete a playlist and all its tracks."""
    deleted = await playlist_service.delete_playlist(
        request.app.state.engine,
        user_id=current_user["user_id"],
        playlist_id=playlist_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playlist not found",
        )


@router.post("/{playlist_id}/tracks")
async def add_tracks(
    request: Request,
    playlist_id: int,
    body: AddTracksRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Add tracks to a playlist."""
    try:
        added = await playlist_service.add_tracks(
            request.app.state.engine,
            user_id=current_user["user_id"],
            playlist_id=playlist_id,
            catalog_ids=body.catalog_ids,
            added_by="user",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return {"added": added}


@router.delete("/{playlist_id}/tracks/{item_id}")
async def remove_track(
    request: Request,
    playlist_id: int,
    item_id: int,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Remove a track from a playlist."""
    removed = await playlist_service.remove_track(
        request.app.state.engine,
        user_id=current_user["user_id"],
        playlist_id=playlist_id,
        item_id=item_id,
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Track or playlist not found",
        )
    return {"removed": True}
