"""Last.fm tag enrichment — fetch and cache top tags per track/artist.

Integrates as a secondary genre signal: 70% embedding similarity + 30%
Last.fm Jaccard tag overlap. Respects Last.fm rate limit (5 req/sec).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa

from musicmind.db.schema import lastfm_tags_cache

logger = logging.getLogger(__name__)

LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
MAX_TAGS = 10
RATE_LIMIT_DELAY = 0.2  # 5 req/sec

# Tag weight normalization
TAG_WEIGHT_SCALE = 100.0


async def fetch_track_tags(
    api_key: str,
    *,
    artist: str,
    track: str,
) -> dict[str, float]:
    """Fetch top tags for a track from Last.fm.

    Args:
        api_key: Last.fm API key.
        artist: Artist name.
        track: Track name.

    Returns:
        Dict of {tag_name: weight} (normalized 0-1), or empty dict.
    """
    params = {
        "method": "track.getTopTags",
        "artist": artist,
        "track": track,
        "api_key": api_key,
        "format": "json",
    }
    return await _fetch_tags(params)


async def fetch_artist_tags(
    api_key: str,
    *,
    artist: str,
) -> dict[str, float]:
    """Fetch top tags for an artist from Last.fm.

    Args:
        api_key: Last.fm API key.
        artist: Artist name.

    Returns:
        Dict of {tag_name: weight} (normalized 0-1), or empty dict.
    """
    params = {
        "method": "artist.getTopTags",
        "artist": artist,
        "api_key": api_key,
        "format": "json",
    }
    return await _fetch_tags(params)


async def _fetch_tags(params: dict[str, str]) -> dict[str, float]:
    """Shared tag fetch logic with rate limiting."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(LASTFM_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.debug("Last.fm API request failed for %s", params.get("method"))
        return {}

    # Parse toptags response
    toptags = data.get("toptags", {})
    tags_list = toptags.get("tag", [])
    if not isinstance(tags_list, list):
        return {}

    result: dict[str, float] = {}
    for tag_data in tags_list[:MAX_TAGS]:
        name = tag_data.get("name", "").lower().strip()
        count = int(tag_data.get("count", 0))
        if name and count > 0:
            result[name] = min(1.0, count / TAG_WEIGHT_SCALE)

    # Rate limit
    await asyncio.sleep(RATE_LIMIT_DELAY)
    return result


def tag_jaccard_similarity(
    tags_a: dict[str, float],
    tags_b: dict[str, float],
) -> float:
    """Weighted Jaccard similarity between two tag sets.

    Uses min/max overlap weighted by tag importance.

    Args:
        tags_a: First tag set {name: weight}.
        tags_b: Second tag set {name: weight}.

    Returns:
        Similarity score 0-1.
    """
    if not tags_a or not tags_b:
        return 0.0

    all_keys = set(tags_a.keys()) | set(tags_b.keys())
    if not all_keys:
        return 0.0

    min_sum = sum(
        min(tags_a.get(k, 0.0), tags_b.get(k, 0.0)) for k in all_keys
    )
    max_sum = sum(
        max(tags_a.get(k, 0.0), tags_b.get(k, 0.0)) for k in all_keys
    )

    if max_sum == 0:
        return 0.0
    return min_sum / max_sum


def combined_genre_similarity(
    embedding_sim: float,
    tag_sim: float,
) -> float:
    """Blend embedding similarity with Last.fm tag overlap.

    Formula: 70% embedding + 30% Last.fm Jaccard.

    Args:
        embedding_sim: Embedding-based similarity (0-1).
        tag_sim: Last.fm tag Jaccard similarity (0-1).

    Returns:
        Combined similarity 0-1.
    """
    return 0.7 * embedding_sim + 0.3 * tag_sim


# ── Cache Operations ───────────────────────────────────────────────────────


async def get_cached_tags(
    engine,
    *,
    entity_type: str,
    entity_id: str,
) -> dict[str, float] | None:
    """Load cached tags from the database.

    Args:
        engine: SQLAlchemy async engine.
        entity_type: "track" or "artist".
        entity_id: Identifier (e.g., "artist_name" or "artist:track").

    Returns:
        Tag dict or None if not cached.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(lastfm_tags_cache.c.tags).where(
                sa.and_(
                    lastfm_tags_cache.c.entity_type == entity_type,
                    lastfm_tags_cache.c.entity_id == entity_id,
                )
            )
        )
        row = result.first()

    if row is None:
        return None

    tags = row.tags
    if isinstance(tags, str):
        tags = json.loads(tags)
    return tags if isinstance(tags, dict) else None


async def store_tags(
    engine,
    *,
    entity_type: str,
    entity_id: str,
    tags: dict[str, float],
) -> None:
    """Store fetched tags to the cache."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(lastfm_tags_cache.c.entity_type).where(
                sa.and_(
                    lastfm_tags_cache.c.entity_type == entity_type,
                    lastfm_tags_cache.c.entity_id == entity_id,
                )
            )
        )
        if existing.first():
            await conn.execute(
                lastfm_tags_cache.update()
                .where(
                    sa.and_(
                        lastfm_tags_cache.c.entity_type == entity_type,
                        lastfm_tags_cache.c.entity_id == entity_id,
                    )
                )
                .values(tags=json.dumps(tags), fetched_at=now)
            )
        else:
            await conn.execute(
                lastfm_tags_cache.insert().values(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    tags=json.dumps(tags),
                    fetched_at=now,
                )
            )


async def get_or_fetch_track_tags(
    engine,
    api_key: str,
    *,
    artist: str,
    track: str,
) -> dict[str, float]:
    """Get tags from cache or fetch from Last.fm API.

    Args:
        engine: SQLAlchemy async engine.
        api_key: Last.fm API key.
        artist: Artist name.
        track: Track name.

    Returns:
        Tag dict {name: weight}.
    """
    entity_id = f"{artist.lower()}:{track.lower()}"
    cached = await get_cached_tags(engine, entity_type="track", entity_id=entity_id)
    if cached is not None:
        return cached

    tags = await fetch_track_tags(api_key, artist=artist, track=track)
    if tags:
        await store_tags(engine, entity_type="track", entity_id=entity_id, tags=tags)
    return tags


async def get_or_fetch_artist_tags(
    engine,
    api_key: str,
    *,
    artist: str,
) -> dict[str, float]:
    """Get artist tags from cache or fetch from Last.fm API."""
    entity_id = artist.lower()
    cached = await get_cached_tags(engine, entity_type="artist", entity_id=entity_id)
    if cached is not None:
        return cached

    tags = await fetch_artist_tags(api_key, artist=artist)
    if tags:
        await store_tags(engine, entity_type="artist", entity_id=entity_id, tags=tags)
    return tags
