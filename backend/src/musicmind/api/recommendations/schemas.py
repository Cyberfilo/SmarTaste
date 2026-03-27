"""Pydantic request/response models for recommendation endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RecommendationItem(BaseModel):
    """A single scored recommendation with explanation."""

    catalog_id: str = Field(description="Service-specific track ID")
    name: str = Field(description="Track name")
    artist_name: str = Field(description="Primary artist name")
    album_name: str = Field(description="Album name")
    artwork_url: str = Field(description="Album artwork URL")
    preview_url: str = Field(description="30-second preview URL (may be empty)")
    score: float = Field(description="Overall match score 0-1")
    explanation: str = Field(
        description="Natural language explanation of why this was recommended"
    )
    strategy_source: str = Field(
        description="Which discovery strategy found this track"
    )
    genre_names: list[str] = Field(
        default_factory=list,
        description="Genre names associated with this track",
    )


class RecommendationsResponse(BaseModel):
    """Full recommendation feed response."""

    items: list[RecommendationItem] = Field(
        description="Scored recommendations sorted by match quality"
    )
    strategy: str = Field(
        description="Requested strategy (auto, similar_artists, genre_adjacent, editorial, charts)"
    )
    mood: str | None = Field(
        default=None,
        description="Mood filter applied (workout, chill, focus, party, sad, driving)",
    )
    total: int = Field(description="Total number of recommendations returned")
    weights_adapted: bool = Field(
        description="Whether adaptive weights were used instead of defaults"
    )


class FeedbackRequest(BaseModel):
    """User feedback on a recommended track."""

    feedback_type: str = Field(
        description="Feedback type: thumbs_up, thumbs_down, or skip"
    )

    @field_validator("feedback_type")
    @classmethod
    def validate_feedback_type(cls, v: str) -> str:
        """Reject feedback types not in the allowed set."""
        allowed = {"thumbs_up", "thumbs_down", "skip"}
        if v not in allowed:
            msg = f"Invalid feedback_type '{v}'. Must be one of: {', '.join(sorted(allowed))}"
            raise ValueError(msg)
        return v


class FeedbackResponse(BaseModel):
    """Confirmation that feedback was recorded."""

    catalog_id: str = Field(description="Track ID the feedback was recorded for")
    feedback_type: str = Field(description="The feedback type that was recorded")
    recorded: bool = Field(default=True, description="Whether the feedback was saved")
