"""Recommendation feed API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from musicmind.api.recommendations.schemas import (
    BreakdownDimension,
    BreakdownResponse,
    FeedbackRequest,
    FeedbackResponse,
    RecommendationItem,
    RecommendationsResponse,
)
from musicmind.api.recommendations.service import (
    VALID_MOODS,
    VALID_STRATEGIES,
    RecommendationService,
)
from musicmind.api.rate_limit import RECOMMENDATIONS_LIMIT, limiter
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])
recommendation_service = RecommendationService()


@router.get("")
@limiter.limit(RECOMMENDATIONS_LIMIT)
async def get_recommendations(
    request: Request,
    strategy: str = Query(default="all", description="Discovery strategy"),
    mood: str | None = Query(default=None, description="Mood filter"),
    limit: int = Query(default=10, ge=1, le=50, description="Max results"),
    current_user: dict = Depends(get_current_user),
) -> RecommendationsResponse:
    """Get personalized music recommendations.

    Params:
        strategy: One of all, similar_artist, genre_adjacent, editorial, chart.
        mood: Optional mood filter (workout, chill, focus, party, sad, driving,
            energy, melancholy).
        limit: Max number of recommendations (1-50, default 10).
    """
    # Validate strategy at router level
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid strategy '{strategy}'. "
                f"Must be one of: {', '.join(sorted(VALID_STRATEGIES))}"
            ),
        )

    # Validate mood at router level
    if mood is not None and mood not in VALID_MOODS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown mood '{mood}'. "
                f"Available: {', '.join(sorted(VALID_MOODS))}"
            ),
        )

    try:
        result = await recommendation_service.get_recommendations(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            strategy=strategy,
            mood=mood,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error generating recommendations")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate recommendations",
        )

    items = [
        RecommendationItem(
            catalog_id=item["catalog_id"],
            name=item["name"],
            artist_name=item["artist_name"],
            album_name=item["album_name"],
            artwork_url=item["artwork_url"],
            preview_url=item["preview_url"],
            score=item["score"],
            explanation=item["explanation"],
            strategy_source=item["strategy_source"],
            genre_names=item.get("genre_names", []),
        )
        for item in result["items"]
    ]

    return RecommendationsResponse(
        items=items,
        strategy=result["strategy"],
        mood=result["mood"],
        total=result["total"],
        weights_adapted=result["weights_adapted"],
    )


@router.post("/{catalog_id}/feedback")
async def submit_feedback(
    request: Request,
    catalog_id: str,
    body: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
) -> FeedbackResponse:
    """Submit feedback on a recommended track.

    Params:
        catalog_id: Service-specific track ID.
        body: FeedbackRequest with feedback_type (thumbs_up, thumbs_down, skip).
    """
    try:
        await recommendation_service.submit_feedback(
            request.app.state.engine,
            user_id=current_user["user_id"],
            catalog_id=catalog_id,
            feedback_type=body.feedback_type,
        )
    except Exception:
        logger.exception("Unexpected error recording feedback")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record feedback",
        )

    return FeedbackResponse(
        catalog_id=catalog_id,
        feedback_type=body.feedback_type,
        recorded=True,
    )


@router.get("/{catalog_id}/breakdown")
async def get_breakdown(
    request: Request,
    catalog_id: str,
    current_user: dict = Depends(get_current_user),
) -> BreakdownResponse:
    """Get the 7-dimension scoring breakdown for a recommendation.

    Params:
        catalog_id: Service-specific track ID.
    """
    try:
        result = await recommendation_service.get_scoring_breakdown(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            catalog_id=catalog_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error computing breakdown")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute scoring breakdown",
        )

    dimensions = [
        BreakdownDimension(
            name=d["name"],
            label=d["label"],
            score=d["score"],
            weight=d["weight"],
        )
        for d in result["dimensions"]
    ]

    return BreakdownResponse(
        catalog_id=result["catalog_id"],
        overall_score=result["overall_score"],
        dimensions=dimensions,
        explanation=result["explanation"],
    )
