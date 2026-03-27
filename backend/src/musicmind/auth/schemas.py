"""Pydantic request/response models for auth endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    """Request body for user registration."""

    email: str = Field(description="User email address")
    password: str = Field(min_length=8, description="Password (minimum 8 characters)")
    display_name: str | None = Field(
        default=None, description="Display name (optional, defaults to email username)"
    )


class LoginRequest(BaseModel):
    """Request body for user login."""

    email: str = Field(description="User email address")
    password: str = Field(description="User password")


class UserResponse(BaseModel):
    """Response with user profile information."""

    user_id: str = Field(description="User ID")
    email: str = Field(description="User email")
    display_name: str = Field(description="Display name")


class AuthResponse(BaseModel):
    """Response after successful authentication."""

    user_id: str = Field(description="User ID")
    email: str = Field(description="User email")
    message: str = Field(default="success", description="Status message")
