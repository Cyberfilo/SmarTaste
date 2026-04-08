"""Last.fm API client — tags + similar tracks for collaborative filtering.

Two endpoints:
1. track.getTopTags → crowd-sourced mood/vibe tags ("dark", "aggressive", "drill")
2. track.getSimilar → collaborative filtering from billions of scrobbles

Free API, key required, ~5 req/sec rate limit.
Results cached permanently in lastfm_tags_cache and lastfm_similar_tracks tables.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa

from musicmind.db.schema import lastfm_similar_tracks, lastfm_tags_cache

logger = logging.getLogger(__name__)

LASTFM_API = "https://ws.audioscrobbler.com/2.0/"

# Rate limiter: ~5 req/sec → 200ms between requests
_last_request_time = 0.0
_RATE_LIMIT_DELAY = 0.2


async def _rate_limited_get(
    client: httpx.AsyncClient,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """Make a rate-limited GET request to Last.fm API."""
    global _last_request_time

    now = asyncio.get_event_loop().time()
    elapsed = now - _last_request_time
    if elapsed < _RATE_LIMIT_DELAY:
        await asyncio.sleep(_RATE_LIMIT_DELAY - elapsed)

    try:
        _last_request_time = asyncio.get_event_loop().time()
        resp = await client.get(LASTFM_API, params=params)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPStatusError, httpx.HTTPError) as exc:
        logger.debug("Last.fm API error: %s", exc)
        return None


# ── Tags ──────────────────────────────────────────────────────────────────


async def fetch_track_tags(
    api_key: str,
    artist: str,
    title: str,
    *,
    engine: Any | None = None,
) -> dict[str, float] | None:
    """Fetch top tags for a track from Last.fm.

    Returns dict of {tag_name: weight} (normalized 0-1).
    Checks cache first, stores result permanently.
    """
    if not api_key or not artist or not title:
        return None

    entity_id = f"track:{artist.lower()}:{title.lower()}"

    # Check cache
    if engine:
        cached = await _get_cached_tags(engine, entity_id)
        if cached is not None:
            return cached

    # Fetch from API
    async with httpx.AsyncClient(timeout=15.0) as client:
        data = await _rate_limited_get(client, {
            "method": "track.getTopTags",
            "artist": artist,
            "track": title,
            "api_key": api_key,
            "format": "json",
        })

    if not data:
        return None

    # Parse tags
    raw_tags = data.get("toptags", {}).get("tag", [])
    if not raw_tags:
        # Try artist tags as fallback
        return await fetch_artist_tags(api_key, artist, engine=engine)

    tags = {}
    for tag_entry in raw_tags[:20]:
        name = tag_entry.get("name", "").lower().strip()
        count = int(tag_entry.get("count", 0))
        if name and count > 0:
            tags[name] = count

    # Normalize to 0-1
    if tags:
        max_count = max(tags.values())
        tags = {k: round(v / max_count, 3) for k, v in tags.items()}

    # Cache
    if engine and tags:
        await _store_tags(engine, entity_id, tags)

    return tags or None


async def fetch_artist_tags(
    api_key: str,
    artist: str,
    *,
    engine: Any | None = None,
) -> dict[str, float] | None:
    """Fetch top tags for an artist from Last.fm (fallback when track tags empty)."""
    if not api_key or not artist:
        return None

    entity_id = f"artist:{artist.lower()}"

    if engine:
        cached = await _get_cached_tags(engine, entity_id)
        if cached is not None:
            return cached

    async with httpx.AsyncClient(timeout=15.0) as client:
        data = await _rate_limited_get(client, {
            "method": "artist.getTopTags",
            "artist": artist,
            "api_key": api_key,
            "format": "json",
        })

    if not data:
        return None

    raw_tags = data.get("toptags", {}).get("tag", [])
    tags = {}
    for tag_entry in raw_tags[:20]:
        name = tag_entry.get("name", "").lower().strip()
        count = int(tag_entry.get("count", 0))
        if name and count > 0:
            tags[name] = count

    if tags:
        max_count = max(tags.values())
        tags = {k: round(v / max_count, 3) for k, v in tags.items()}

    if engine and tags:
        await _store_tags(engine, entity_id, tags)

    return tags or None


# ── Similar Tracks ────────────────────────────────────────────────────────


async def fetch_similar_tracks(
    api_key: str,
    artist: str,
    title: str,
    *,
    engine: Any | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch similar tracks from Last.fm (collaborative filtering).

    Returns list of {artist, title, similarity_score, mbid}.
    Stores results in lastfm_similar_tracks table.
    """
    if not api_key or not artist or not title:
        return []

    # Check cache
    if engine:
        cached = await _get_cached_similar(engine, artist, title)
        if cached:
            return cached

    async with httpx.AsyncClient(timeout=15.0) as client:
        data = await _rate_limited_get(client, {
            "method": "track.getSimilar",
            "artist": artist,
            "track": title,
            "api_key": api_key,
            "limit": limit,
            "format": "json",
        })

    if not data:
        return []

    similar_tracks = data.get("similartracks", {}).get("track", [])
    results: list[dict[str, Any]] = []

    for track in similar_tracks:
        sim_artist = track.get("artist", {})
        if isinstance(sim_artist, dict):
            sim_artist_name = sim_artist.get("name", "")
        else:
            sim_artist_name = str(sim_artist)

        sim_title = track.get("name", "")
        match_score = float(track.get("match", 0))
        mbid = track.get("mbid", "")

        if sim_artist_name and sim_title and match_score > 0:
            results.append({
                "artist": sim_artist_name,
                "title": sim_title,
                "similarity_score": round(match_score, 4),
                "mbid": mbid,
            })

    # Store in DB
    if engine and results:
        await _store_similar(engine, artist, title, results)

    return results


