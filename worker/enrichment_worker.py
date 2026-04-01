"""Standalone enrichment worker with full artist discography support.

Connects to Railway PostgreSQL. Two phases:
  1. Enrich un-enriched library tracks (Deezer → ReccoBeats)
  2. Discover + enrich full discographies of relevant artists

Run on Mac: DATABASE_URL="postgresql://..." python3 enrichment_worker.py
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
CONCURRENCY = int(os.environ.get("CONCURRENCY", "30"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "500"))
SLEEP_SECONDS = int(os.environ.get("SLEEP_SECONDS", "5"))
GENRE_OVERLAP_THRESHOLD = 0.2

DEEZER_API = "https://api.deezer.com"
RECCOBEATS_API = "https://api.reccobeats.com/v1/analysis/audio-features"


async def main() -> None:
    if not DATABASE_URL:
        logger.error("Set DATABASE_URL to your Railway PostgreSQL URL")
        sys.exit(1)

    url = DATABASE_URL
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url, pool_size=20, max_overflow=20)
    logger.info("Worker started — concurrency=%d, batch=%d", CONCURRENCY, BATCH_SIZE)

    try:
        # Phase 1: Library tracks
        lib_count = await enrich_library_tracks(engine)
        logger.info("Phase 1 complete: %d library tracks processed", lib_count)

        # Phase 2: Artist discographies (main work)
        await enrich_artist_discographies(engine)

        # Loop: keep checking for new tracks
        while True:
            lib_count = await enrich_library_tracks(engine)
            if lib_count == 0:
                logger.info("All done. Sleeping %ds before next check.", SLEEP_SECONDS)
            await asyncio.sleep(SLEEP_SECONDS)
    finally:
        await engine.dispose()


# ── Phase 1: Library Tracks ──────────────────────────────────────────────────


async def enrich_library_tracks(engine) -> int:
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
    logger.info("Phase 1: Enriching %d library tracks...", len(tracks))
    stats = await _enrich_batch(engine, tracks)
    logger.info("Phase 1: %d ok, %d fail, %d skip", stats["ok"], stats["fail"], stats["skip"])
    return len(tracks)


# ── Phase 2: Artist Discographies ────────────────────────────────────────────


async def enrich_artist_discographies(engine) -> int:
    async with engine.begin() as conn:
        users_result = await conn.execute(sa.text(
            "SELECT DISTINCT user_id FROM service_connections"
        ))
        user_ids = [r[0] for r in users_result.fetchall()]

    logger.info("Phase 2: Found %d users with connected services", len(user_ids))

    total = 0
    for user_id in user_ids:
        count = await _discover_for_user(engine, user_id)
        total += count
    return total


async def _discover_for_user(engine, user_id: str) -> int:
    # Get genre profile
    async with engine.begin() as conn:
        result = await conn.execute(sa.text(
            "SELECT genre_vector FROM taste_profile_snapshots "
            "WHERE user_id = :uid ORDER BY computed_at DESC LIMIT 1"
        ), {"uid": user_id})
        row = result.first()

    if not row:
        logger.info("  User %s: no taste profile, skipping", user_id[:8])
        return 0

    genre_vector = row[0]
    if isinstance(genre_vector, str):
        try:
            genre_vector = json.loads(genre_vector)
        except (json.JSONDecodeError, TypeError):
            genre_vector = {}
    if not genre_vector:
        logger.info("  User %s: empty genre vector, skipping", user_id[:8])
        return 0

    user_genres = set(g.lower() for g in genre_vector.keys())

    # Get artists — simpler query that doesn't use json_array_elements
    async with engine.begin() as conn:
        artists_result = await conn.execute(sa.text(
            "SELECT artist_name, genre_names, COUNT(*) AS song_count "
            "FROM song_metadata_cache "
            "WHERE user_id = :uid AND artist_name != '' "
            "GROUP BY artist_name, genre_names "
            "ORDER BY song_count DESC"
        ), {"uid": user_id})
        raw_artists = artists_result.fetchall()

    # Aggregate artists (same artist may appear with different genre_names rows)
    artist_data: dict[str, dict] = {}
    for row in raw_artists:
        name = row[0]
        raw_genres = row[1]
        count = row[2]

        if name not in artist_data:
            artist_data[name] = {"genres": set(), "count": 0}
        artist_data[name]["count"] += count

        # Parse genres from various formats
        if raw_genres:
            genres_str = raw_genres if isinstance(raw_genres, str) else json.dumps(raw_genres)
            try:
                parsed = json.loads(genres_str)
                if isinstance(parsed, list):
                    artist_data[name]["genres"].update(g.lower() for g in parsed)
                elif isinstance(parsed, str):
                    artist_data[name]["genres"].add(parsed.lower())
            except (json.JSONDecodeError, TypeError):
                if isinstance(raw_genres, str) and raw_genres.strip():
                    artist_data[name]["genres"].add(raw_genres.lower())

    logger.info("  User %s: %d unique artists in library", user_id[:8], len(artist_data))

    # Filter relevant artists (skip outliers)
    relevant: list[str] = []
    skipped_outliers: list[str] = []
    for name, data in sorted(artist_data.items(), key=lambda x: x[1]["count"], reverse=True):
        artist_genres = data["genres"]
        if not artist_genres or not user_genres:
            if data["count"] >= 3:
                relevant.append(name)
            continue

        overlap = len(artist_genres & user_genres) / len(artist_genres)
        if overlap >= GENRE_OVERLAP_THRESHOLD:
            relevant.append(name)
        else:
            skipped_outliers.append(name)

    if skipped_outliers:
        logger.info(
            "  Skipped %d outlier artists: %s",
            len(skipped_outliers),
            ", ".join(skipped_outliers[:5]) + ("..." if len(skipped_outliers) > 5 else ""),
        )
    logger.info("  %d relevant artists to fetch discographies for", len(relevant))

    if not relevant:
        return 0

    # Fetch discographies concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _fetch(name: str) -> list[dict]:
        async with semaphore:
            return await _deezer_artist_top(name)

    all_tasks = [_fetch(name) for name in relevant[:100]]  # Cap at 100 artists
    disco_results = await asyncio.gather(*all_tasks, return_exceptions=True)

    all_tracks: list[dict] = []
    for i, result in enumerate(disco_results):
        if isinstance(result, list):
            for t in result:
                t["user_id"] = user_id
            all_tracks.extend(result)

    logger.info("  Fetched %d discography tracks from %d artists", len(all_tracks), len(relevant))

    # Deduplicate
    seen: set[str] = set()
    unique: list[dict] = []
    for t in all_tracks:
        cid = t.get("catalog_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(t)

    # Filter already-enriched
    async with engine.begin() as conn:
        existing = await conn.execute(sa.text(
            "SELECT catalog_id FROM audio_features_cache WHERE user_id = :uid"
        ), {"uid": user_id})
        existing_ids = {r[0] for r in existing.fetchall()}

    to_enrich = [t for t in unique if t["catalog_id"] not in existing_ids]

    if not to_enrich:
        logger.info("  All discography tracks already enriched")
        return 0

    logger.info("  Enriching %d new discography tracks (%d already cached)...",
                len(to_enrich), len(unique) - len(to_enrich))

    # Enrich in batches with progress
    total_ok = 0
    for i in range(0, len(to_enrich), BATCH_SIZE):
        batch = to_enrich[i:i + BATCH_SIZE]
        stats = await _enrich_batch(engine, batch)
        total_ok += stats["ok"]
        logger.info(
            "  Progress: %d/%d enriched (batch: %d ok, %d fail)",
            i + len(batch), len(to_enrich), stats["ok"], stats["fail"],
        )

    logger.info("  Done: %d discography tracks enriched for user %s", total_ok, user_id[:8])
    return len(to_enrich)


async def _deezer_artist_top(artist_name: str) -> list[dict]:
    """Fetch an artist's top 50 tracks from Deezer."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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

            resp2 = await client.get(
                f"{DEEZER_API}/artist/{artist_id}/top",
                params={"limit": 50},
            )
            items = resp2.json().get("data", [])

            return [
                {
                    "catalog_id": f"dz_{item['id']}",
                    "name": item.get("title", ""),
                    "artist_name": item.get("artist", {}).get("name", artist_name),
                    "isrc": "",
                    "service_source": "deezer",
                    "preview_url": item.get("preview", ""),
                }
                for item in items if item.get("id")
            ]
    except Exception:
        return []


