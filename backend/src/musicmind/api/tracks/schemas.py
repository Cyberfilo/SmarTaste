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


class AnalyzeRequest(BaseModel):
    """Request to analyze seed tracks with Essentia."""

    catalog_ids: list[str] = Field(
        description="Track catalog IDs to analyze (max 10)",
        min_length=1,
        max_length=10,
    )


class AnalysisResult(BaseModel):
    """Result for a single analyzed track."""

    catalog_id: str = Field(description="Track ID")
    status: str = Field(description="analyzed, cached, no_preview, download_failed, partial")
    embedding_dim: int = Field(description="Embedding dimensions (128 or 0)")
    features_extracted: bool = Field(description="Whether scalar features were extracted")


class AnalyzeResponse(BaseModel):
    """Response from the seed track analysis endpoint."""

    results: list[AnalysisResult] = Field(description="Per-track analysis results")
    analyzed: int = Field(description="Tracks newly analyzed")
    cached: int = Field(description="Tracks with existing analysis")
    total: int = Field(description="Total tracks processed")
    essentia_available: bool = Field(description="Whether Essentia is installed")
