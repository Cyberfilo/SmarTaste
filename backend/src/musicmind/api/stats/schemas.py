"""Pydantic request/response models for listening stats endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StatTrackEntry(BaseModel):
    """A single track entry in the top tracks ranking."""

    rank: int = Field(description="1-based position in the ranking")
    name: str = Field(description="Track name")
    artist_name: str = Field(description="Primary artist name")
    album_name: str = Field(description="Album name")
    play_count_estimate: int | None = Field(
        default=None,
        description="Estimated play count (None for Spotify since no play counts available)",
    )


class StatArtistEntry(BaseModel):
    """A single artist entry in the top artists ranking."""

    rank: int = Field(description="1-based position in the ranking")
    name: str = Field(description="Artist name")
    genres: list[str] = Field(description="Artist genre list")
    score: float | None = Field(
        default=None,
        description="Affinity score (None for Apple Music computed stats)",
    )


class StatGenreEntry(BaseModel):
    """A single genre entry in the top genres ranking."""

    rank: int = Field(description="1-based position in the ranking")
    genre: str = Field(description="Genre name")
    track_count: int = Field(description="Number of tracks associated with this genre")
    artist_count: int = Field(
        description="Number of unique artists associated with this genre"
    )


class TopTracksResponse(BaseModel):
    """Response for top tracks endpoint."""

    service: str = Field(description="Source service (spotify or apple_music)")
    period: str = Field(description="Time period (month, 6months, or alltime)")
    items: list[StatTrackEntry] = Field(description="Ranked track entries")
    total: int = Field(description="Total number of items returned")


class TopArtistsResponse(BaseModel):
    """Response for top artists endpoint."""

    service: str = Field(description="Source service (spotify or apple_music)")
    period: str = Field(description="Time period (month, 6months, or alltime)")
    items: list[StatArtistEntry] = Field(description="Ranked artist entries")
    total: int = Field(description="Total number of items returned")


class TopGenresResponse(BaseModel):
    """Response for top genres endpoint."""

    service: str = Field(description="Source service (spotify or apple_music)")
    period: str = Field(description="Time period (month, 6months, or alltime)")
    items: list[StatGenreEntry] = Field(description="Ranked genre entries")
    total: int = Field(description="Total number of items returned")
