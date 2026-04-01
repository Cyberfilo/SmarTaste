"""Standalone enrichment worker with full artist discography support.

Runs on your Mac or any server. Connects to Railway PostgreSQL directly.
Three phases per cycle:
  1. Enrich un-enriched library tracks (Deezer → ReccoBeats)
  2. Discover discography tracks for relevant artists
  3. Enrich discography tracks

Outlier artists (e.g. Mariah Carey in an Italian trap library) are
detected and skipped based on genre overlap with user's profile.

Usage:
    DATABASE_URL="postgresql://..." python enrichment_worker.py
    DATABASE_URL="postgresql://..." CONCURRENCY=15 python enrichment_worker.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections import Counter
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("worker")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SOUNDSTAT_API_KEY = os.environ.get("SOUNDSTAT_API_KEY", "")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "15"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "200"))
SLEEP_SECONDS = int(os.environ.get("SLEEP_SECONDS", "5"))

# Minimum genre overlap ratio for an artist to be considered "relevant"
# to the user's taste (vs an outlier like Christmas songs)
GENRE_OVERLAP_THRESHOLD = 0.2

DEEZER_API = "https://api.deezer.com"
RECCOBEATS_API = "https://api.reccobeats.com/v1/analysis/audio-features"


# ── Main Loop ────────────────────────────────────────────────────────────────


async def main() -> None:
    if not DATABASE_URL:
        logger.error("Set DATABASE_URL to your Railway PostgreSQL connection string")
        sys.exit(1)

    url = DATABASE_URL
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url, pool_size=10, max_overflow=10)
    logger.info(
        "Worker started — concurrency=%d, batch=%d, sleep=%ds",
        CONCURRENCY, BATCH_SIZE, SLEEP_SECONDS,
    )

    try:
        while True:
            # Phase 1: Enrich existing un-enriched library tracks
            lib_count = await enrich_library_tracks(engine)

            # Phase 2: Discover + enrich artist discographies
            disco_count = await enrich_artist_discographies(engine)

            if lib_count == 0 and disco_count == 0:
                logger.info("Nothing to do, sleeping %ds", SLEEP_SECONDS)

            await asyncio.sleep(SLEEP_SECONDS)
    finally:
        await engine.dispose()


# ── Phase 1: Library Tracks ──────────────────────────────────────────────────


async def enrich_library_tracks(engine) -> int:
    """Find and enrich un-enriched library tracks across all users."""
    async with engine.begin() as conn:
        result = await conn.execute(sa.text(
            "SELECT s.catalog_id, s.user_id, s.name, s.artist_name, s.isrc, "
            "       s.service_source "
            "FROM song_metadata_cache s "
            "LEFT JOIN audio_features_cache a "
            "  ON s.catalog_id = a.catalog_id AND s.user_id = a.user_id "
            "WHERE a.catalog_id IS NULL "
            "LIMIT :limit"
        ), {"limit": BATCH_SIZE})
        rows = result.fetchall()

    if not rows:
        return 0

    tracks = [
        {"catalog_id": r[0], "user_id": r[1], "name": r[2],
         "artist_name": r[3], "isrc": r[4] or "", "service_source": r[5] or ""}
        for r in rows
    ]

    logger.info("Phase 1: Enriching %d library tracks", len(tracks))
    stats = await _enrich_batch(engine, tracks)
    logger.info(
        "Phase 1 done: %d enriched, %d failed, %d skipped",
        stats["ok"], stats["fail"], stats["skip"],
    )
    return len(tracks)


# ── Phase 2: Artist Discographies ────────────────────────────────────────────


async def enrich_artist_discographies(engine) -> int:
    """For each user, fetch relevant artists' full discographies and enrich."""
    # Get all users with connected services
    async with engine.begin() as conn:
        users_result = await conn.execute(sa.text(
            "SELECT DISTINCT user_id FROM service_connections"
        ))
        user_ids = [r[0] for r in users_result.fetchall()]

    if not user_ids:
        return 0

    total_discovered = 0
    for user_id in user_ids:
        count = await _discover_and_enrich_for_user(engine, user_id)
        total_discovered += count

    return total_discovered


