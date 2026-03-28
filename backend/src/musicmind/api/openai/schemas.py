"""Pydantic request/response models for BYOK OpenAI API key management endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StoreKeyRequest(BaseModel):
    """Request body for storing a user's OpenAI API key."""

    api_key: str = Field(
        min_length=1,
        description="OpenAI API key (sk-...)",
    )


class KeyStatusResponse(BaseModel):
    """Response indicating whether an OpenAI API key is configured."""

    configured: bool = Field(description="Whether an API key is stored for this user")
    masked_key: str | None = Field(
        default=None,
        description="Masked preview of the stored key (e.g., sk-...XXXX)",
    )
    service: str = Field(
        default="openai",
        description="AI service provider name",
    )


class ValidateKeyResponse(BaseModel):
    """Response from validating an OpenAI API key."""

    valid: bool = Field(description="Whether the API key is valid and active")
    error: str | None = Field(
        default=None,
        description="Error message if validation failed",
    )


class CostEstimateResponse(BaseModel):
    """Approximate cost information for OpenAI GPT-4o API usage."""

    model: str = Field(
        default="gpt-4o",
        description="OpenAI model used for chat",
    )
    estimated_cost_per_message: str = Field(
        description="Approximate dollar range per message",
    )
    input_token_price: str = Field(
        description="Price per 1M input tokens",
    )
    output_token_price: str = Field(
        description="Price per 1M output tokens",
    )
