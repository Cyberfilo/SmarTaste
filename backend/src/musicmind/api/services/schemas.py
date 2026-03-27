"""Pydantic request/response models for service connection endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServiceConnectionResponse(BaseModel):
    """Status of a single service connection."""

    service: str = Field(description="Service name (spotify or apple_music)")
    status: str = Field(description="Connection status: connected, expired, or not_connected")
    service_user_id: str | None = Field(
        default=None, description="User ID on the external service"
    )
    connected_at: str | None = Field(default=None, description="ISO timestamp of connection")


class ServiceListResponse(BaseModel):
    """List of all service connections for the current user."""

    services: list[ServiceConnectionResponse] = Field(
        description="All service connections with status"
    )


class SpotifyConnectResponse(BaseModel):
    """Response from Spotify connect initiation with OAuth authorize URL."""

    authorize_url: str = Field(description="Spotify OAuth authorize URL to redirect user to")


class AppleMusicConnectRequest(BaseModel):
    """Request body for connecting Apple Music with a Music User Token."""

    music_user_token: str = Field(
        min_length=1, description="Music User Token from MusicKit JS authorize()"
    )


class AppleMusicDeveloperTokenResponse(BaseModel):
    """Response containing the Apple Music developer token for MusicKit JS."""

    developer_token: str = Field(description="ES256-signed Apple Developer Token")


class DisconnectResponse(BaseModel):
    """Response after disconnecting a service."""

    message: str = Field(description="Confirmation message")
