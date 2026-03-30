"""Pydantic models for playlist endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlaylistItemOut(BaseModel):
    """A single track in a playlist."""

    id: int = Field(description="Item ID")
    catalog_id: str = Field(description="Service-specific track ID")
    position: int = Field(description="Position in playlist (0-indexed)")
    name: str = Field(description="Track name")
    artist_name: str = Field(description="Primary artist")
    album_name: str = Field(description="Album name")
    artwork_url: str = Field(default="", description="Album artwork URL")
    genre_names: list[str] = Field(default_factory=list, description="Genre list")
    added_by: str = Field(default="user", description="Who added: user, ai_expand, ai_improve")


class PlaylistOut(BaseModel):
    """Playlist summary (without full track list)."""

    id: int = Field(description="Playlist ID")
    name: str = Field(description="Playlist name")
    description: str = Field(default="", description="User description")
    ai_context: str = Field(default="", description="AI context for recommendations")
    track_count: int = Field(default=0, description="Number of tracks")
    created_at: str = Field(description="ISO creation timestamp")
    updated_at: str | None = Field(default=None, description="ISO last update")


class PlaylistDetailOut(PlaylistOut):
    """Full playlist with tracks."""

    items: list[PlaylistItemOut] = Field(default_factory=list, description="Tracks in order")


class CreatePlaylistRequest(BaseModel):
    """Create a new playlist."""

    name: str = Field(description="Playlist name", min_length=1, max_length=200)
    description: str = Field(default="", description="Optional description")
    ai_context: str = Field(
        default="",
        description="Context for AI recommendations (e.g. 'chill Italian rap for late night drives')",
    )


class UpdatePlaylistRequest(BaseModel):
    """Update playlist metadata."""

    name: str | None = Field(default=None, description="New name")
    description: str | None = Field(default=None, description="New description")
    ai_context: str | None = Field(default=None, description="New AI context")


class AddTracksRequest(BaseModel):
    """Add tracks to a playlist."""

    catalog_ids: list[str] = Field(
        description="Track catalog IDs to add",
        min_length=1,
        max_length=50,
    )


class PlaylistListResponse(BaseModel):
    """List of user playlists."""

    playlists: list[PlaylistOut] = Field(description="User's playlists")
    total: int = Field(description="Total count")
