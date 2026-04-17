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
    sample_artwork_url: str | None = Field(
        default=None,
        description=(
            "Pre-rendered artwork URL for a representative song by this artist "
            "from the user's own library (Apple Music template resolved to a "
            "small thumbnail); empty string when no artwork is available."
        ),
    )


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
    breadth: BreadthMetrics | None = Field(
        default=None,
        description=(
            "Library breadth aggregates (genre entropy, artist concentration, "
            "composite sonic breadth). Null until the snapshot exists."
        ),
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


class SonicNeighbor(BaseModel):
    """A discovery artist whose sound matches the user's CLAP centroid."""

    artist_name: str = Field(description="Artist name")
    similarity: float = Field(
        description="Cosine similarity to the user's CLAP centroid (0-1)"
    )
    sample_song_name: str = Field(
        description="Representative song name used to match this artist"
    )
    sample_catalog_id: str = Field(
        description="Catalog id of the representative song (for recommendation deep-link)"
    )
    sample_album_name: str = Field(
        default="", description="Album name of the representative song"
    )
    artwork_url: str = Field(
        default="",
        description="Pre-formatted artwork URL from the global cache; empty when missing",
    )
    genre_names: list[str] = Field(
        default_factory=list,
        description="Genres attached to the representative song",
    )


class SonicNeighborsResponse(BaseModel):
    """Artists whose embedded sound is closest to the user's library centroid."""

    service: str = Field(description="Source service for the profile centroid")
    neighbors: list[SonicNeighbor] = Field(
        default_factory=list,
        description="Ranked artists (highest similarity first)",
    )
    note: str | None = Field(
        default=None,
        description=(
            "Optional note explaining degraded results "
            "(e.g. 'CLAP centroid not yet available')"
        ),
    )


class RecentEnrichment(BaseModel):
    """A song recently analyzed by the enrichment pipeline for this user."""

    catalog_id: str = Field(description="Apple Music / Spotify catalog id")
    name: str = Field(description="Track name")
    artist_name: str = Field(description="Track artist")
    album_name: str = Field(default="", description="Album name")
    artwork_url: str = Field(
        default="",
        description="Pre-formatted artwork URL; empty when no template available",
    )
    enriched_at: str | None = Field(
        default=None,
        description="ISO-8601 timestamp of enrichment (analyzed_at fallback)",
    )
    tempo: float | None = Field(default=None, description="Essentia tempo (BPM)")
    energy: float | None = Field(
        default=None, description="Essentia energy 0-1 scalar"
    )
    danceability: float | None = Field(
        default=None, description="Essentia danceability 0-1 scalar"
    )


class RecentEnrichmentsResponse(BaseModel):
    """Last N songs enriched for this user (freshest output of the pipeline)."""

    items: list[RecentEnrichment] = Field(
        default_factory=list,
        description="Most recently enriched songs, newest first",
    )
    total: int = Field(default=0, description="Number returned")


class BreadthMetrics(BaseModel):
    """Library-breadth numbers derived client-side from the taste snapshot.

    Returned by the /profile endpoint alongside existing fields so the frontend
    doesn't need a second round-trip just to compute these simple aggregates.
    """

    genre_entropy: float = Field(
        description="Shannon entropy of genre_vector, normalized to 0-1"
    )
    artist_concentration: float = Field(
        description="Fraction of total artist score held by the top 5 artists"
    )
    sonic_breadth: float = Field(
        description="Composite 0-1 breadth score (higher = more varied taste)"
    )


# Resolve forward reference to BreadthMetrics in TasteProfileResponse
TasteProfileResponse.model_rebuild()
