"""MusicBrainz ISRC → Spotify ID resolver.

Free API, 1 req/s rate limit, requires proper User-Agent.
Results cached permanently in isrc_spotify_mapping table.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import sqlalchemy as sa

from musicmind.db.schema import isrc_spotify_mapping

logger = logging.getLogger(__name__)

MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
USER_AGENT = "SmarTaste/0.1.0 (https://music.menghi.dev)"

# Global rate limiter: 1 request per second
_last_request_time = 0.0


async def resolve_spotify_id(
    isrc: str,
    *,
    engine: Any | None = None,
) -> str | None:
    """Resolve an ISRC to a Spotify track ID via MusicBrainz.

    Checks the local isrc_spotify_mapping cache first. If not cached,
    queries MusicBrainz for recordings matching the ISRC, then scans
    external links for Spotify URLs.

    Args:
        isrc: International Standard Recording Code.
        engine: SQLAlchemy async engine for cache lookup/storage.

    Returns:
        Spotify track ID (e.g. "4iV5W9uYEdYUVa79Axb7Rh") or None.
    """
    if not isrc:
        return None

    # Check cache first
    if engine is not None:
        cached = await _get_cached_mapping(engine, isrc)
        if cached is not None:
            return cached  # May be "" (negative cache)

    # Query MusicBrainz
    spotify_id = await _query_musicbrainz(isrc)

    # Cache result (including negative results to avoid re-querying)
    if engine is not None:
        await _store_mapping(engine, isrc, spotify_id or "")

    return spotify_id


async def _get_cached_mapping(engine: Any, isrc: str) -> str | None:
    """Check isrc_spotify_mapping for a cached result.

    Returns the spotify_id string (may be empty for negative cache),
    or None if not cached at all.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(isrc_spotify_mapping.c.spotify_id).where(
                isrc_spotify_mapping.c.isrc == isrc.upper()
            )
        )
        row = result.first()
    if row is None:
        return None
    return row.spotify_id or None


async def _store_mapping(
    engine: Any,
    isrc: str,
    spotify_id: str,
) -> None:
    """Store an ISRC → Spotify ID mapping permanently."""
    async with engine.begin() as conn:
        # Upsert: insert or update on conflict
        stmt = sa.text(
            "INSERT INTO isrc_spotify_mapping (isrc, spotify_id, resolved_via, resolved_at) "
            "VALUES (:isrc, :spotify_id, :resolved_via, NOW()) "
            "ON CONFLICT (isrc) DO UPDATE SET "
            "spotify_id = :spotify_id, resolved_via = :resolved_via, resolved_at = NOW()"
        )
        await conn.execute(
            stmt,
            {
                "isrc": isrc.upper(),
                "spotify_id": spotify_id,
                "resolved_via": "musicbrainz" if spotify_id else "miss",
            },
        )


async def _query_musicbrainz(isrc: str) -> str | None:
    """Query MusicBrainz for a recording matching the ISRC, extract Spotify ID."""
    global _last_request_time

    # Rate limit: 1 request per second
    now = asyncio.get_event_loop().time()
    elapsed = now - _last_request_time
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            _last_request_time = asyncio.get_event_loop().time()
            resp = await client.get(
                f"{MUSICBRAINZ_API}/recording/",
                params={"query": f"isrc:{isrc}", "fmt": "json", "limit": 5},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

            recordings = data.get("recordings", [])
            if not recordings:
                return None

            # Check each recording for Spotify external links
            for recording in recordings[:3]:
                recording_id = recording.get("id")
                if not recording_id:
                    continue

                spotify_id = await _get_spotify_link(client, recording_id)
                if spotify_id:
                    return spotify_id

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 503:
            logger.warning("MusicBrainz rate limited for ISRC %s", isrc)
        else:
            logger.warning("MusicBrainz HTTP error for ISRC %s: %s", isrc, exc)
    except httpx.HTTPError:
        logger.warning("MusicBrainz connection error for ISRC %s", isrc)

    return None


async def _get_spotify_link(
    client: httpx.AsyncClient,
    recording_id: str,
) -> str | None:
    """Get Spotify track ID from a MusicBrainz recording's external links."""
    global _last_request_time

    # Rate limit
    now = asyncio.get_event_loop().time()
    elapsed = now - _last_request_time
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)

    try:
        _last_request_time = asyncio.get_event_loop().time()
        resp = await client.get(
            f"{MUSICBRAINZ_API}/recording/{recording_id}",
            params={"inc": "url-rels", "fmt": "json"},
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()

        for rel in data.get("relations", []):
            url_data = rel.get("url", {})
            resource = url_data.get("resource", "")
            # Spotify URLs look like: https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh
            if "open.spotify.com/track/" in resource:
                spotify_id = resource.split("/track/")[-1].split("?")[0]
                if spotify_id:
                    return spotify_id

    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.debug("Failed to get Spotify link for recording %s", recording_id)

    return None
