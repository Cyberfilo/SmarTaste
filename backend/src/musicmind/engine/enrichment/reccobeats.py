"""ReccoBeats audio analysis API — upload audio, get features back.

Free, no auth. POST multipart/form-data with audio file (max 5MB, 30s).
Returns: acousticness, danceability, energy, instrumentalness, liveness,
loudness, speechiness, tempo, valence.

Retry logic with Retry-After handling is in the orchestrator.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RECCOBEATS_API = "https://api.reccobeats.com/v1/analysis/audio-features"


async def analyze_audio_features(audio_bytes: bytes) -> dict[str, Any] | None:
    """Upload audio to ReccoBeats and get audio features back.

    Returns a dict with features mapped to our schema. None on error.
    Caller handles retries and Retry-After.
    """
    if not audio_bytes or len(audio_bytes) < 1000:
        return None

    if len(audio_bytes) > 5 * 1024 * 1024:
        audio_bytes = audio_bytes[:5 * 1024 * 1024]

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            files = {"audioFile": ("preview.mp3", audio_bytes, "audio/mpeg")}
            resp = await client.post(RECCOBEATS_API, files=files)

            if resp.status_code == 429:
                # Let caller handle retry with Retry-After header
                retry_after = resp.headers.get("Retry-After", "2")
                logger.info("ReccoBeats 429, Retry-After: %s", retry_after)
                return None

            resp.raise_for_status()
            return resp.json()

    except httpx.ReadTimeout:
        logger.debug("ReccoBeats timeout")
        return None
    except httpx.HTTPError:
        logger.debug("ReccoBeats connection error")
        return None
    except Exception:
        logger.debug("ReccoBeats unexpected error")
        return None
