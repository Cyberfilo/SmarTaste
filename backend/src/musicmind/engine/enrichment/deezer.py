"""Deezer preview URL fallback — search by name when Apple Music lacks a preview.

Free API, no auth required. Rate limit ~10 req/s with exponential backoff.
Only used as a FALLBACK for preview URLs, not for audio features.
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


async def search_preview_url(
    *,
    name: str,
    artist_name: str,
    isrc: str | None = None,
) -> str | None:
    """Search Deezer for a 30s preview URL.

    Strategy: try ISRC lookup first (exact match), fall back to name search.

    Returns:
        Preview MP3 URL string, or None if not found.
    """
    if not name and not artist_name and not isrc:
        return None

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Strategy 1: ISRC direct lookup (exact match)
                if isrc:
                    try:
                        resp = await client.get(
                            f"{DEEZER_API}/2.0/track/isrc:{isrc}",
                        )
                        if resp.is_success:
                            data = resp.json()
                            if "error" not in data and data.get("preview"):
                                return data["preview"]
                    except Exception:
                        pass

                # Strategy 2: Name + artist search (fallback)
                if name and artist_name:
                    query = f"{name} {artist_name}"
                    resp = await client.get(
                        f"{DEEZER_API}/search",
                        params={"q": query, "limit": 1},
                    )
                    resp.raise_for_status()
                    items = resp.json().get("data", [])
                    if items:
                        preview = items[0].get("preview")
                        if preview:
                            return preview

                return None

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                wait = BACKOFF_BASE * (2**attempt)
                logger.debug("Deezer rate limited, waiting %.1fs", wait)
                await asyncio.sleep(wait)
                continue
            return None
        except httpx.HTTPError:
            return None

    return None
