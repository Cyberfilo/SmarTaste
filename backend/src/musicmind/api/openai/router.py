"""BYOK OpenAI API key management endpoints.

Provides REST endpoints for storing, validating, deleting, and checking
the status of user-provided OpenAI API keys, plus cost estimation.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from musicmind.api.openai.schemas import (
    CostEstimateResponse,
    KeyStatusResponse,
    StoreKeyRequest,
    ValidateKeyResponse,
)
from musicmind.api.openai.service import (
    delete_api_key,
    estimate_chat_cost,
    get_api_key_status,
    get_decrypted_api_key,
    store_api_key,
    validate_openai_key,
)
from musicmind.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/openai", tags=["openai"])
logger = logging.getLogger(__name__)


@router.post("/key", status_code=status.HTTP_201_CREATED)
async def store_key(
    request: Request,
    body: StoreKeyRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Store or update a user's OpenAI API key.

    Encrypts the key at rest and overwrites any previously stored key
    for the same user. No key history is maintained.
    """
    engine = request.app.state.engine
    encryption = request.app.state.encryption

    await store_api_key(
        engine,
        encryption,
        user_id=current_user["user_id"],
        api_key=body.api_key,
    )

    logger.info("OpenAI API key stored for user %s", current_user["user_id"])
    return {"message": "API key stored"}


@router.get("/key/status")
async def key_status(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> KeyStatusResponse:
    """Check whether the authenticated user has a stored OpenAI API key.

    Returns configured=true with a masked preview of the key if one exists,
    or configured=false with no masked key otherwise.
    """
    engine = request.app.state.engine
    encryption = request.app.state.encryption

    status_dict = await get_api_key_status(
        engine,
        encryption,
        user_id=current_user["user_id"],
    )

    return KeyStatusResponse(**status_dict)


@router.post("/key/validate")
async def validate_key(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> ValidateKeyResponse:
    """Validate the stored OpenAI API key by making a minimal API call.

    Retrieves the user's decrypted key and calls the OpenAI API with
    max_tokens=1 to verify the key is valid without spending significant tokens.
    Returns valid=false with an error message if no key is stored.
    """
    engine = request.app.state.engine
    encryption = request.app.state.encryption

    api_key = await get_decrypted_api_key(
        engine,
        encryption,
        user_id=current_user["user_id"],
    )

    if api_key is None:
        return ValidateKeyResponse(valid=False, error="No API key configured")

    result = await validate_openai_key(api_key)
    return ValidateKeyResponse(**result)


@router.delete("/key")
async def delete_key(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Remove the authenticated user's stored OpenAI API key.

    Returns 404 if no key exists for the user.
    """
    engine = request.app.state.engine

    deleted = await delete_api_key(engine, user_id=current_user["user_id"])

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key configured",
        )

    logger.info("OpenAI API key removed for user %s", current_user["user_id"])
    return {"message": "API key removed"}


@router.get("/key/cost")
async def cost_estimate(
    current_user: dict = Depends(get_current_user),
) -> CostEstimateResponse:
    """Return static pricing estimate for OpenAI GPT-4o API usage.

    Based on GPT-4o pricing. This is informational only,
    not real-time cost tracking.
    """
    cost_dict = estimate_chat_cost()
    return CostEstimateResponse(**cost_dict)