async def _discover_and_enrich_for_user(engine, user_id: str) -> int:
    """Discover discography tracks for one user's relevant artists."""
    # Get user's genre profile (to detect outlier artists)
    async with engine.begin() as conn:
        profile_result = await conn.execute(sa.text(
            "SELECT genre_vector FROM taste_profile_snapshots "
            "WHERE user_id = :uid ORDER BY computed_at DESC LIMIT 1"
        ), {"uid": user_id})
        profile_row = profile_result.first()

    if not profile_row:
        return 0

    genre_vector = profile_row[0]
    if isinstance(genre_vector, str):
        try:
            genre_vector = json.loads(genre_vector)
        except (json.JSONDecodeError, TypeError):
            genre_vector = {}

    if not genre_vector:
        return 0

    # Get user's top genres (normalized names, lowercase)
    user_genres = set(g.lower() for g in genre_vector.keys())

    # Get all distinct artists from user's library with their genres
    # genre_names can be a JSON array, a plain string, or null — handle all cases
    async with engine.begin() as conn:
        artists_result = await conn.execute(sa.text(
            "SELECT artist_name, "
            "       json_agg(DISTINCT g) FILTER (WHERE g IS NOT NULL) AS genres, "
            "       COUNT(*) AS song_count "
            "FROM song_metadata_cache "
            "LEFT JOIN LATERAL json_array_elements_text( "
            "    CASE "
            "        WHEN genre_names IS NULL THEN '[]'::json "
            "        WHEN genre_names::text = '' THEN '[]'::json "
            "        WHEN left(genre_names::text, 1) = '[' THEN genre_names::json "
            "        ELSE json_build_array(genre_names::text) "
            "    END "
            ") AS g ON true "
            "WHERE user_id = :uid AND artist_name != '' "
            "GROUP BY artist_name "
            "ORDER BY song_count DESC"
        ), {"uid": user_id})
        artists = artists_result.fetchall()

    if not artists:
        return 0

    # Filter to relevant artists (skip outliers)
    relevant_artists: list[str] = []
    for row in artists:
        artist_name = row[0]
        artist_genres_raw = row[1] or []
        if isinstance(artist_genres_raw, str):
            try:
                artist_genres_raw = json.loads(artist_genres_raw)
            except (json.JSONDecodeError, TypeError):
                artist_genres_raw = []

        artist_genres = set(g.lower() for g in artist_genres_raw)

        if not artist_genres or not user_genres:
            # No genre data — include if they have 3+ songs
            if row[2] >= 3:
                relevant_artists.append(artist_name)
            continue

        overlap = len(artist_genres & user_genres) / len(artist_genres)
        if overlap >= GENRE_OVERLAP_THRESHOLD:
            relevant_artists.append(artist_name)
        else:
            logger.debug(
                "Skipping outlier artist '%s' (%.0f%% genre overlap)",
                artist_name, overlap * 100,
            )

    if not relevant_artists:
        return 0

    logger.info(
        "Phase 2: %d relevant artists (of %d total) for user %s",
        len(relevant_artists), len(artists), user_id[:8],
    )

    # For each relevant artist, search Deezer for their discography
    all_disco_tracks: list[dict] = []
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _fetch_artist_disco(artist_name: str) -> list[dict]:
        async with semaphore:
            return await _deezer_artist_discography(artist_name)

    disco_results = await asyncio.gather(
        *[_fetch_artist_disco(name) for name in relevant_artists[:50]],
        return_exceptions=True,
    )

    for result in disco_results:
        if isinstance(result, list):
            all_disco_tracks.extend(result)

    if not all_disco_tracks:
        return 0

    # Deduplicate by catalog_id
    seen: set[str] = set()
    unique_tracks: list[dict] = []
    for t in all_disco_tracks:
        cid = t.get("catalog_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            t["user_id"] = user_id
            unique_tracks.append(t)

    # Filter out already-enriched tracks
    async with engine.begin() as conn:
        existing_result = await conn.execute(sa.text(
            "SELECT catalog_id FROM audio_features_cache WHERE user_id = :uid"
        ), {"uid": user_id})
        existing_ids = {r[0] for r in existing_result.fetchall()}

    to_enrich = [t for t in unique_tracks if t["catalog_id"] not in existing_ids]

    if not to_enrich:
        logger.info("Phase 2: All discography tracks already enriched for %s", user_id[:8])
        return 0

    logger.info(
        "Phase 2: Enriching %d new discography tracks (from %d unique) for %s",
        len(to_enrich), len(unique_tracks), user_id[:8],
    )

    # Enrich in batches
    for i in range(0, len(to_enrich), BATCH_SIZE):
        batch = to_enrich[i:i + BATCH_SIZE]
        stats = await _enrich_batch(engine, batch)
        logger.info(
            "Phase 2 batch: %d/%d enriched (total progress: %d/%d)",
            stats["ok"], len(batch), i + len(batch), len(to_enrich),
        )

    return len(to_enrich)


async def _deezer_artist_discography(artist_name: str) -> list[dict]:
    """Fetch an artist's top tracks from Deezer (up to 50 songs)."""
    tracks: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Search for the artist
            resp = await client.get(
                f"{DEEZER_API}/search/artist",
                params={"q": artist_name, "limit": 1},
            )
            artists = resp.json().get("data", [])
            if not artists:
                return []

            artist_id = artists[0].get("id")
            if not artist_id:
                return []

            # Get their top tracks
            resp2 = await client.get(
                f"{DEEZER_API}/artist/{artist_id}/top",
                params={"limit": 50},
            )
            items = resp2.json().get("data", [])

            for item in items:
                tracks.append({
                    "catalog_id": f"dz_{item.get('id', '')}",
                    "name": item.get("title", ""),
                    "artist_name": item.get("artist", {}).get("name", artist_name),
                    "isrc": "",
                    "service_source": "deezer",
                    "deezer_id": str(item.get("id", "")),
                    "preview_url": item.get("preview", ""),
                })

    except Exception:
        logger.debug("Failed to fetch discography for '%s'", artist_name)

    return tracks


# ── Shared Enrichment Logic ──────────────────────────────────────────────────


async def _enrich_batch(engine, tracks: list[dict]) -> dict[str, int]:
    """Enrich a batch of tracks concurrently."""
    stats = {"ok": 0, "fail": 0, "skip": 0}
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _bounded(track: dict) -> str:
        async with semaphore:
            return await _enrich_track(engine, track)

    results = await asyncio.gather(
        *[_bounded(t) for t in tracks],
        return_exceptions=True,
    )

    for r in results:
        if isinstance(r, Exception):
            stats["fail"] += 1
        elif r in ("reccobeats", "deezer", "soundstat"):
            stats["ok"] += 1
        elif r == "skipped":
            stats["skip"] += 1
        else:
            stats["fail"] += 1

    return stats


async def _enrich_track(engine, track: dict) -> str:
    """Enrich a single track: Deezer search → download preview → ReccoBeats."""
    catalog_id = track["catalog_id"]
    user_id = track.get("user_id", "")
    name = track.get("name", "")
    artist_name = track.get("artist_name", "")

    if not catalog_id or not user_id:
        return "failed"

    if not name or not artist_name:
        await _store_empty(engine, catalog_id, user_id)
        return "failed"

    features: dict = {}
    feature_source: dict = {}
    preview_url = track.get("preview_url", "")

    # Stage 1: Deezer search (skip if we already have a preview URL from discography)
    if not preview_url:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{DEEZER_API}/search",
                    params={"q": f"{name} {artist_name}", "limit": 1},
                )
                items = resp.json().get("data", [])
                if items:
                    did = items[0]["id"]
                    resp2 = await client.get(f"{DEEZER_API}/track/{did}")
                    full = resp2.json()
                    bpm = full.get("bpm")
                    if bpm and bpm > 0:
                        features["tempo"] = float(bpm)
                        feature_source["tempo"] = "deezer"
                    preview_url = full.get("preview", "")
        except Exception:
            pass

    # Stage 2: ReccoBeats — download preview and upload for analysis
    if preview_url:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                preview_resp = await client.get(preview_url)
                audio_bytes = preview_resp.content

            if len(audio_bytes) > 1000:
                for attempt in range(3):
                    try:
                        async with httpx.AsyncClient(timeout=60.0) as rc:
                            files = {"audioFile": ("p.mp3", audio_bytes, "audio/mpeg")}
                            rr = await rc.post(RECCOBEATS_API, files=files)

                            if rr.status_code == 200:
                                data = rr.json()
                                for field, key in [
                                    ("energy", "energy"),
                                    ("danceability", "danceability"),
                                    ("acousticness", "acousticness"),
                                    ("valence_proxy", "valence"),
                                    ("instrumentalness", "instrumentalness"),
                                ]:
                                    if data.get(key) is not None:
                                        features[field] = round(float(data[key]), 4)
                                        feature_source[field] = "reccobeats"
                                if data.get("tempo") and data["tempo"] > 0 and "tempo" not in features:
                                    features["tempo"] = round(float(data["tempo"]), 2)
                                    feature_source["tempo"] = "reccobeats"
                                if data.get("loudness") is not None:
                                    raw = float(data["loudness"])
                                    features["loudness"] = round(
                                        max(0.0, min(1.0, (raw + 60) / 60)), 4
                                    )
                                    feature_source["loudness"] = "reccobeats"
                                break  # Success

                            elif rr.status_code == 429:
                                wait = float(rr.headers.get("Retry-After", "3"))
                                logger.info("ReccoBeats 429, waiting %.0fs", wait)
                                await asyncio.sleep(wait)
                            else:
                                break  # Non-retryable error
                    except httpx.ReadTimeout:
                        continue
                    except Exception:
                        break

                del audio_bytes
        except Exception:
            pass

    if features:
        await _store(engine, catalog_id, user_id, features, feature_source)
        return "reccobeats" if "energy" in features else "deezer"
    else:
        await _store_empty(engine, catalog_id, user_id)
        return "failed"


