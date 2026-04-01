"""Pydantic schemas for calibration API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AlbumOut(BaseModel):
    album_id: str
    name: str
    artist_name: str
    artwork_url: str
    track_count: int = 0
    release_date: str = ""
    service: str = ""


class AlbumsResponse(BaseModel):
    albums: list[AlbumOut]
    total: int


class ArtistCalibrationEntry(BaseModel):
    name: str
    score: float
    song_count: int


class ArtistsResponse(BaseModel):
    artists: list[ArtistCalibrationEntry]
    service: str = ""


class CalibrationItem(BaseModel):
    calibration_type: str = Field(
        description="One of: album, artist_confirm, artist_reject, playlist_song"
    )
    item_id: str = Field(description="Album ID, artist name, or catalog_id")
    item_name: str = Field(default="", description="Human-readable name for display")
    weight: float = Field(default=1.0, description="Weight multiplier for profile")


class SaveCalibrationRequest(BaseModel):
    items: list[CalibrationItem] = Field(description="All calibration selections")


class CalibrationStatusResponse(BaseModel):
    completed: bool
    item_count: int