# ── Cache Helpers ─────────────────────────────────────────────────────────


async def _get_cached_tags(engine: Any, entity_id: str) -> dict[str, float] | None:
    """Check lastfm_tags_cache for existing tags."""
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(lastfm_tags_cache.c.tags).where(
                sa.and_(
                    lastfm_tags_cache.c.entity_type == entity_id.split(":")[0],
                    lastfm_tags_cache.c.entity_id == entity_id,
                )
            )
        )
        row = result.first()

    if row is None:
        return None

    tags = row.tags
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            return None
    return tags if tags else None


async def _store_tags(engine: Any, entity_id: str, tags: dict[str, float]) -> None:
    """Store tags in lastfm_tags_cache."""
    entity_type = entity_id.split(":")[0]
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO lastfm_tags_cache (entity_type, entity_id, tags, fetched_at) "
            "VALUES (:entity_type, :entity_id, :tags, :fetched_at) "
            "ON CONFLICT (entity_type, entity_id) DO UPDATE SET "
            "tags = :tags, fetched_at = :fetched_at"
        ), {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "tags": json.dumps(tags),
            "fetched_at": datetime.now(UTC),
        })


async def _get_cached_similar(
    engine: Any, artist: str, title: str,
) -> list[dict[str, Any]]:
    """Check lastfm_similar_tracks cache."""
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(lastfm_similar_tracks).where(
                sa.and_(
                    sa.func.lower(lastfm_similar_tracks.c.source_artist) == artist.lower(),
                    sa.func.lower(lastfm_similar_tracks.c.source_title) == title.lower(),
                )
            ).limit(20)
        )
        rows = result.fetchall()

    return [
        {
            "artist": r.similar_artist,
            "title": r.similar_title,
            "similarity_score": r.similarity_score,
            "mbid": r.similar_mbid or "",
        }
        for r in rows
    ]


async def _store_similar(
    engine: Any, artist: str, title: str,
    similar: list[dict[str, Any]],
) -> None:
    """Store similar tracks in lastfm_similar_tracks."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        # Clear old entries for this source
        await conn.execute(
            lastfm_similar_tracks.delete().where(
                sa.and_(
                    sa.func.lower(lastfm_similar_tracks.c.source_artist) == artist.lower(),
                    sa.func.lower(lastfm_similar_tracks.c.source_title) == title.lower(),
                )
            )
        )
        # Insert new
        for s in similar:
            await conn.execute(
                lastfm_similar_tracks.insert().values(
                    source_artist=artist,
                    source_title=title,
                    similar_artist=s["artist"],
                    similar_title=s["title"],
                    similarity_score=s["similarity_score"],
                    similar_mbid=s.get("mbid", ""),
                    fetched_at=now,
                )
            )
