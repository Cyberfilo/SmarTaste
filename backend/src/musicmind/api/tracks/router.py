"""Track detail view API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from musicmind.api.tracks.schemas import (
    AnalysisResult,
    AnalyzeRequest,
    AnalyzeResponse,
    AudioFeaturesResponse,
)
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


@router.post("/analyze")
async def analyze_seed_tracks(
    request: Request,
    body: AnalyzeRequest,
    current_user: dict = Depends(get_current_user),
) -> AnalyzeResponse:
    """Analyze seed tracks with Essentia for deep acoustic similarity.

    Downloads 30s preview clips and extracts 128-dim embeddings + scalar
    features. Max 10 tracks per request. Skips tracks already analyzed.

    Used by the chat system for "find songs that sound like X" requests.
    """
    try:
        result = await track_service.analyze_seeds(
            request.app.state.engine,
            user_id=current_user["user_id"],
            catalog_ids=body.catalog_ids,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error during seed analysis")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to analyze tracks",
        )

    items = [
        AnalysisResult(
            catalog_id=r["catalog_id"],
            status=r["status"],
            embedding_dim=r["embedding_dim"],
            features_extracted=r["features_extracted"],
        )
        for r in result["results"]
    ]

    return AnalyzeResponse(
        results=items,
        analyzed=result["analyzed"],
        cached=result["cached"],
        total=result["total"],
        essentia_available=result["essentia_available"],
    )
