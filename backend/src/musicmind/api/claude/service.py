"""BYOK Claude API key management service helpers.

Provides functions for storing, retrieving, deleting, validating, and masking
user-provided Anthropic API keys, plus static cost estimation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import anthropic
import sqlalchemy as sa

from musicmind.db.schema import user_api_keys
from musicmind.security.encryption import EncryptionService

logger = logging.getLogger(__name__)

# ── Pure Functions ───────────────────────────────────────────────────────────


def mask_api_key(api_key: str) -> str:
    """Return a masked preview of an API key.

    Shows "sk-ant-...{last4}" for standard keys, or "****" for very short keys.

    Args:
        api_key: Full plaintext API key.

    Returns:
        Masked string safe for display.
    """
    if len(api_key) < 8:
        return "****"
    return f"sk-ant-...{api_key[-4:]}"


def estimate_chat_cost() -> dict:
    """Return static cost estimate for Claude API usage.

    Based on Claude Sonnet 4 pricing as of 2025. This is a pure function
    with hardcoded current pricing, not real-time tracking.

    Returns:
        Dict with model, estimated_cost_per_message, input/output token prices.
    """
    return {
        "model": "claude-sonnet-4-20250514",
        "estimated_cost_per_message": "$0.01-0.05",
        "input_token_price": "$3.00 / 1M tokens",
        "output_token_price": "$15.00 / 1M tokens",
    }


# ── Async DB Operations ─────────────────────────────────────────────────────


async def store_api_key(
    engine,
    encryption: EncryptionService,
    *,
    user_id: str,
    api_key: str,
) -> None:
    """Encrypt and store an Anthropic API key for a user.

    Uses dialect-agnostic SELECT-then-INSERT/UPDATE within a transaction,
    compatible with both PostgreSQL and SQLite. Overwrites any existing key
    for the same user and service.

    Args:
        engine: SQLAlchemy async engine.
        encryption: EncryptionService for key encryption.
        user_id: MusicMind user ID.
        api_key: Plaintext Anthropic API key to encrypt and store.
    """
    encrypted_key = encryption.encrypt(api_key)
    now = datetime.now(UTC)

    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(user_api_keys.c.user_id).where(
                sa.and_(
                    user_api_keys.c.user_id == user_id,
                    user_api_keys.c.service == "anthropic",
                )
            )
        )
        row = existing.first()

        if row:
            logger.info("Updating existing API key for user %s", user_id)
            await conn.execute(
                user_api_keys.update()
                .where(
                    sa.and_(
                        user_api_keys.c.user_id == user_id,
                        user_api_keys.c.service == "anthropic",
                    )
                )
                .values(
                    api_key_encrypted=encrypted_key,
                    updated_at=now,
                )
            )
        else:
            logger.info("Storing new API key for user %s", user_id)
            await conn.execute(
                user_api_keys.insert().values(
                    user_id=user_id,
                    service="anthropic",
                    api_key_encrypted=encrypted_key,
                    created_at=now,
                    updated_at=now,
                )
            )


async def get_api_key_status(
    engine,
    encryption: EncryptionService,
    *,
    user_id: str,
) -> dict:
    """Check whether a user has a stored Claude API key.

    Args:
        engine: SQLAlchemy async engine.
        encryption: EncryptionService for key decryption (to generate masked preview).
        user_id: MusicMind user ID.

    Returns:
        Dict with configured (bool), masked_key (str|None), service (str).
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(user_api_keys.c.api_key_encrypted).where(
                sa.and_(
                    user_api_keys.c.user_id == user_id,
                    user_api_keys.c.service == "anthropic",
                )
            )
        )
        row = result.first()

    if not row:
        return {"configured": False, "masked_key": None, "service": "anthropic"}

    plaintext = encryption.decrypt(row.api_key_encrypted)
    masked = mask_api_key(plaintext)
    return {"configured": True, "masked_key": masked, "service": "anthropic"}


async def get_decrypted_api_key(
    engine,
    encryption: EncryptionService,
    *,
    user_id: str,
) -> str | None:
    """Retrieve and decrypt a user's stored Anthropic API key.

    Args:
        engine: SQLAlchemy async engine.
        encryption: EncryptionService for key decryption.
        user_id: MusicMind user ID.

    Returns:
        Plaintext API key, or None if no key is stored.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(user_api_keys.c.api_key_encrypted).where(
                sa.and_(
                    user_api_keys.c.user_id == user_id,
                    user_api_keys.c.service == "anthropic",
                )
            )
        )
        row = result.first()

    if not row:
        return None

    return encryption.decrypt(row.api_key_encrypted)


async def delete_api_key(
    engine,
    *,
    user_id: str,
) -> bool:
    """Delete a user's stored Anthropic API key.

    Args:
        engine: SQLAlchemy async engine.
        user_id: MusicMind user ID.

    Returns:
        True if a key was deleted, False if no key existed.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            user_api_keys.delete().where(
                sa.and_(
                    user_api_keys.c.user_id == user_id,
                    user_api_keys.c.service == "anthropic",
                )
            )
        )
        deleted = result.rowcount > 0
        if deleted:
            logger.info("Deleted API key for user %s", user_id)
        else:
            logger.info("No API key found for user %s", user_id)
        return deleted


# ── Async API Validation ────────────────────────────────────────────────────


async def validate_anthropic_key(api_key: str) -> dict:
    """Validate an Anthropic API key by making a minimal API call.

    Creates an AsyncAnthropic client and calls messages.create with max_tokens=1
    to verify the key works without spending significant tokens.

    Args:
        api_key: Anthropic API key to validate.

    Returns:
        Dict with valid (bool) and error (str|None).
    """
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return {"valid": True, "error": None}
    except anthropic.AuthenticationError:
        return {"valid": False, "error": "Invalid API key"}
    except anthropic.APIError as e:
        return {"valid": False, "error": str(e)}
    except Exception as e:
        return {"valid": False, "error": f"Validation failed: {e}"}
