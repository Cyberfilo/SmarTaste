"""Deezer API client for BPM + preview URL enrichment.

Free API, no auth required. Rate limit ~10 req/s with exponential backoff.
Uses search-by-name (ISRC lookup is broken/deprecated).
Returns BPM and 30s preview MP3 URL.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEEZER_API = "https://api.deezer.com"
MAX_RETRIES = 3
BACKOFF_BASE = 0.5


async def fetch_deezer_features(
    *,
    name: str,
    artist_name: str,
    isrc: str | None = None,
) -> dict[str, Any] | None:
    """Fetch BPM and preview URL from Deezer by searching title + artist.

    Deezer's ISRC endpoint is broken, so we use search instead.
    Returns BPM (if available) and a 30s preview MP3 URL.

    Args:
        name: Track title.
        artist_name: Primary artist name.
        isrc: Not used for lookup (kept for interface compat).

    Returns:
        Dict with tempo (BPM), preview_url, deezer_id. None on miss.
    """
    if not name or not artist_name:
        return None

    query = f"{name} {artist_name}"

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Step 1: Search by title + artist
                resp = await client.get(
                    f"{DEEZER_API}/search",
                    params={"q": query, "limit": 1},
                )
                resp.raise_for_status()
                items = resp.json().get("data", [])
                if not items:
                    return None

                deezer_id = items[0].get("id")
                if not deezer_id:
                    return None

                # Step 2: Get full track details (includes BPM)
                resp2 = await client.get(f"{DEEZER_API}/track/{deezer_id}")
                resp2.raise_for_status()
                full = resp2.json()

                if "error" in full:
                    return None

                result: dict[str, Any] = {"deezer_id": str(deezer_id)}

                bpm = full.get("bpm")
                if bpm and bpm > 0:
                    result["tempo"] = float(bpm)

                preview = full.get("preview")
                if preview:
                    result["preview_url"] = preview

                deezer_isrc = full.get("isrc")
                if deezer_isrc:
                    result["isrc"] = deezer_isrc

                return result if len(result) > 1 else None

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                wait = BACKOFF_BASE * (2**attempt)
                logger.warning("Deezer rate limited, waiting %.1fs", wait)
                await asyncio.sleep(wait)
                continue
            logger.warning("Deezer HTTP error: %s", exc)
            return None
        except httpx.HTTPError:
            logger.warning("Deezer connection error for '%s'", query[:50])
            return None

    return None
