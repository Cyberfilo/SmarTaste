"""Taste profile insights — derive dashboard-ready data from the raw snapshot.

These helpers turn the centroids and caches the pipeline already computes
(CLAP/MERT/EffNet embeddings, per-song Essentia features, library metadata)
into insights the frontend can render without dumping raw 512-dim vectors
over the wire.

Pure-ish: takes an engine + keyword args, returns plain dicts. No Pydantic.
Rendered into response schemas by the router layer.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import (
    audio_embeddings_global,
    audio_features_cache,
    global_song_cache,
    song_metadata_cache,
)

logger = logging.getLogger(__name__)

APPLE_ARTWORK_SIZE_SMALL = 120
APPLE_ARTWORK_SIZE_MEDIUM = 240
APPLE_ARTWORK_SIZE_LARGE = 400


def format_apple_artwork(template: str | None, size: int = APPLE_ARTWORK_SIZE_MEDIUM) -> str:
    """Fill `{w}` / `{h}` placeholders in an Apple Music artwork template.

    Apple's templates look like:
        https://.../Music/<path>/{w}x{h}bb.jpg

    Returns empty string for missing/unknown templates; callers should
    handle the empty case with a placeholder.
    """
    if not template:
        return ""
    if "{w}" not in template and "{h}" not in template:
        return template
    s = str(size)
    return template.replace("{w}", s).replace("{h}", s).replace("{f}", "jpg")


async def get_artist_artworks(
    engine, *, user_id: str, artist_names: list[str], size: int = APPLE_ARTWORK_SIZE_SMALL,
) -> dict[str, str]:
    """Return {artist_name_lower: artwork_url} for the supplied artists.

    Picks one representative song per artist from the user's own library
    (preferring the most recently added). Empty string when nothing is found.
    """
    if not artist_names:
        return {}
    targets = [a for a in artist_names if a]
    if not targets:
        return {}

    result: dict[str, str] = {}
    async with engine.begin() as conn:
        rows = await conn.execute(
            sa.select(
                song_metadata_cache.c.artist_name,
                song_metadata_cache.c.artwork_url_template,
                song_metadata_cache.c.date_added_to_library,
                song_metadata_cache.c.fetched_at,
            )
            .where(
                sa.and_(
                    song_metadata_cache.c.user_id == user_id,
                    sa.func.lower(song_metadata_cache.c.artist_name).in_(
                        [a.lower() for a in targets]
                    ),
                    song_metadata_cache.c.artwork_url_template.isnot(None),
                    song_metadata_cache.c.artwork_url_template != "",
                )
            )
            .order_by(
                sa.func.coalesce(
                    song_metadata_cache.c.date_added_to_library,
                    song_metadata_cache.c.fetched_at,
                ).desc()
            )
        )
        for row in rows:
            key = (row.artist_name or "").lower()
            if key and key not in result:
                result[key] = format_apple_artwork(row.artwork_url_template, size)
    return result


async def get_recent_enrichments(
    engine, *, user_id: str, limit: int = 12,
) -> list[dict[str, Any]]:
    """Last N songs enriched for this user (worker's freshest output).

    Joins `audio_features_cache` (the "analyzed" side) with
    `song_metadata_cache` (for artwork + display name). Ordered by
    enriched_at DESC with a fallback to analyzed_at for older rows.
    """
    async with engine.begin() as conn:
        rows = await conn.execute(
            sa.select(
                song_metadata_cache.c.catalog_id,
                song_metadata_cache.c.name,
                song_metadata_cache.c.artist_name,
                song_metadata_cache.c.album_name,
                song_metadata_cache.c.artwork_url_template,
                audio_features_cache.c.tempo,
                audio_features_cache.c.energy,
                audio_features_cache.c.danceability,
                audio_features_cache.c.enriched_at,
                audio_features_cache.c.analyzed_at,
            )
            .select_from(
                audio_features_cache.join(
                    song_metadata_cache,
                    sa.and_(
                        audio_features_cache.c.catalog_id
                        == song_metadata_cache.c.catalog_id,
                        audio_features_cache.c.user_id
                        == song_metadata_cache.c.user_id,
                    ),
                )
            )
            .where(
                sa.and_(
                    audio_features_cache.c.user_id == user_id,
                    audio_features_cache.c.energy.isnot(None),
                )
            )
            .order_by(
                sa.func.coalesce(
                    audio_features_cache.c.enriched_at,
                    audio_features_cache.c.analyzed_at,
                ).desc()
            )
            .limit(limit)
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            ts = row.enriched_at or row.analyzed_at
            items.append({
                "catalog_id": row.catalog_id or "",
                "name": row.name or "",
                "artist_name": row.artist_name or "",
                "album_name": row.album_name or "",
                "artwork_url": format_apple_artwork(
                    row.artwork_url_template, APPLE_ARTWORK_SIZE_SMALL,
                ),
                "enriched_at": ts.isoformat() if ts is not None else None,
                "tempo": float(row.tempo) if row.tempo is not None else None,
                "energy": float(row.energy) if row.energy is not None else None,
                "danceability": (
                    float(row.danceability) if row.danceability is not None else None
                ),
            })
        return items


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors. Returns 0 for bad input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


async def compute_sonic_neighbors(
    engine,
    *,
    user_id: str,
    clap_centroid: list[float] | None,
    limit: int = 8,
    sample_size: int = 2000,
) -> list[dict[str, Any]]:
    """Discovery artists whose CLAP embeddings most closely match the user.

    Samples up to `sample_size` rows from `audio_embeddings_global` (ISRC-keyed
    CLAP embeddings on the global catalog), joins with `global_song_cache` for
    display metadata, filters out artists already in the user's library, groups
    by artist, picks each artist's best-matching song, and returns the top
    `limit` by cosine similarity.

    Returns empty list if the user has no CLAP centroid yet.
    """
    if not clap_centroid or len(clap_centroid) < 128:
        return []

    # Collect user's artist names to exclude from results (so we recommend new artists)
    async with engine.begin() as conn:
        user_artist_rows = await conn.execute(
            sa.select(sa.distinct(song_metadata_cache.c.artist_name)).where(
                song_metadata_cache.c.user_id == user_id,
            )
        )
        user_artists = {
            (row[0] or "").lower() for row in user_artist_rows if row[0]
        }

    # Sample global candidates with non-null CLAP. We order by ISRC descending
    # as a cheap randomization proxy; a proper random sample would scan too much.
    async with engine.begin() as conn:
        rows = await conn.execute(
            sa.select(
                global_song_cache.c.catalog_id,
                global_song_cache.c.name,
                global_song_cache.c.artist_name,
                global_song_cache.c.album_name,
                global_song_cache.c.artwork_url,
                global_song_cache.c.genre_names,
                audio_embeddings_global.c.clap_embedding,
            )
            .select_from(
                audio_embeddings_global.join(
                    global_song_cache,
                    audio_embeddings_global.c.isrc == global_song_cache.c.isrc,
                )
            )
            .where(audio_embeddings_global.c.clap_embedding.isnot(None))
            .limit(sample_size)
        )
        candidates = list(rows)

    if not candidates:
        return []

    # Group by artist, track best similarity per artist
    artist_best: dict[str, dict[str, Any]] = {}
    for row in candidates:
        artist = (row.artist_name or "").strip()
        if not artist:
            continue
        if artist.lower() in user_artists:
            continue

        clap = row.clap_embedding
        if isinstance(clap, str):
            try:
                clap = json.loads(clap)
            except (json.JSONDecodeError, TypeError):
                continue
        if not clap or not isinstance(clap, list):
            continue

        sim = _cosine(clap_centroid, clap)
        if sim <= 0:
            continue

        existing = artist_best.get(artist)
        if existing is None or sim > existing["similarity"]:
            genres = row.genre_names
            if isinstance(genres, str):
                try:
                    genres = json.loads(genres)
                except (json.JSONDecodeError, TypeError):
                    genres = []
            artist_best[artist] = {
                "artist_name": artist,
                "similarity": sim,
                "sample_song_name": row.name or "",
                "sample_catalog_id": row.catalog_id or "",
                "sample_album_name": row.album_name or "",
                "artwork_url": row.artwork_url or "",
                "genre_names": list(genres) if isinstance(genres, list) else [],
            }

    ranked = sorted(
        artist_best.values(), key=lambda x: -float(x["similarity"])
    )[:limit]
    return ranked


def compute_breadth_metrics(
    genre_vector: dict[str, float], top_artists: list[dict[str, Any]],
) -> dict[str, float]:
    """Client-ready library breadth numbers derived from the existing snapshot.

    - genre_entropy: Shannon entropy of the genre distribution, normalized to
      [0, 1] by dividing by log(N_genres). 0 = one-genre listener, 1 = uniform.
    - artist_concentration: share of the top-5 artists out of all ranked artists.
      High = monoculture, low = spread.
    - sonic_breadth: composite 0–1 ("wider = more varied").
    """
    total_weight = sum(max(0.0, v) for v in genre_vector.values())
    genre_entropy = 0.0
    if total_weight > 0 and len(genre_vector) > 1:
        probs = [
            max(0.0, v) / total_weight
            for v in genre_vector.values()
            if v > 0
        ]
        raw = -sum(p * math.log(p) for p in probs if p > 0)
        max_entropy = math.log(len(probs)) if len(probs) > 1 else 1.0
        genre_entropy = min(1.0, raw / max_entropy) if max_entropy > 0 else 0.0

    if top_artists:
        scores = [max(0.0, float(a.get("score", 0.0))) for a in top_artists]
        total_score = sum(scores)
        top5_score = sum(sorted(scores, reverse=True)[:5])
        artist_concentration = (
            top5_score / total_score if total_score > 0 else 0.0
        )
    else:
        artist_concentration = 0.0

    sonic_breadth = max(
        0.0,
        min(1.0, 0.6 * genre_entropy + 0.4 * (1.0 - artist_concentration)),
    )

    return {
        "genre_entropy": round(genre_entropy, 3),
        "artist_concentration": round(artist_concentration, 3),
        "sonic_breadth": round(sonic_breadth, 3),
    }


def freshness_label(iso_ts: str | None) -> str:
    """Human label for 'X ago' — used in enrichment timestamps."""
    if not iso_ts:
        return ""
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - ts
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h ago"
    days = hrs // 24
    if days < 30:
        return f"{days}d ago"
    return ts.date().isoformat()
