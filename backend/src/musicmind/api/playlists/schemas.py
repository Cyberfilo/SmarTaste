"""Pydantic models for playlist endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlaylistOut(BaseModel):
    """A playlist from a connected service."""

    service_playlist_id: str = Field(description="Service-specific playlist ID")
    name: str = Field(description="Playlist name")
    description: str = Field(default="", description="Playlist description")
    track_count: int = Field(default=0, description="Number of tracks")
    artwork_url: str = Field(default="", description="Cover artwork URL")
    owner: str = Field(default="", description="Playlist owner name")
    service: str = Field(description="Source service (spotify or apple_music)")


class PlaylistTrackOut(BaseModel):
    """A track within a playlist."""

    catalog_id: str = Field(description="Service-specific track ID")
    name: str = Field(description="Track name")
    artist_name: str = Field(description="Primary artist")
    album_name: str = Field(description="Album name")
    artwork_url: str = Field(default="", description="Album artwork URL")
    genre_names: list[str] = Field(default_factory=list, description="Genres")
    service: str = Field(default="", description="Source service")


class PlaylistListResponse(BaseModel):
    """List of user playlists from connected services."""

    playlists: list[PlaylistOut] = Field(description="User's playlists")
    total: int = Field(description="Total count")


class PlaylistTracksResponse(BaseModel):
    """Tracks within a specific playlist."""

    playlist_id: str = Field(description="Service-specific playlist ID")
    service: str = Field(description="Source service")
    items: list[PlaylistTrackOut] = Field(description="Tracks in order")
    total: int = Field(description="Total tracks")


# ── Playlist brief (V 6.440) ────────────────────────────────────────────────


class BriefMentionedSong(BaseModel):
    """A song the user mentioned in their playlist brief."""

    catalog_id: str = Field(description="Service-specific track ID")
    isrc: str | None = Field(default=None, description="ISRC if known")
    role: str = Field(
        default="referenced",
        description="referenced | target_example | anti_example",
    )
    reason_text: str | None = Field(
        default=None,
        description="User's freeform 'why I like/dislike this' note",
    )


class CreateBriefRequest(BaseModel):
    """POST /api/playlists/{id}/brief body."""

    service: str = Field(description="apple_music | spotify")
    brief_text: str = Field(
        min_length=1, max_length=4000,
        description="Freeform 'what kind of music for this playlist' text",
    )
    mentioned_songs: list[BriefMentionedSong] = Field(
        default_factory=list,
        description="Songs the user referenced inline in the brief",
    )


class BriefResponse(BaseModel):
    """Persisted playlist brief (target_vector populated once synthesized)."""

    id: str
    playlist_id: str
    service: str
    brief_text: str
    mentioned_songs: list[BriefMentionedSong]
    target_vector: dict | None = None
    synthesis_error: str | None = None
    created_at: str
    updated_at: str
