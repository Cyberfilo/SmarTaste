"""Taste profile API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status

from musicmind.api.taste.schemas import (
    ArtistEntry,
    AudioTraitsResponse,
    GenreEntry,
    TasteProfileResponse,
    TopArtistsResponse,
    TopGenresResponse,
)
from musicmind.api.taste.service import TasteService
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taste", tags=["taste"])
taste_service = TasteService()

# Track in-progress background rebuilds (user_id -> status)
_rebuild_status: dict[str, str] = {}


@router.get("/profile")
async def get_profile(
    request: Request,
    service: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
) -> TasteProfileResponse:
    """Get the full taste profile for the authenticated user.

    Params:
        service: Target service (spotify or apple_music). Auto-detected if omitted.
        refresh: Force re-fetch even if cache is fresh (<24h).
    """
    try:
        profile = await taste_service.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            force_refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error building taste profile")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build taste profile",
        )

    top_artists = [
        ArtistEntry(
            name=a["name"],
            score=a["score"],
            song_count=a["song_count"],
        )
        if isinstance(a, dict)
        else a
        for a in profile.get("top_artists", [])
    ]

    return TasteProfileResponse(
        service=profile.get("service", ""),
        computed_at=profile.get("computed_at", ""),
        total_songs_analyzed=profile.get("total_songs_analyzed", 0),
        listening_hours_estimated=profile.get("listening_hours_estimated", 0.0),
        familiarity_score=profile.get("familiarity_score", 0.0),
        genre_vector=profile.get("genre_vector", {}),
        top_artists=top_artists,
        audio_trait_preferences=profile.get("audio_trait_preferences", {}),
        release_year_distribution=profile.get("release_year_distribution", {}),
        services_included=profile.get("services_included", []),
    )


@router.post("/profile/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh_profile(
    request: Request,
    background_tasks: BackgroundTasks,
    service: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Trigger a background taste profile rebuild.

    Returns 202 immediately. Poll GET /api/taste/profile/status for completion.
    """
    user_id = current_user["user_id"]

    if _rebuild_status.get(user_id) == "in_progress":
        return {"status": "in_progress", "message": "Profile rebuild already in progress"}

    async def _rebuild() -> None:
        _rebuild_status[user_id] = "in_progress"
        try:
            await taste_service.get_profile(
                request.app.state.engine,
                request.app.state.encryption,
                request.app.state.settings,
                user_id=user_id,
                service=service,
                force_refresh=True,
            )
            _rebuild_status[user_id] = "completed"
            logger.info("Background profile rebuild completed for user %s", user_id)
        except Exception:
            _rebuild_status[user_id] = "failed"
            logger.exception("Background profile rebuild failed for user %s", user_id)

    background_tasks.add_task(_rebuild)
    _rebuild_status[user_id] = "in_progress"
    return {"status": "accepted", "message": "Profile rebuild started"}


@router.get("/profile/status")
async def get_profile_status(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Check status of a background profile rebuild."""
    user_id = current_user["user_id"]
    status_val = _rebuild_status.get(user_id, "idle")
    return {"status": status_val}


@router.get("/genres")
async def get_genres(
    request: Request,
    service: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
) -> TopGenresResponse:
    """Get top genres from the user's taste profile."""
    try:
        profile = await taste_service.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            force_refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching genres")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch genres",
        )

    genre_vector = profile.get("genre_vector", {})
    genres = [
        GenreEntry(genre=k, weight=v)
        for k, v in sorted(genre_vector.items(), key=lambda x: x[1], reverse=True)
    ]

    return TopGenresResponse(
        service=profile.get("service", ""),
        genres=genres,
    )


@router.get("/artists")
async def get_artists(
    request: Request,
    service: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
) -> TopArtistsResponse:
    """Get top artists from the user's taste profile, ranked by affinity score."""
    try:
        profile = await taste_service.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            force_refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching artists")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch artists",
        )

    raw_artists = profile.get("top_artists", [])
    artists = [
        ArtistEntry(
            name=a["name"],
            score=a["score"],
            song_count=a["song_count"],
        )
        if isinstance(a, dict)
        else a
        for a in raw_artists
    ]

    return TopArtistsResponse(
        service=profile.get("service", ""),
        artists=artists,
    )


@router.get("/audio-traits")
async def get_audio_traits(
    request: Request,
    service: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
) -> AudioTraitsResponse:
    """Get audio trait preferences from the user's taste profile."""
    try:
        profile = await taste_service.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            force_refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching audio traits")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch audio traits",
        )

    traits = profile.get("audio_trait_preferences", {})
    resolved_service = profile.get("service", service or "")

    note = None
    if not traits and resolved_service == "spotify":
        note = "Audio traits not available for Spotify (Apple Music only)"

    return AudioTraitsResponse(
        service=resolved_service,
        traits=traits,
        note=note,
    )
