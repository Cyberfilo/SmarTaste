"""Pydantic request/response models for taste profile endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GenreEntry(BaseModel):
    """A single genre with its normalized affinity weight."""

    genre: str = Field(description="Genre name with regional specificity preserved")
    weight: float = Field(description="Normalized affinity weight (genre_vector sums to ~1.0)")


class ArtistEntry(BaseModel):
    """A single artist with affinity score and song count."""

    name: str = Field(description="Artist name")
    score: float = Field(description="Affinity score normalized 0-1")
    song_count: int = Field(description="Number of songs by this artist in library")


class TasteProfileResponse(BaseModel):
    """Full taste profile built from one or both connected services.

    When service="unified", services_included lists which services contributed.
    """

    service: str = Field(
        description="Service source (spotify, apple_music, or unified)"
    )
    computed_at: str = Field(description="ISO 8601 timestamp of last computation")
    total_songs_analyzed: int = Field(
        description="Number of songs used to build profile"
    )
    listening_hours_estimated: float = Field(
        description="Estimated total listening hours"
    )
    familiarity_score: float = Field(
        description="0=focused on few genres, 1=adventurous (Shannon entropy)"
    )
    genre_vector: dict[str, float] = Field(
        description="Genre name -> normalized affinity"
    )
    top_artists: list[ArtistEntry] = Field(
        description="Artists sorted by affinity score descending"
    )
    audio_trait_preferences: dict[str, float] = Field(
        description="Audio trait -> fraction of library"
    )
    release_year_distribution: dict[str, float] = Field(
        description="Year -> fraction of library"
    )
    services_included: list[str] = Field(
        default_factory=list,
        description="Services that contributed to this profile (empty for single-service)",
    )


class TopGenresResponse(BaseModel):
    """Top genres from user's taste profile."""

    service: str = Field(description="Source service")
    genres: list[GenreEntry] = Field(
        description="Genres sorted by weight descending"
    )


class TopArtistsResponse(BaseModel):
    """Top artists from user's taste profile."""

    service: str = Field(description="Source service")
    artists: list[ArtistEntry] = Field(
        description="Artists sorted by affinity descending"
    )


class AudioTraitsResponse(BaseModel):
    """Audio trait preferences from user's taste profile."""

    service: str = Field(description="Source service")
    traits: dict[str, float] = Field(
        description="Audio trait -> fraction of library with that trait"
    )
    note: str | None = Field(
        default=None,
        description="Optional note, e.g. 'Audio traits not available for Spotify'",
    )
