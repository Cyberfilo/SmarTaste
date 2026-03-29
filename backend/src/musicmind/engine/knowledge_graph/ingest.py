"""MusicBrainz data ingestion — fetch artist relationships and genres.

Queries the MusicBrainz API for artist metadata and relationships,
stores results in kg_artists and kg_relationships tables.

Respects MusicBrainz rate limit: 1 req/sec with User-Agent header.
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

MB_API_URL = "https://musicbrainz.org/ws/2"
MB_USER_AGENT = "MusicMind/2.20 (https://github.com/cyberfilo/musicmind)"
RATE_LIMIT_DELAY = 1.1  # slightly over 1 req/sec

# Relationship types we care about
RELATION_TYPES = {
    "member of band",
    "collaboration",
    "is person",
    "vocal",
    "instrument",
    "producer",
    "mix",
    "remixer",
    "writer",
    "composer",
}

# Map MB relation types to our simplified types
RELATION_MAP: dict[str, str] = {
    "member of band": "member_of",
    "collaboration": "collaborated_with",
    "is person": "member_of",
    "vocal": "collaborated_with",
    "instrument": "collaborated_with",
    "producer": "produced_by",
    "mix": "produced_by",
    "remixer": "remixed_by",
    "writer": "wrote_for",
    "composer": "wrote_for",
}


async def search_artist(name: str) -> dict[str, Any] | None:
    """Search MusicBrainz for an artist by name.

    Returns the best match with MBID, name, and disambiguation.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                f"{MB_API_URL}/artist/",
                params={"query": name, "limit": 1, "fmt": "json"},
                headers={"User-Agent": MB_USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.debug("MusicBrainz search failed for '%s'", name)
            return None

    artists = data.get("artists", [])
    if not artists:
        return None

    best = artists[0]
    return {
        "mbid": best.get("id", ""),
        "name": best.get("name", ""),
        "disambiguation": best.get("disambiguation", ""),
    }


async def fetch_artist_relations(mbid: str) -> list[dict[str, Any]]:
    """Fetch artist relationships from MusicBrainz.

    Args:
        mbid: MusicBrainz artist ID.

    Returns:
        List of relationship dicts with source_mbid, target_mbid,
        relation_type, and target_name.
    """
    await asyncio.sleep(RATE_LIMIT_DELAY)

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                f"{MB_API_URL}/artist/{mbid}",
                params={"inc": "artist-rels+tags", "fmt": "json"},
                headers={"User-Agent": MB_USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.debug("MusicBrainz relation fetch failed for %s", mbid)
            return []

    relations = []
    for rel in data.get("relations", []):
        rel_type = rel.get("type", "")
        if rel_type not in RELATION_TYPES:
            continue

        target = rel.get("artist", {})
        target_mbid = target.get("id")
        if not target_mbid or target_mbid == mbid:
            continue

        mapped_type = RELATION_MAP.get(rel_type, "related_to")
        relations.append({
            "source_mbid": mbid,
            "target_mbid": target_mbid,
            "relation_type": mapped_type,
            "target_name": target.get("name", ""),
        })

    # Also extract genres from tags
    genres = []
    for tag in data.get("tags", []):
        if tag.get("count", 0) > 0:
            genres.append(tag.get("name", ""))

    return relations


async def fetch_artist_genres(mbid: str) -> list[str]:
    """Fetch genre tags for an artist from MusicBrainz."""
    await asyncio.sleep(RATE_LIMIT_DELAY)

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                f"{MB_API_URL}/artist/{mbid}",
                params={"inc": "tags+genres", "fmt": "json"},
                headers={"User-Agent": MB_USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.debug("MusicBrainz genre fetch failed for %s", mbid)
            return []

    genres = []
    for tag in data.get("genres", []) + data.get("tags", []):
        name = tag.get("name", "")
        count = tag.get("count", 0)
        if name and count > 0:
            genres.append(name)

    return genres


async def ingest_artist(
    engine,
    *,
    name: str,
) -> str | None:
    """Search, fetch, and store an artist + relationships.

    Returns the MBID if found, None otherwise.
    """
    # Search for artist
    result = search_artist(name)
    match = await result
    if match is None or not match.get("mbid"):
        return None

    mbid = match["mbid"]

    # Fetch genres
    genres = await fetch_artist_genres(mbid)

    # Store artist
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(kg_artists.c.mbid).where(kg_artists.c.mbid == mbid)
        )
        if existing.first():
            await conn.execute(
                kg_artists.update()
                .where(kg_artists.c.mbid == mbid)
                .values(
                    name=match["name"],
                    disambiguation=match.get("disambiguation", ""),
                    genres=genres,
                    fetched_at=now,
                )
            )
        else:
            await conn.execute(
                kg_artists.insert().values(
                    mbid=mbid,
                    name=match["name"],
                    disambiguation=match.get("disambiguation", ""),
                    genres=genres,
                    fetched_at=now,
                )
            )

    # Fetch and store relationships
    relations = await fetch_artist_relations(mbid)
    if relations:
        async with engine.begin() as conn:
            for rel in relations:
                # Check if relation already exists
                existing = await conn.execute(
                    sa.select(kg_relationships.c.id).where(
                        sa.and_(
                            kg_relationships.c.source_mbid == rel["source_mbid"],
                            kg_relationships.c.target_mbid == rel["target_mbid"],
                            kg_relationships.c.relation_type == rel["relation_type"],
                        )
                    )
                )
                if not existing.first():
                    await conn.execute(
                        kg_relationships.insert().values(
                            source_mbid=rel["source_mbid"],
                            target_mbid=rel["target_mbid"],
                            relation_type=rel["relation_type"],
                            fetched_at=now,
                        )
                    )

                # Also store target artist stub if not exists
                target_existing = await conn.execute(
                    sa.select(kg_artists.c.mbid).where(
                        kg_artists.c.mbid == rel["target_mbid"]
                    )
                )
                if not target_existing.first():
                    await conn.execute(
                        kg_artists.insert().values(
                            mbid=rel["target_mbid"],
                            name=rel.get("target_name", ""),
                            fetched_at=now,
                        )
                    )

    logger.info(
        "Ingested artist '%s' (mbid=%s) with %d relationships",
        match["name"], mbid, len(relations),
    )
    return mbid


async def build_graph_for_user(
    engine,
    *,
    artist_names: list[str],
    max_artists: int = 20,
) -> int:
    """Ingest a batch of artists and their relationships.

    Args:
        engine: SQLAlchemy async engine.
        artist_names: Artist names to ingest.
        max_artists: Maximum number of artists to process.

    Returns:
        Number of artists successfully ingested.
    """
    ingested = 0
    for name in artist_names[:max_artists]:
        mbid = await ingest_artist(engine, name=name)
        if mbid:
            ingested += 1
    return ingested
