"""ReccoBeats audio analysis API — upload audio, get features back.

Free, no auth. POST multipart/form-data with audio file (max 5MB, 30s).
Returns: acousticness, danceability, energy, instrumentalness, liveness,
loudness, speechiness, tempo, valence.

https://reccobeats.com/docs/documentation/Analysis/audio-features-extraction
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RECCOBEATS_API = "https://api.reccobeats.com/v1/analysis/audio-features"


async def analyze_audio_features(audio_bytes: bytes) -> dict[str, Any] | None:
    """Upload audio to ReccoBeats and get audio features back.

    Args:
        audio_bytes: Raw audio file content (MP3, max 5MB/30s).

    Returns:
        Dict with all 9 features mapped to our schema names. None on error.
    """
    if not audio_bytes or len(audio_bytes) < 1000:
        return None

    # Cap at 5MB
    if len(audio_bytes) > 5 * 1024 * 1024:
        audio_bytes = audio_bytes[:5 * 1024 * 1024]

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            files = {"audioFile": ("preview.mp3", audio_bytes, "audio/mpeg")}
            resp = await client.post(RECCOBEATS_API, files=files)
            resp.raise_for_status()
            data = resp.json()

            result: dict[str, Any] = {}

            # Map ReccoBeats fields to our schema
            if data.get("tempo") is not None and data["tempo"] > 0:
                result["tempo"] = round(float(data["tempo"]), 2)
            if data.get("energy") is not None:
                result["energy"] = round(float(data["energy"]), 4)
            if data.get("danceability") is not None:
                result["danceability"] = round(float(data["danceability"]), 4)
            if data.get("acousticness") is not None:
                result["acousticness"] = round(float(data["acousticness"]), 4)
            if data.get("valence") is not None:
                result["valence_proxy"] = round(float(data["valence"]), 4)
            if data.get("instrumentalness") is not None:
                result["instrumentalness"] = round(float(data["instrumentalness"]), 4)
            if data.get("loudness") is not None:
                # Normalize loudness from dB (-60..0) to 0..1
                raw = float(data["loudness"])
                result["loudness"] = round(max(0.0, min(1.0, (raw + 60) / 60)), 4)
            if data.get("speechiness") is not None:
                result["speechiness"] = round(float(data["speechiness"]), 4)
            if data.get("liveness") is not None:
                result["liveness"] = round(float(data["liveness"]), 4)

            return result if result else None

    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 429:
            logger.info("ReccoBeats rate limited (429)")
        else:
            logger.warning("ReccoBeats HTTP error: %s", code)
        return None
    except httpx.ReadTimeout:
        logger.info("ReccoBeats timeout — will retry")
        return None
    except httpx.HTTPError:
        logger.warning("ReccoBeats connection error")
        return None
    except Exception:
        logger.exception("ReccoBeats unexpected error")
        return None
