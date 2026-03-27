"""Pydantic response models for track detail endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AudioFeaturesResponse(BaseModel):
    """Per-track audio feature data for radar chart visualization."""

    catalog_id: str = Field(description="Service-specific track ID")
    energy: float | None = Field(default=None, description="Energy level 0-1")
    danceability: float | None = Field(default=None, description="Danceability 0-1")
    valence: float | None = Field(
        default=None, description="Musical positivity 0-1 (from valence_proxy)"
    )
    acousticness: float | None = Field(default=None, description="Acousticness 0-1")
    tempo: float | None = Field(default=None, description="Tempo in BPM")
    instrumentalness: float | None = Field(
        default=None,
        description="Instrumentalness 0-1 (not in DB -- included for API contract completeness)",
    )
    beat_strength: float | None = Field(default=None, description="Beat strength 0-1")
    brightness: float | None = Field(default=None, description="Brightness 0-1")
