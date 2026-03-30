"""Deezer API client for BPM enrichment.

Free API, no auth required. Rate limit ~10 req/s with exponential backoff.
ISRC-native: https://api.deezer.com/track/isrc:{ISRC}
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEEZER_API_BASE = "https://api.deezer.com"
MAX_RETRIES = 3
BACKOFF_BASE = 0.5


async def fetch_deezer_features(isrc: str) -> dict[str, Any] | None:
    """Fetch audio features from Deezer by ISRC.

    Returns a partial feature dict with BPM (tempo) and preview_url.
    Returns None if the track is not found or on error.

    Args:
        isrc: International Standard Recording Code.

    Returns:
        Dict with keys: tempo, preview_url, deezer_id.  None on miss.
    """
    if not isrc:
        return None

    url = f"{DEEZER_API_BASE}/track/isrc:{isrc}"

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)

                if resp.status_code == 404:
                    return None

                # Deezer returns 200 with error object for not-found
                data = resp.json()
                if "error" in data:
                    logger.debug("Deezer miss for ISRC %s: %s", isrc, data["error"])
                    return None

                resp.raise_for_status()

                bpm = data.get("bpm")
                result: dict[str, Any] = {}

                if bpm and bpm > 0:
                    result["tempo"] = float(bpm)

                # Deezer provides 30s preview MP3 — useful for Essentia later
                preview = data.get("preview")
                if preview:
                    result["preview_url"] = preview

                # Store Deezer ID for potential future use
                deezer_id = data.get("id")
                if deezer_id:
                    result["deezer_id"] = str(deezer_id)

                return result if result else None

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                import asyncio

                wait = BACKOFF_BASE * (2**attempt)
                logger.warning("Deezer rate limited, waiting %.1fs", wait)
                await asyncio.sleep(wait)
                continue
            logger.warning("Deezer HTTP error for ISRC %s: %s", isrc, exc)
            return None
        except httpx.HTTPError:
            logger.warning("Deezer connection error for ISRC %s", isrc)
            return None

    logger.warning("Deezer max retries exceeded for ISRC %s", isrc)
    return None
