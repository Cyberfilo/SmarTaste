"""Standalone enrichment worker — runs independently of the web server.

Continuously polls the database for all users' top artists, fetches their
full discographies from connected services, caches song metadata, and
enriches tracks via the Deezer → ReccoBeats pipeline.

Designed to run as a separate Railway service with its own rate budget
so it doesn't compete with user-facing API requests.

Usage:
    python -m musicmind.worker

Environment variables:
    DATABASE_URL              — PostgreSQL connection string
    MUSICMIND_FERNET_KEY      — Fernet key for decrypting service tokens
    MUSICMIND_LOGS_DATABASE_URL — Optional logging database
    WORKER_CONCURRENCY        — Tracks enriched in parallel (default 5)
    WORKER_BATCH_SIZE         — Tracks per enrichment batch (default 50)
    WORKER_POLL_INTERVAL      — Seconds between cycles (default 60)
    WORKER_ARTIST_DEPTH       — Top songs per artist to fetch (default 25)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

import httpx
import sqlalchemy as sa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("musicmind.worker")

# ── Configuration ─────────────────────────────────────────────────────────

CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "5"))
BATCH_SIZE = int(os.environ.get("WORKER_BATCH_SIZE", "50"))
POLL_INTERVAL = int(os.environ.get("WORKER_POLL_INTERVAL", "60"))
ARTIST_DEPTH = int(os.environ.get("WORKER_ARTIST_DEPTH", "25"))


# ── Main Loop ─────────────────────────────────────────────────────────────


async def main() -> None:
    """Main worker loop: discover → fetch → cache → enrich → sleep → repeat."""
    from musicmind.config import Settings
    from musicmind.db.engine import create_engine
    from musicmind.security.encryption import EncryptionService

    settings = Settings()
    engine = create_engine(settings.database_url)
    encryption = EncryptionService(settings.fernet_key)

    # Optional: connect to logs DB
    log_writer = None
    logs_engine = None
    if settings.logs_database_url:
        try:
            from musicmind.db.logs import LogWriter, create_logs_engine, init_logs_schema

            logs_engine = create_logs_engine(settings.logs_database_url)
            await init_logs_schema(logs_engine)
            log_writer = LogWriter(logs_engine)
            log_writer.start()
            logger.info("Logging database connected")
        except Exception:
            logger.warning("Failed to connect logging database", exc_info=True)

    logger.info(
        "Worker started: concurrency=%d, batch=%d, poll=%ds, artist_depth=%d",
        CONCURRENCY, BATCH_SIZE, POLL_INTERVAL, ARTIST_DEPTH,
    )

    cycle = 0
    while True:
        cycle += 1
        start = time.monotonic()
        try:
            stats = await _run_cycle(
                engine, encryption, settings,
                log_writer=log_writer,
            )
            duration = round(time.monotonic() - start, 1)
            logger.info(
                "Cycle %d complete in %.1fs: %d artists, %d tracks fetched, "
                "%d enriched, %d skipped (already enriched)",
                cycle, duration,
                stats["artists_processed"],
                stats["tracks_fetched"],
                stats["tracks_enriched"],
                stats["tracks_skipped"],
            )
        except Exception:
            logger.exception("Cycle %d failed", cycle)

        await asyncio.sleep(POLL_INTERVAL)


# ── Cycle Logic ───────────────────────────────────────────────────────────


async def _run_cycle(
    engine,
    encryption,
    settings,
    *,
    log_writer=None,
) -> dict[str, int]:
    """One enrichment cycle across all users."""
    from musicmind.db.schema import (
        users,
    )

    stats = {
        "artists_processed": 0,
        "tracks_fetched": 0,
        "tracks_enriched": 0,
        "tracks_skipped": 0,
    }

    # Get all users
    async with engine.begin() as conn:
        result = await conn.execute(sa.select(users.c.id))
        user_ids = [row.id for row in result]

    if not user_ids:
        logger.info("No users found, sleeping")
        return stats

    for user_id in user_ids:
        try:
            user_stats = await _process_user(
                engine, encryption, settings,
                user_id=user_id,
                log_writer=log_writer,
            )
            for k, v in user_stats.items():
                stats[k] = stats.get(k, 0) + v
        except Exception:
            logger.warning("Failed to process user %s", user_id, exc_info=True)

    return stats


async def _process_user(
    engine,
    encryption,
    settings,
    *,
    user_id: str,
    log_writer=None,
) -> dict[str, int]:
    """Process one user: get top artists, fetch discographies, enrich."""
    from musicmind.api.recommendations.fetch import (
        _fetch_artist_top_tracks,
        _search_artist_id,
    )
    from musicmind.api.services.service import (
        detect_apple_music_storefront,
        generate_apple_developer_token,
        get_user_connections,
    )
    from musicmind.db.schema import (
        audio_features_cache,
        service_connections,
        song_metadata_cache,
    )
    from musicmind.engine.enrichment.orchestrator import enrich_tracks

    stats = {
        "artists_processed": 0,
        "tracks_fetched": 0,
        "tracks_enriched": 0,
        "tracks_skipped": 0,
    }

    # Get user's service connections
    connections = await get_user_connections(engine, user_id=user_id)
    if not connections:
        return stats

    conn_data = connections[0]
    service = conn_data["service"]

    # Get credentials
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(service_connections).where(
                sa.and_(
                    service_connections.c.user_id == user_id,
                    service_connections.c.service == service,
                )
            )
        )
        row = result.first()

    if not row:
        return stats

    access_token = encryption.decrypt(row.access_token_encrypted)
    developer_token = None
    storefront = "us"

    if service == "apple_music":
        developer_token = generate_apple_developer_token(
            settings.apple_team_id, settings.apple_key_id,
            settings.apple_private_key_path,
            private_key_b64=settings.apple_private_key_b64,
        )
        storefront = await detect_apple_music_storefront(
            access_token, developer_token,
        )

    # Get top artists from profile
    async with engine.begin() as conn:
        from musicmind.db.schema import taste_profile_snapshots

        result = await conn.execute(
            sa.select(taste_profile_snapshots)
            .where(taste_profile_snapshots.c.user_id == user_id)
            .order_by(taste_profile_snapshots.c.computed_at.desc())
            .limit(1)
        )
        profile_row = result.first()

    if not profile_row:
        return stats

    top_artists_raw = profile_row.top_artists
    if isinstance(top_artists_raw, str):
        try:
            top_artists_raw = json.loads(top_artists_raw)
        except (json.JSONDecodeError, TypeError):
            top_artists_raw = []

    artist_names = [
        a["name"] for a in top_artists_raw[:20]
        if isinstance(a, dict) and a.get("name")
    ]

    if not artist_names:
        return stats

    # For each artist: search → fetch top tracks → cache → enrich
    all_tracks: list[dict[str, Any]] = []

    for artist_name in artist_names:
        try:
            # Check what we already have cached for this artist
            async with engine.begin() as conn:
                existing = await conn.execute(
                    sa.select(song_metadata_cache.c.catalog_id).where(
                        sa.and_(
                            song_metadata_cache.c.user_id == user_id,
                            sa.func.lower(song_metadata_cache.c.artist_name)
                            == artist_name.lower(),
                        )
                    )
                )
                cached_ids = {r.catalog_id for r in existing}

            # Search artist and fetch top tracks
            artist_id = await _search_artist_id(
                service, access_token, artist_name,
                developer_token=developer_token,
                storefront=storefront,
            )
            if not artist_id:
                continue

            async with httpx.AsyncClient(timeout=30.0) as client:
                tracks = await _fetch_artist_top_tracks(
                    client, service, access_token, artist_id,
                    developer_token=developer_token,
                    storefront=storefront,
                    limit=ARTIST_DEPTH,
                )

            stats["artists_processed"] += 1

            # Cache new tracks into song_metadata_cache
            new_tracks = [t for t in tracks if t.get("catalog_id") not in cached_ids]
            if new_tracks:
                async with engine.begin() as conn:
                    for track in new_tracks:
                        cid = track.get("catalog_id", "")
                        if not cid:
                            continue
                        exists = await conn.execute(
                            sa.select(song_metadata_cache.c.catalog_id).where(
                                sa.and_(
                                    song_metadata_cache.c.catalog_id == cid,
                                    song_metadata_cache.c.user_id == user_id,
                                )
                            )
                        )
                        if exists.first():
                            continue
                        await conn.execute(
                            song_metadata_cache.insert().values(
                                catalog_id=cid,
                                user_id=user_id,
                                name=track.get("name", ""),
                                artist_name=track.get("artist_name", ""),
                                album_name=track.get("album_name", ""),
                                genre_names=json.dumps(
                                    track.get("genre_names", [])
                                ),
                                duration_ms=track.get("duration_ms"),
                                release_date=track.get("release_date"),
                                isrc=track.get("isrc"),
                                preview_url=track.get("preview_url", ""),
                                service_source=service,
                            )
                        )
                        stats["tracks_fetched"] += 1

            all_tracks.extend(tracks)

        except Exception:
            logger.debug("Failed to process artist %s for user %s", artist_name, user_id)

    if not all_tracks:
        return stats

    # Check which tracks already have enrichment (per-user or global)
    tracks_to_enrich: list[dict[str, Any]] = []
    async with engine.begin() as conn:
        catalog_ids = [t.get("catalog_id", "") for t in all_tracks if t.get("catalog_id")]
        if catalog_ids:
            # Check per-user cache
            enriched_result = await conn.execute(
                sa.select(audio_features_cache.c.catalog_id).where(
                    sa.and_(
                        audio_features_cache.c.user_id == user_id,
                        audio_features_cache.c.catalog_id.in_(catalog_ids),
                        audio_features_cache.c.energy.isnot(None),
                    )
                )
            )
            enriched_ids = {r.catalog_id for r in enriched_result}
        else:
            enriched_ids = set()

    for t in all_tracks:
        cid = t.get("catalog_id", "")
        if cid and cid not in enriched_ids:
            tracks_to_enrich.append(t)
        else:
            stats["tracks_skipped"] += 1

    if tracks_to_enrich:
        logger.info(
            "User %s: enriching %d tracks (%d skipped)",
            user_id[:8], len(tracks_to_enrich), stats["tracks_skipped"],
        )
        result = await enrich_tracks(
            engine, tracks_to_enrich, user_id=user_id,
            soundstat_api_key=settings.soundstat_api_key,
        )
        stats["tracks_enriched"] = result.get("deezer", 0) + result.get(
            "reccobeats", 0
        ) + result.get("soundstat", 0)

        if log_writer:
            for t in tracks_to_enrich:
                log_writer.log_enrichment(
                    user_id=user_id,
                    catalog_id=t.get("catalog_id", ""),
                    stage="worker",
                    result="enriched",
                )

    return stats


# ── Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(main())
