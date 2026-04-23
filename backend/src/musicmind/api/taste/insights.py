"""Taste profile insights — derive dashboard-ready data from the raw snapshot.

These helpers turn the centroids and caches the pipeline already computes
(CLAP/MERT/EffNet embeddings, per-song Essentia features, library metadata)
into insights the frontend can render without dumping raw 512-dim vectors
over the wire.

Pure-ish: takes an engine + keyword args, returns plain dicts. No Pydantic.
Rendered into response schemas by the router layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa

from musicmind.db.schema import (
    artist_artwork_cache,
    audio_embeddings_global,
    audio_features_cache,
    global_song_cache,
    song_metadata_cache,
)

# Module-level in-memory artist artwork cache — keyed by artist_name.lower().
# Process restart loses it, which is fine (Apple catalog search is cheap).
# Value "" means "we tried and Apple returned nothing" — prevents retry spam.
_ARTIST_ARTWORK_CACHE: dict[str, str] = {}

# Apple Music storefront order for artist catalog search. "us" first for the
# widest catalog, Italian fallback because many of this project's users listen
# to Italian artists not indexed in the US catalog.
_APPLE_STOREFRONTS = ("us", "it", "gb")

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


def _expand_artwork_url(raw: str, size: int) -> str:
    """Accept either a Spotify-style already-resolved URL or an Apple template."""
    if not raw:
        return ""
    if "{w}" in raw or "{h}" in raw:
        return format_apple_artwork(raw, size)
    return raw


async def _fetch_apple_artist_artwork(
    artist_name: str, *, developer_token: str, size: int,
) -> str:
    """Hit Apple Music catalog search for one artist's portrait.

    Tries multiple storefronts in order and returns the first hit with an
    artwork template. Returns "" on miss or error.
    """
    headers = {"Authorization": f"Bearer {developer_token}"}
    async with httpx.AsyncClient(timeout=4.0) as client:
        for storefront in _APPLE_STOREFRONTS:
            try:
                resp = await client.get(
                    f"https://api.music.apple.com/v1/catalog/{storefront}/search",
                    headers=headers,
                    params={
                        "term": artist_name,
                        "types": "artists",
                        "limit": 1,
                    },
                )
                if resp.status_code != 200:
                    continue
                data = resp.json().get("results", {}).get("artists", {}).get("data", [])
                if not data:
                    continue
                attrs = data[0].get("attributes", {}) or {}
                # Only accept results whose name matches (case-insensitive) —
                # Apple's fuzzy match can return totally unrelated artists.
                returned_name = (attrs.get("name") or "").lower().strip()
                if returned_name != artist_name.lower().strip():
                    continue
                artwork = attrs.get("artwork") or {}
                template = artwork.get("url") or ""
                if template:
                    return format_apple_artwork(template, size)
            except (httpx.HTTPError, ValueError, KeyError):
                continue
    return ""


async def get_artist_artworks(
    engine,
    *,
    user_id: str,
    artist_names: list[str],
    size: int = APPLE_ARTWORK_SIZE_SMALL,
    developer_token: str | None = None,
) -> dict[str, str]:
    """Return {artist_name_lower: artwork_url} for the supplied artists.

    Resolution order per artist (V 6.460 — persistent DB cache added):
      0) Persistent `artist_artwork_cache` table (survives deploys)
      1) In-memory cache (process-local fast path)
      2) User's own library song artwork (album cover for a representative song)
      3) Apple Music catalog search for the artist's portrait
    Empty string when nothing is found; negative results are still cached
    so we don't keep re-querying Apple for artists that have no portrait.
    """
    if not artist_names:
        return {}
    targets = [a for a in artist_names if a]
    if not targets:
        return {}

    result: dict[str, str] = {}

    # Step 0: persistent DB cache. Seeds the in-memory cache too so
    # subsequent calls within the same process skip the DB round-trip.
    lower_targets = [n.lower() for n in targets]
    try:
        async with engine.begin() as conn:
            db_rows = await conn.execute(
                sa.select(
                    artist_artwork_cache.c.artist_name_lower,
                    artist_artwork_cache.c.artwork_url,
                ).where(
                    artist_artwork_cache.c.artist_name_lower.in_(lower_targets)
                )
            )
            for row in db_rows:
                _ARTIST_ARTWORK_CACHE[row.artist_name_lower] = row.artwork_url
                if row.artwork_url:
                    result[row.artist_name_lower] = row.artwork_url
    except Exception:
        logger.debug(
            "artist_artwork_cache read failed; falling back to live lookup",
            exc_info=True,
        )

    to_fetch: list[str] = []

    # Step 1: in-memory cache (also catches the negative rows from Step 0)
    for name in targets:
        key = name.lower()
        if key in _ARTIST_ARTWORK_CACHE and key not in result:
            cached = _ARTIST_ARTWORK_CACHE[key]
            if cached:
                result[key] = cached
            # else: tried previously and got empty — don't re-fetch this turn

    missing = [
        n for n in targets
        if n.lower() not in result and n.lower() not in _ARTIST_ARTWORK_CACHE
    ]

    # Step 2: library album art (Apple template OR Spotify full URL)
    if missing:
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
                            [a.lower() for a in missing]
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
                    url = _expand_artwork_url(row.artwork_url_template, size)
                    if url:
                        result[key] = url
                        to_fetch.append(key)  # pipe to cache after loop

        for k in to_fetch:
            _ARTIST_ARTWORK_CACHE[k] = result[k]

    # Accumulator for new rows to persist to the DB cache.
    new_db_entries: list[tuple[str, str]] = [(k, result[k]) for k in to_fetch]

    # Step 3: Apple Music catalog search for still-missing artists
    still_missing = [
        n for n in targets
        if n.lower() not in result and n.lower() not in _ARTIST_ARTWORK_CACHE
    ]
    if still_missing and developer_token:
        async def _fetch_one(name: str) -> tuple[str, str]:
            url = await _fetch_apple_artist_artwork(
                name,
                developer_token=developer_token,
                size=APPLE_ARTWORK_SIZE_MEDIUM,  # artist portraits look better at 240
            )
            return name.lower(), url

        fetched = await asyncio.gather(
            *(_fetch_one(n) for n in still_missing),
            return_exceptions=True,
        )
        for entry in fetched:
            if isinstance(entry, BaseException):
                continue
            key, url = entry
            _ARTIST_ARTWORK_CACHE[key] = url  # cache both hits and misses ("")
            new_db_entries.append((key, url))  # persist even negatives
            if url:
                result[key] = url

    # Persist new entries to artist_artwork_cache. Best-effort — if the
    # write fails the in-memory cache still saves this request; we'll
    # retry on the next deploy-cold-start.
    if new_db_entries:
        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            async with engine.begin() as conn:
                stmt = pg_insert(artist_artwork_cache).values([
                    {"artist_name_lower": k, "artwork_url": v}
                    for k, v in new_db_entries
                ])
                stmt = stmt.on_conflict_do_update(
                    index_elements=["artist_name_lower"],
                    set_={
                        "artwork_url": stmt.excluded.artwork_url,
                        "fetched_at": sa.func.now(),
                    },
                )
                await conn.execute(stmt)
        except Exception:
            logger.debug(
                "artist_artwork_cache write failed; next cold start will re-fetch",
                exc_info=True,
            )

    return result


async def get_recent_enrichments(
    engine, *, user_id: str, limit: int = 12,
) -> list[dict[str, Any]]:
    """Last N songs enriched for this user (worker's freshest output).

    Joins `audio_features_cache` (the "analyzed" side) with
    `song_metadata_cache` (for artwork + display name) and falls back to
    `global_song_cache.artwork_url` when the user-library template is empty
    (older libraries indexed before the artwork_url_template fix was on).
    Ordered by enriched_at DESC with analyzed_at fallback.
    """
    async with engine.begin() as conn:
        rows = await conn.execute(
            sa.select(
                song_metadata_cache.c.catalog_id,
                song_metadata_cache.c.name,
                song_metadata_cache.c.artist_name,
                song_metadata_cache.c.album_name,
                song_metadata_cache.c.artwork_url_template,
                global_song_cache.c.artwork_url.label("global_artwork_url"),
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
                ).outerjoin(
                    global_song_cache,
                    global_song_cache.c.catalog_id
                    == song_metadata_cache.c.catalog_id,
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
            artwork = _expand_artwork_url(
                row.artwork_url_template or "", APPLE_ARTWORK_SIZE_SMALL,
            )
            if not artwork and row.global_artwork_url:
                artwork = _expand_artwork_url(
                    row.global_artwork_url, APPLE_ARTWORK_SIZE_SMALL,
                )
            items.append({
                "catalog_id": row.catalog_id or "",
                "name": row.name or "",
                "artist_name": row.artist_name or "",
                "album_name": row.album_name or "",
                "artwork_url": artwork,
                "enriched_at": ts.isoformat() if ts is not None else None,
                "tempo": float(row.tempo) if row.tempo is not None else None,
                "energy": float(row.energy) if row.energy is not None else None,
                "danceability": (
                    float(row.danceability) if row.danceability is not None else None
                ),
            })
        return items


# ── Library distributions (interactive chart data) ────────────────────

TEMPO_BINS = [
    (60, 80, "60-80"),
    (80, 100, "80-100"),
    (100, 115, "100-115"),
    (115, 130, "115-130"),
    (130, 150, "130-150"),
    (150, 180, "150-180"),
    (180, 220, "180+"),
]

_MUSICAL_KEY_ORDER = [
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
]
_KEY_ALIASES = {  # Essentia uses flat names; map to sharp equivalents for display
    "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",
}


async def get_library_distributions(
    engine, *, user_id: str, scatter_limit: int | None = None,
) -> dict[str, Any]:
    """Aggregate Essentia enrichment data into dashboard-ready distributions.

    Returns tempo histogram (fixed BPM bins), key × mode table (major/minor
    counts per 12 pitch classes), acousticness histogram, valence histogram,
    plus an energy/danceability scatter of **every library song** — the
    scatter intentionally shows all of the user's own songs so they can see
    their full library's cloud, not a sample.

    Scope: this function ONLY includes library-origin rows
    (`library_id IS NOT NULL OR date_added_to_library IS NOT NULL`).
    Worker-discovered tracks (songs the cobweb pulled in because an artist
    was related, but the user never added) are excluded — those represent
    candidate recommendations, not the user's actual taste.

    Args:
        scatter_limit: Optional hard cap on scatter points for very large
            libraries (>5k songs). None means "no cap — include every point".
    """
    async with engine.begin() as conn:
        rows = await conn.execute(
            sa.select(
                song_metadata_cache.c.catalog_id,
                song_metadata_cache.c.name,
                song_metadata_cache.c.artist_name,
                audio_features_cache.c.tempo,
                audio_features_cache.c.energy,
                audio_features_cache.c.danceability,
                audio_features_cache.c.acousticness,
                audio_features_cache.c.valence_proxy,
                audio_features_cache.c.key,
                audio_features_cache.c.scale,
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
                    # Library-only: user explicitly added these songs.
                    # Discography / cobweb rows have both columns NULL.
                    sa.or_(
                        song_metadata_cache.c.library_id.isnot(None),
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                    ),
                )
            )
        )
        all_rows = list(rows)

    if not all_rows:
        return {
            "total_songs": 0,
            "tempo_histogram": [],
            "key_distribution": [],
            "acousticness_histogram": [],
            "valence_histogram": [],
            "scatter": [],
        }

    # Tempo histogram
    tempo_counts = [0] * len(TEMPO_BINS)
    tempos: list[float] = []
    for r in all_rows:
        t = r.tempo
        if t is None or not (0 < t < 400):
            continue
        tempos.append(float(t))
        for i, (lo, hi, _) in enumerate(TEMPO_BINS):
            if lo <= t < hi:
                tempo_counts[i] += 1
                break
    tempo_histogram = [
        {"range": label, "count": cnt, "low": lo, "high": hi}
        for (lo, hi, label), cnt in zip(TEMPO_BINS, tempo_counts)
    ]
    avg_tempo = (sum(tempos) / len(tempos)) if tempos else 0.0

    # Key × mode
    key_counts: dict[str, dict[str, int]] = {
        k: {"major": 0, "minor": 0} for k in _MUSICAL_KEY_ORDER
    }
    total_keyed = 0
    for r in all_rows:
        if not r.key or not r.scale:
            continue
        key = _KEY_ALIASES.get(r.key, r.key)
        if key not in key_counts:
            continue
        mode = (r.scale or "").lower()
        if mode not in ("major", "minor"):
            continue
        key_counts[key][mode] += 1
        total_keyed += 1
    key_distribution = [
        {"key": k, "major": counts["major"], "minor": counts["minor"]}
        for k, counts in key_counts.items()
    ]

    # Acousticness histogram (0-1, 10 bins)
    ac_bins = [0] * 10
    for r in all_rows:
        if r.acousticness is None:
            continue
        v = max(0.0, min(1.0, float(r.acousticness)))
        idx = min(9, int(v * 10))
        ac_bins[idx] += 1
    acousticness_histogram = [
        {"bucket": f"{i*10}-{(i+1)*10}%", "count": c}
        for i, c in enumerate(ac_bins)
    ]

    # Valence histogram (0-1, 10 bins)
    v_bins = [0] * 10
    for r in all_rows:
        if r.valence_proxy is None:
            continue
        v = max(0.0, min(1.0, float(r.valence_proxy)))
        idx = min(9, int(v * 10))
        v_bins[idx] += 1
    valence_histogram = [
        {"bucket": f"{i*10}-{(i+1)*10}%", "count": c}
        for i, c in enumerate(v_bins)
    ]

    # Scatter (energy × danceability): every library song that has both scalars.
    # Only stride-sample if the library exceeds the optional cap, which is a
    # performance guard for >5k-song libraries — for most users, every song
    # becomes a dot.
    scatter: list[dict[str, Any]] = []
    source_rows = all_rows
    if scatter_limit is not None and len(all_rows) > scatter_limit:
        stride = max(1, len(all_rows) // scatter_limit)
        source_rows = all_rows[::stride]
    for r in source_rows:
        if r.energy is None or r.danceability is None:
            continue
        scatter.append({
            "catalog_id": r.catalog_id or "",
            "name": r.name or "",
            "artist_name": r.artist_name or "",
            "energy": max(0.0, min(1.0, float(r.energy))),
            "danceability": max(0.0, min(1.0, float(r.danceability))),
        })

    return {
        "total_songs": len(all_rows),
        "avg_tempo": round(avg_tempo, 1),
        "total_keyed": total_keyed,
        "tempo_histogram": tempo_histogram,
        "key_distribution": key_distribution,
        "acousticness_histogram": acousticness_histogram,
        "valence_histogram": valence_histogram,
        "scatter": scatter,
    }


# ── Genre normalization for display ───────────────────────────────────

# Raw service genres we want to drop from the dashboard entirely.
# "Musica" and "Música" are Apple's Italian/Spanish "catch-all" label
# applied when Apple can't identify a real genre — not meaningful signal.
# Empty string appears when the API returns no genre data.
_GENRE_BLOCKLIST = {"", "musica", "música", "music"}

# Redundant parent genres to roll up into their more-specific sibling.
# "Rap" → "Hip-Hop/Rap", "Hip-Hop" → "Hip-Hop/Rap" — Apple occasionally
# returns the parent and the compound label on the same song, double-counting.
_GENRE_MERGE_INTO_COMPOUND = {
    "Rap": "Hip-Hop/Rap",
    "Hip-Hop": "Hip-Hop/Rap",
    "Soul": "R&B/Soul",
    "R&B": "R&B/Soul",
}


def clean_genre_vector(genre_vector: dict[str, float]) -> dict[str, float]:
    """Strip blocklist entries and merge redundant parent/child labels.

    Returned dict sums to the same total as the input minus blocklist weight
    (so "Hip-Hop/Rap" stays stable if "Rap" was already its child); callers
    using % formatting still get percentages that add up correctly.
    """
    cleaned: dict[str, float] = {}
    for name, weight in genre_vector.items():
        key = (name or "").strip()
        if key.lower() in _GENRE_BLOCKLIST:
            continue
        merged_to = _GENRE_MERGE_INTO_COMPOUND.get(key)
        # Only merge if the compound label is actually present in the vector
        if merged_to and merged_to in genre_vector:
            cleaned[merged_to] = cleaned.get(merged_to, 0.0) + weight
            continue
        cleaned[key] = cleaned.get(key, 0.0) + weight
    return cleaned


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
