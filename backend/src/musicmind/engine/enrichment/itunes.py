"""iTunes Search API — unauthenticated preview URL resolver.

Apple's public iTunes Search API indexes the same catalogue Apple Music
streams from, so for storefronts where Apple is the dominant service
(Italian rap / drill, most European pop) it fills the gaps Deezer's ISRC
lookup misses. Returns a 30-second MP3 preview URL on Apple's CDN — same
length and format as Deezer previews, so the Essentia extractor processes
it without any change.

No auth, no key, no quota visible to us. The unofficial IP-level rate
limit is ~20 requests/minute; we stay well under that since this only
fires for tracks Deezer couldn't resolve.

Reference: https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_TIMEOUT = 10.0
ITUNES_LIMIT = 5


async def search_preview_url(
    *,
    name: str,
    artist_name: str,
) -> str | None:
    """Find a 30-second preview URL for the given (name, artist) pair.

    Scores results by exact (name, artist) match first, then name-only, then
    first result with a non-empty `previewUrl`. Returns None on HTTP errors,
    empty results, or rate-limit responses — the caller treats this as
    "no preview available" and marks the track as needing re-attempt.
    """
    if not name or not artist_name:
        return None

    # iTunes ignores "&", commas, and parentheses to its benefit — no need to
    # pre-strip. Let it do lexicographic matching.
    query = f"{name} {artist_name}".strip()
    if not query:
        return None

    try:
        async with httpx.AsyncClient(timeout=ITUNES_TIMEOUT) as client:
            resp = await client.get(
                ITUNES_SEARCH_URL,
                params={
                    "term": query,
                    "entity": "song",
                    "limit": ITUNES_LIMIT,
                    "media": "music",
                },
            )
            resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.HTTPError, httpx.TimeoutException):
        logger.debug(
            "iTunes Search request failed for '%s' — '%s' (query=%s)",
            artist_name, name, quote(query),
        )
        return None

    try:
        results = resp.json().get("results", [])
    except Exception:
        return None

    if not results:
        return None

    name_lower = name.strip().lower()
    artist_lower = artist_name.strip().lower()

    # Tier 1: exact (title, artist) match
    for r in results:
        rn = (r.get("trackName") or "").strip().lower()
        ra = (r.get("artistName") or "").strip().lower()
        if rn == name_lower and ra == artist_lower:
            url = r.get("previewUrl")
            if url:
                return url

    # Tier 2: exact title match (artist may have slightly different casing /
    # feature parsing than what iTunes indexes)
    for r in results:
        rn = (r.get("trackName") or "").strip().lower()
        if rn == name_lower:
            url = r.get("previewUrl")
            if url:
                return url

    # Tier 3: title contains match (handles remaster / version suffixes)
    for r in results:
        rn = (r.get("trackName") or "").strip().lower()
        if name_lower and (name_lower in rn or rn in name_lower):
            url = r.get("previewUrl")
            if url:
                return url

    # Tier 4: any result with a previewUrl — last resort
    for r in results:
        url = r.get("previewUrl")
        if url:
            return url

    return None
