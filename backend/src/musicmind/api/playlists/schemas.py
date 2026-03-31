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
