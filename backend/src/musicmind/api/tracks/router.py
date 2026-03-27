"""Track detail view API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from musicmind.api.tracks.schemas import AudioFeaturesResponse
from musicmind.api.tracks.service import TrackService
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tracks", tags=["tracks"])
track_service = TrackService()


@router.get("/{catalog_id}/audio-features")
async def get_audio_features(
    request: Request,
    catalog_id: str,
    current_user: dict = Depends(get_current_user),
) -> AudioFeaturesResponse:
    """Get audio features for a specific track.

    Params:
        catalog_id: Service-specific track ID.
    """
    try:
        result = await track_service.get_audio_features(
            request.app.state.engine,
            user_id=current_user["user_id"],
            catalog_id=catalog_id,
        )
    except Exception:
        logger.exception("Unexpected error fetching audio features")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch audio features",
        )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio features not available for this track",
        )

    return AudioFeaturesResponse(
        catalog_id=result["catalog_id"],
        energy=result["energy"],
        danceability=result["danceability"],
        valence=result["valence"],
        acousticness=result["acousticness"],
        tempo=result["tempo"],
        instrumentalness=result["instrumentalness"],
        beat_strength=result["beat_strength"],
        brightness=result["brightness"],
    )
