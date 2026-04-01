"""Standalone enrichment worker — runs independently from the main API.

Deploy on Render (free tier), Fly.io, or any Python host.
Connects directly to the Railway PostgreSQL database.
Processes un-enriched tracks in concurrent batches.

Usage:
    # Set DATABASE_URL to your Railway PostgreSQL connection string
    DATABASE_URL=postgresql+asyncpg://... python enrichment_worker.py

    # Optional: SoundStat for premium features
    SOUNDSTAT_API_KEY=... DATABASE_URL=... python enrichment_worker.py

Environment variables:
    DATABASE_URL          — Railway PostgreSQL (required)
    SOUNDSTAT_API_KEY     — Optional SoundStat API key
    CONCURRENCY           — Parallel tracks (default: 5)
    BATCH_SIZE            — Tracks per cycle (default: 50)
    SLEEP_SECONDS         — Pause between cycles (default: 30)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("worker")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SOUNDSTAT_API_KEY = os.environ.get("SOUNDSTAT_API_KEY", "")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "5"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))
SLEEP_SECONDS = int(os.environ.get("SLEEP_SECONDS", "30"))

DEEZER_API = "https://api.deezer.com"
RECCOBEATS_API = "https://api.reccobeats.com/v1/analysis/audio-features"


async def main() -> None:
    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    url = DATABASE_URL
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url, pool_size=5, max_overflow=5)
    logger.info("Enrichment worker started (concurrency=%d, batch=%d)", CONCURRENCY, BATCH_SIZE)

    try:
        while True:
            processed = await run_cycle(engine)
            if processed == 0:
                logger.info("No un-enriched tracks found, sleeping %ds", SLEEP_SECONDS)
            else:
                logger.info("Processed %d tracks, continuing", processed)
            await asyncio.sleep(SLEEP_SECONDS)
    finally:
        await engine.dispose()


async def run_cycle(engine) -> int:
    """Find and enrich un-enriched tracks across all users."""
    # Find tracks missing audio features
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
        {
            "catalog_id": r[0], "user_id": r[1], "name": r[2],
            "artist_name": r[3], "isrc": r[4] or "",
            "service_source": r[5] or "",
        }
        for r in rows
    ]

    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(
        *[_bounded_enrich(engine, t, semaphore) for t in tracks],
        return_exceptions=True,
    )

    enriched = sum(1 for r in results if r in ("deezer", "reccobeats", "soundstat"))
    failed = sum(1 for r in results if r == "failed" or isinstance(r, Exception))
    skipped = sum(1 for r in results if r == "skipped")
    logger.info(
        "Cycle: %d enriched, %d failed, %d skipped out of %d",
        enriched, failed, skipped, len(tracks),
    )
    return len(tracks)


async def _bounded_enrich(engine, track: dict, semaphore: asyncio.Semaphore) -> str:
    async with semaphore:
        return await _enrich_track(engine, track)


async def _enrich_track(engine, track: dict) -> str:
    """Enrich a single track: Deezer → ReccoBeats → store."""
    catalog_id = track["catalog_id"]
    user_id = track["user_id"]
    name = track.get("name", "")
    artist_name = track.get("artist_name", "")

    if not name or not artist_name:
        await _store_empty(engine, catalog_id, user_id)
        return "failed"

    # Stage 1: Deezer search → preview URL + BPM
    preview_url = None
    features: dict = {}
    feature_source: dict = {}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{DEEZER_API}/search", params={"q": f"{name} {artist_name}", "limit": 1})
            items = resp.json().get("data", [])
            if items:
                did = items[0]["id"]
                resp2 = await client.get(f"{DEEZER_API}/track/{did}")
                full = resp2.json()
                bpm = full.get("bpm")
                if bpm and bpm > 0:
                    features["tempo"] = float(bpm)
                    feature_source["tempo"] = "deezer"
                preview_url = full.get("preview")
    except Exception:
        pass

    # Stage 2: ReccoBeats — upload preview
    if preview_url:
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                preview_resp = await client.get(preview_url)
                audio_bytes = preview_resp.content
                if len(audio_bytes) > 1000:
                    files = {"audioFile": ("p.mp3", audio_bytes, "audio/mpeg")}
                    async with httpx.AsyncClient(timeout=60.0) as rc:
                        rr = await rc.post(RECCOBEATS_API, files=files)
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
                        elif rr.status_code == 429:
                            retry_after = float(rr.headers.get("Retry-After", "5"))
                            await asyncio.sleep(retry_after)
        except Exception:
            pass

    if features:
        await _store(engine, catalog_id, user_id, features, feature_source)
        return "reccobeats" if "energy" in features else "deezer"
    else:
        await _store_empty(engine, catalog_id, user_id)
        return "failed"


async def _store(engine, catalog_id: str, user_id: str, features: dict, source: dict) -> None:
    from datetime import UTC, datetime
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
            "cid": catalog_id, "uid": user_id,
            "tempo": features.get("tempo"), "energy": features.get("energy"),
            "dance": features.get("danceability"), "acoustic": features.get("acousticness"),
            "valence": features.get("valence_proxy"), "inst": features.get("instrumentalness"),
            "loud": features.get("loudness"), "src": json.dumps(source),
            "ea": now, "aa": now,
        })


async def _store_empty(engine, catalog_id: str, user_id: str) -> None:
    """Mark track as attempted so it's not retried."""
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO audio_features_cache "
            "(catalog_id, user_id, feature_source, enriched_at, analyzed_at) "
            "VALUES (:cid, :uid, :src, :ea, :aa) "
            "ON CONFLICT (catalog_id, user_id) DO NOTHING"
        ), {
            "cid": catalog_id, "uid": user_id,
            "src": json.dumps({"_status": "no_data_available"}),
            "ea": now, "aa": now,
        })


if __name__ == "__main__":
    asyncio.run(main())