# ── Shared Enrichment ────────────────────────────────────────────────────────


async def _enrich_batch(engine, tracks: list[dict]) -> dict[str, int]:
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
        elif r in ("reccobeats", "deezer"):
            stats["ok"] += 1
        elif r == "skipped":
            stats["skip"] += 1
        else:
            stats["fail"] += 1
    return stats


async def _enrich_track(engine, track: dict) -> str:
    catalog_id = track["catalog_id"]
    user_id = track.get("user_id", "")
    name = track.get("name", "")
    artist_name = track.get("artist_name", "")
    preview_url = track.get("preview_url", "")

    if not catalog_id or not user_id or not name:
        await _store_empty(engine, catalog_id, user_id)
        return "failed"

    features: dict = {}
    feature_source: dict = {}

    # Stage 1: Deezer (skip search if we already have preview_url from discography)
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

    # Stage 2: ReccoBeats
    if preview_url:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                audio_bytes = (await client.get(preview_url)).content

            if len(audio_bytes) > 1000:
                for attempt in range(3):
                    try:
                        async with httpx.AsyncClient(timeout=60.0) as rc:
                            rr = await rc.post(
                                RECCOBEATS_API,
                                files={"audioFile": ("p.mp3", audio_bytes, "audio/mpeg")},
                            )
                            if rr.status_code == 200:
                                data = rr.json()
                                for field, key in [
                                    ("energy", "energy"), ("danceability", "danceability"),
                                    ("acousticness", "acousticness"), ("valence_proxy", "valence"),
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
                                    features["loudness"] = round(max(0.0, min(1.0, (raw + 60) / 60)), 4)
                                    feature_source["loudness"] = "reccobeats"
                                break
                            elif rr.status_code == 429:
                                wait = float(rr.headers.get("Retry-After", "3"))
                                await asyncio.sleep(wait)
                            else:
                                break
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


# ── DB ───────────────────────────────────────────────────────────────────────


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
