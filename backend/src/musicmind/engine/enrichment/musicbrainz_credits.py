"""MusicBrainz recording credits — producers, songwriters, engineers.

Expands on musicbrainz.py (ISRC → Spotify ID) to also fetch:
- Producer credits (relationship type: "producer")
- Songwriter credits (relationship type: "writer")
- Featured artist relationships

Stores relationships in kg_artists + kg_relationships tables.
1 req/sec rate limit (shared with musicbrainz.py).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa

from musicmind.db.schema import kg_artists, kg_relationships

logger = logging.getLogger(__name__)

MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
USER_AGENT = "SmarTaste/3.310 (https://music.menghi.dev)"

# Shared rate limiter with musicbrainz.py
_last_request_time = 0.0

# Relationship types we care about
CREDIT_TYPES = {
    "producer", "co-producer", "mix", "recording",
    "writer", "composer", "lyricist",
    "engineer", "mastering",
}


async def fetch_recording_credits(
    isrc: str,
    *,
    engine: Any,
) -> list[dict[str, Any]]:
    """Fetch producer/songwriter credits for a recording via ISRC.

    Returns list of {name, mbid, role, disambiguation} for each credit.
    Stores in kg_artists + kg_relationships tables.
    """
    global _last_request_time

    if not isrc:
        return []

    # Check if we already have relationships for this ISRC
    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(kg_relationships).where(
                kg_relationships.c.source_mbid == f"isrc:{isrc.upper()}"
            ).limit(1)
        )
        if existing.first():
            # Already fetched — load from cache
            result = await conn.execute(
                sa.select(kg_relationships, kg_artists.c.name).where(
                    sa.and_(
                        kg_relationships.c.source_mbid == f"isrc:{isrc.upper()}",
                        kg_relationships.c.target_mbid == kg_artists.c.mbid,
                    )
                )
            )
            return [
                {
                    "name": row.name,
                    "mbid": row.target_mbid,
                    "role": row.relation_type,
                }
                for row in result
            ]

    # Rate limit
    now = asyncio.get_event_loop().time()
    elapsed = now - _last_request_time
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)

    credits: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            _last_request_time = asyncio.get_event_loop().time()

            # Search for recording by ISRC
            resp = await client.get(
                f"{MUSICBRAINZ_API}/recording/",
                params={
                    "query": f"isrc:{isrc}",
                    "fmt": "json",
                    "limit": 1,
                },
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            recordings = resp.json().get("recordings", [])
            if not recordings:
                return []

            recording_id = recordings[0].get("id")
            if not recording_id:
                return []

            # Fetch recording with artist-rels
            await asyncio.sleep(1.0)  # Rate limit
            _last_request_time = asyncio.get_event_loop().time()

            resp = await client.get(
                f"{MUSICBRAINZ_API}/recording/{recording_id}",
                params={"inc": "artist-rels", "fmt": "json"},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

            for rel in data.get("relations", []):
                rel_type = rel.get("type", "").lower()
                if rel_type not in CREDIT_TYPES:
                    continue

                artist = rel.get("artist", {})
                artist_name = artist.get("name", "")
                artist_mbid = artist.get("id", "")
                disambiguation = artist.get("disambiguation", "")

                if not artist_name or not artist_mbid:
                    continue

                credits.append({
                    "name": artist_name,
                    "mbid": artist_mbid,
                    "role": rel_type,
                    "disambiguation": disambiguation,
                })

    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.debug("MusicBrainz credits lookup failed for ISRC %s", isrc)
        return []

    # Store in kg_artists + kg_relationships
    if credits:
        await _store_credits(engine, isrc, credits)

    return credits


async def _store_credits(
    engine: Any,
    isrc: str,
    credits: list[dict[str, Any]],
) -> None:
    """Store producer/songwriter credits in knowledge graph tables."""
    source_mbid = f"isrc:{isrc.upper()}"
    now = datetime.now(UTC)

    async with engine.begin() as conn:
        for credit in credits:
            # Upsert artist
            await conn.execute(sa.text(
                "INSERT INTO kg_artists (mbid, name, disambiguation, fetched_at) "
                "VALUES (:mbid, :name, :disambiguation, :fetched_at) "
                "ON CONFLICT (mbid) DO UPDATE SET "
                "name = :name, disambiguation = :disambiguation"
            ), {
                "mbid": credit["mbid"],
                "name": credit["name"],
                "disambiguation": credit.get("disambiguation", ""),
                "fetched_at": now,
            })

            # Insert relationship
            await conn.execute(
                kg_relationships.insert().values(
                    source_mbid=source_mbid,
                    target_mbid=credit["mbid"],
                    relation_type=credit["role"],
                    weight=1.0,
                    fetched_at=now,
                )
            )


async def get_shared_producers(
    engine: Any,
    isrc_a: str,
    isrc_b: str,
) -> float:
    """Compute producer proximity between two songs.

    Returns 0.0 (no shared producers) to 1.0 (same producer).
    Checks kg_relationships for shared producer/mixer credits.
    """
    if not isrc_a or not isrc_b:
        return 0.0

    source_a = f"isrc:{isrc_a.upper()}"
    source_b = f"isrc:{isrc_b.upper()}"

    async with engine.begin() as conn:
        # Get producers for song A
        result_a = await conn.execute(
            sa.select(kg_relationships.c.target_mbid).where(
                sa.and_(
                    kg_relationships.c.source_mbid == source_a,
                    kg_relationships.c.relation_type.in_(
                        ["producer", "co-producer", "mix", "recording"]
                    ),
                )
            )
        )
        producers_a = {row.target_mbid for row in result_a}

        # Get producers for song B
        result_b = await conn.execute(
            sa.select(kg_relationships.c.target_mbid).where(
                sa.and_(
                    kg_relationships.c.source_mbid == source_b,
                    kg_relationships.c.relation_type.in_(
                        ["producer", "co-producer", "mix", "recording"]
                    ),
                )
            )
        )
        producers_b = {row.target_mbid for row in result_b}

    if not producers_a or not producers_b:
        return 0.0

    shared = producers_a & producers_b
    if not shared:
        return 0.0

    # Jaccard similarity
    return len(shared) / len(producers_a | producers_b)