# ── Database Storage ─────────────────────────────────────────────────────────


async def _store(engine, cid: str, uid: str, features: dict, source: dict) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO audio_features_cache "
            "(catalog_id, user_id, tempo, energy, danceability, acousticness, "
            " valence_proxy, instrumentalness, loudness, feature_source, "
            " enriched_at, analyzed_at) "
            "VALUES (:cid, :uid, :tempo, :energy, :dance, :acoustic, "
            " :valence, :inst, :loud, :src, :ea, :aa) "
            "ON CONFLICT (catalog_id, user_id) DO UPDATE SET "
            "tempo=COALESCE(EXCLUDED.tempo, audio_features_cache.tempo), "
            "energy=COALESCE(EXCLUDED.energy, audio_features_cache.energy), "
            "danceability=COALESCE(EXCLUDED.danceability, audio_features_cache.danceability), "
            "acousticness=COALESCE(EXCLUDED.acousticness, audio_features_cache.acousticness), "
            "valence_proxy=COALESCE(EXCLUDED.valence_proxy, audio_features_cache.valence_proxy), "
            "instrumentalness=COALESCE(EXCLUDED.instrumentalness, audio_features_cache.instrumentalness), "
            "loudness=COALESCE(EXCLUDED.loudness, audio_features_cache.loudness), "
            "feature_source=EXCLUDED.feature_source, enriched_at=EXCLUDED.enriched_at, "
            "analyzed_at=EXCLUDED.analyzed_at"
        ), {
            "cid": cid, "uid": uid,
            "tempo": features.get("tempo"), "energy": features.get("energy"),
            "dance": features.get("danceability"), "acoustic": features.get("acousticness"),
            "valence": features.get("valence_proxy"), "inst": features.get("instrumentalness"),
            "loud": features.get("loudness"), "src": json.dumps(source),
            "ea": now, "aa": now,
        })


async def _store_empty(engine, cid: str, uid: str) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO audio_features_cache "
            "(catalog_id, user_id, feature_source, enriched_at, analyzed_at) "
            "VALUES (:cid, :uid, :src, :ea, :aa) "
            "ON CONFLICT (catalog_id, user_id) DO NOTHING"
        ), {
            "cid": cid, "uid": uid,
            "src": json.dumps({"_status": "no_data_available"}),
            "ea": now, "aa": now,
        })


if __name__ == "__main__":
    asyncio.run(main())
