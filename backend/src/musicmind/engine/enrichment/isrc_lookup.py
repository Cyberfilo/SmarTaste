"""ISRC lookup via free APIs — Deezer (fast) then MusicBrainz (reliable).

Resolves missing ISRC codes for songs in song_metadata_cache and global_song_cache.
ISRC is the global dedup key for sharing enrichment across users via
audio_features_global and audio_embeddings_global.

Both APIs are free and require no authentication keys.

Rate limits:
- Deezer: ~50 req/sec (we cap at 10 concurrent)
- MusicBrainz: 1 req/sec (enforced via asyncio.Lock + sleep)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

DEEZER_API = "https://api.deezer.com"
MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
MUSICBRAINZ_USER_AGENT = "SmarTaste/6.100 (filo.gametech@gmail.com)"

# MusicBrainz requires 1 req/sec max — track last request time globally
_mb_lock = asyncio.Lock()
_mb_last_request: float = 0.0

# Deezer concurrency cap
_deezer_sem = asyncio.Semaphore(10)

# Valid ISRC pattern: 2-letter country + 3-char registrant + 2-digit year + 5-digit designation
_ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{2}\d{5}$")


def _is_valid_isrc(value: str | None) -> bool:
    """Check if a string looks like a valid ISRC."""
    if not value:
        return False
    return bool(_ISRC_RE.match(value.strip().upper()))


async def _deezer_lookup(name: str, artist_name: str) -> str | None:
    """Search Deezer for a track and return its ISRC.

    Strategy: search by artist + track name, then fetch full track details
    (the search endpoint doesn't always include ISRC).
    """
    async with _deezer_sem:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Search for the track
                query = f'artist:"{artist_name}" track:"{name}"'
                resp = await client.get(
                    f"{DEEZER_API}/search/track",
                    params={"q": query, "limit": 1},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("data", [])
                if not items:
                    return None

                track_id = items[0].get("id")
                if not track_id:
                    return None

                # Fetch full track details for ISRC
                detail_resp = await client.get(f"{DEEZER_API}/track/{track_id}")
                detail_resp.raise_for_status()
                detail = detail_resp.json()

                isrc = detail.get("isrc")
                if _is_valid_isrc(isrc):
                    return isrc.strip().upper()

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                logger.debug("Deezer rate limited during ISRC lookup")
            return None
        except httpx.HTTPError:
            return None

    return None


async def deezer_preview_by_isrc(isrc: str) -> str | None:
    """Get a fresh Deezer 30s preview URL for a track by its ISRC.

    Uses the direct /track/isrc:{ISRC} endpoint — no search needed.
    Returns the preview MP3 URL or None if the track isn't on Deezer.
    """
    if not isrc:
        return None
    async with _deezer_sem:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{DEEZER_API}/track/isrc:{isrc}")
                resp.raise_for_status()
                data = resp.json()
                if data.get("error"):
                    return None
                preview = data.get("preview")
                if preview and len(preview) > 10:
                    return preview
        except httpx.HTTPError:
            return None
    return None


async def _musicbrainz_lookup(name: str, artist_name: str) -> str | None:
    """Search MusicBrainz for a recording and return its ISRC.

    MUST respect 1 req/sec rate limit (enforced via global lock + sleep).
    """
    global _mb_last_request

    async with _mb_lock:
        # Enforce 1 req/sec
        now = time.monotonic()
        elapsed = now - _mb_last_request
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                query = f'recording:"{name}" AND artist:"{artist_name}"'
                resp = await client.get(
                    f"{MUSICBRAINZ_API}/recording",
                    params={"query": query, "fmt": "json", "limit": 3},
                    headers={"User-Agent": MUSICBRAINZ_USER_AGENT},
                )
                _mb_last_request = time.monotonic()

                resp.raise_for_status()
                data = resp.json()

                recordings = data.get("recordings", [])
                for recording in recordings:
                    isrcs = recording.get("isrcs", [])
                    if isrcs:
                        isrc = isrcs[0]
                        if _is_valid_isrc(isrc):
                            return isrc.strip().upper()

        except httpx.HTTPStatusError as exc:
            _mb_last_request = time.monotonic()
            if exc.response.status_code == 429:
                logger.debug("MusicBrainz rate limited during ISRC lookup")
            elif exc.response.status_code == 503:
                logger.debug("MusicBrainz service unavailable")
            return None
        except httpx.HTTPError:
            _mb_last_request = time.monotonic()
            return None

    return None


async def lookup_isrc(
    name: str,
    artist_name: str,
    *,
    existing_isrc: str | None = None,
) -> str | None:
    """Look up ISRC for a track using free APIs. Returns ISRC or None.

    Tries Deezer first (faster, less restrictive), then MusicBrainz as fallback.

    Args:
        name: Track title.
        artist_name: Primary artist name.
        existing_isrc: If already valid, returned immediately (skip lookup).

    Returns:
        ISRC string (uppercased, e.g. "USAT21234567") or None.
    """
    # Short-circuit if already valid
    if _is_valid_isrc(existing_isrc):
        return existing_isrc.strip().upper()

    if not name or not artist_name:
        return None

    # Try Deezer first (faster, higher rate limit)
    isrc = await _deezer_lookup(name, artist_name)
    if isrc:
        return isrc

    # Fallback to MusicBrainz (slower but more reliable for niche tracks)
    isrc = await _musicbrainz_lookup(name, artist_name)
    if isrc:
        return isrc

    return None
