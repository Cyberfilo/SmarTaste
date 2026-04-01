"""Calibration API endpoints for onboarding taste wizard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from musicmind.api.calibration.schemas import (
    AlbumOut,
    AlbumsResponse,
    ArtistCalibrationEntry,
    ArtistsResponse,
    CalibrationStatusResponse,
    SaveCalibrationRequest,
)
from musicmind.api.calibration.service import CalibrationService
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calibration", tags=["calibration"])
calibration_service = CalibrationService()


@router.get("/albums")
async def get_albums(
    request: Request,
    service: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> AlbumsResponse:
    """Fetch the user's library albums for calibration step 1.

    Returns albums with artwork for the selection grid.
    """
    try:
        albums = await calibration_service.get_albums(
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
        logger.exception("Failed to fetch albums for calibration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch albums",
        )

    items = [AlbumOut(**a) for a in albums]
    return AlbumsResponse(albums=items, total=len(items))


@router.get("/artists")
async def get_artists(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> ArtistsResponse:
    """Get top artists for calibration step 2 (confirm/reject).

    Returns artists sorted by affinity score from the taste profile.
    """
    try:
        artists, service = await calibration_service.get_artists(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Failed to fetch artists for calibration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch artists",
        )

    items = [
        ArtistCalibrationEntry(
            name=a["name"] if isinstance(a, dict) else a.name,
            score=a["score"] if isinstance(a, dict) else a.score,
            song_count=a["song_count"] if isinstance(a, dict) else a.song_count,
        )
        for a in artists[:20]
    ]
    return ArtistsResponse(artists=items, service=service)


@router.post("/save")
async def save_calibration(
    request: Request,
    body: SaveCalibrationRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Save all calibration selections from the onboarding wizard.

    Replaces any existing calibration. After saving, triggers a profile
    rebuild so the weights take effect immediately.
    """
    try:
        count = await calibration_service.save_calibration(
            request.app.state.engine,
            user_id=current_user["user_id"],
            items=[item.model_dump() for item in body.items],
        )
    except Exception:
        logger.exception("Failed to save calibration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save calibration",
        )

    # Trigger profile rebuild so calibration weights take effect
    from musicmind.api.taste.service import TasteService

    taste_svc = TasteService()
    try:
        await taste_svc.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            force_refresh=True,
        )
    except Exception:
        logger.warning("Profile rebuild after calibration failed, will rebuild on next access")

    return {"message": "Calibration saved", "items_saved": count}


@router.get("/status")
async def get_calibration_status(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> CalibrationStatusResponse:
    """Check if the user has completed the onboarding calibration."""
    try:
        result = await calibration_service.get_calibration_status(
            request.app.state.engine,
            user_id=current_user["user_id"],
        )
    except Exception:
        logger.exception("Failed to check calibration status")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check calibration status",
        )

    return CalibrationStatusResponse(**result)
