"""Playlist API endpoints — fetch real playlists from connected services."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from musicmind.api.playlists.schemas import (
    AddTrackRequest,
    AddTrackResponse,
    BriefResponse,
    CreateBriefRequest,
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
    brief_id: str | None = Query(
        default=None,
        description="Consult the target_vector of this brief (V 6.441).",
    ),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """V 6.430/6.441 — playlist-scoped recommendations with 0.8/0.2 blending.

    Scoring = playlist centroid ×0.8 + user taste ×0.2 across genre,
    audio traits, CLAP, and MERT spaces. If `brief_id` is supplied, the
    brief's synthesized target_vector overrides audio scalar targets
    (tempo, energy, valence, danceability) and boosts `genre_emphasis`
    in the blended genre vector.

    Candidates are pulled from the worker-populated pool (no 22s fresh-
    discovery re-run). `apple_only` filter is on by default because the
    add-to-playlist action is currently Apple-only.
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
            brief_id=brief_id,
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


# ── Add track to playlist (V 6.470 — Apple Music only) ─────────────────────


@router.post("/{playlist_id}/tracks")
@limiter.limit(PLAYLISTS_LIMIT)
async def add_track_to_playlist(
    request: Request,
    playlist_id: str,
    body: AddTrackRequest,
    current_user: dict = Depends(get_current_user),
) -> AddTrackResponse:
    """Add a catalog song to the user's Apple Music playlist.

    V 6.470 — powers the "+ Apple" button on brief suggestions.
    Backend-proxied: uses the music_user_token stored at connect time,
    so the frontend doesn't need to re-authorize MusicKit per click.
    """
    service = body.service.lower()
    if service != "apple_music":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only apple_music is supported for now",
        )
    try:
        await playlist_service.add_track(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            playlist_id=playlist_id,
            catalog_id=body.catalog_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Failed to add track to playlist")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc)[:500],
        ) from exc

    return AddTrackResponse(
        playlist_id=playlist_id,
        catalog_id=body.catalog_id,
        service=service,
        added=True,
    )


# ── Playlist brief (V 6.440 — chat-driven target data) ──────────────────────


@router.post("/{playlist_id}/brief", status_code=status.HTTP_201_CREATED)
@limiter.limit(PLAYLISTS_LIMIT)
async def create_playlist_brief(
    request: Request,
    playlist_id: str,
    body: CreateBriefRequest,
    current_user: dict = Depends(get_current_user),
) -> BriefResponse:
    """Persist a playlist brief (freeform 'what music for here' + mentions).

    V 6.440 step 5a — storage only. The gpt-5.4 target_vector synthesis
    that turns brief_text + mentioned-song enrichment into an audio-trait
    + mood target vector lands in a follow-up commit (5b); until then,
    `target_vector` comes back `null`.
    """
    if body.service.lower() not in {"apple_music", "spotify"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="service must be one of: apple_music, spotify",
        )

    try:
        created = await playlist_service.create_brief(
            request.app.state.engine,
            request.app.state.settings,
            user_id=current_user["user_id"],
            playlist_id=playlist_id,
            service=body.service.lower(),
            brief_text=body.brief_text,
            mentioned_songs=[s.model_dump() for s in body.mentioned_songs],
        )
    except Exception:
        logger.exception("Failed to create playlist brief")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create brief",
        )

    return BriefResponse(**created)


@router.get("/{playlist_id}/briefs")
@limiter.limit(PLAYLISTS_LIMIT)
async def list_playlist_briefs(
    request: Request,
    playlist_id: str,
    limit: int = Query(default=10, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the authenticated user's briefs for this playlist, newest first."""
    briefs = await playlist_service.list_briefs(
        request.app.state.engine,
        user_id=current_user["user_id"],
        playlist_id=playlist_id,
        limit=limit,
    )
    return {"items": briefs, "total": len(briefs)}
