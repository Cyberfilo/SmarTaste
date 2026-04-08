"""Standalone enrichment worker — runs independently of the web server.

On startup: full scan of all users → all artists → check enrichment → enrich gaps.
Then polls continuously for new unenriched tracks.

Key behaviors:
- Processes ALL artists from every user's library (not just top 20)
- Parses featuring artists ("Drake feat. Future" → searches "Drake" and "Future" separately)
- Checks global ISRC cache before calling external APIs
- Logs progress to the logging database

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
    """Startup scan → continuous enrichment loop."""
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

    # ── Phase 1: Startup scan — enrich all unenriched tracks ──────────
    logger.info("=== STARTUP SCAN: checking all users for unenriched tracks ===")
    try:
        startup_stats = await _startup_scan(engine, log_writer=log_writer)
        logger.info(
            "Startup scan complete: %d unenriched tracks found, %d enriched",
            startup_stats["unenriched_found"],
            startup_stats["enriched"],
        )
    except Exception:
        logger.exception("Startup scan failed, continuing to poll loop")

    # ── Phase 2: Continuous loop — discover new artists + enrich ──────
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
                "%d enriched, %d skipped",
                cycle, duration,
                stats["artists_processed"],
                stats["tracks_fetched"],
                stats["tracks_enriched"],
                stats["tracks_skipped"],
            )
        except Exception:
            logger.exception("Cycle %d failed", cycle)

        await asyncio.sleep(POLL_INTERVAL)


# ── Startup Scan ──────────────────────────────────────────────────────────


async def _startup_scan(
    engine,
    *,
    log_writer=None,
) -> dict[str, int]:
    """Scan ALL users → ALL their songs → find unenriched → enrich them.

    This runs once on startup to catch up on any songs that were cached
    but never enriched (e.g. from library imports, calibration, etc.).
    """
    from musicmind.db.schema import audio_features_cache, song_metadata_cache, users
    from musicmind.engine.enrichment.orchestrator import enrich_tracks

    stats = {"unenriched_found": 0, "enriched": 0}

    # Get all users
    async with engine.begin() as conn:
        result = await conn.execute(sa.select(users.c.id))
        user_ids = [row.id for row in result]

    for user_id in user_ids:
        try:
            # Find songs that have no audio features (or only empty marker rows)
            async with engine.begin() as conn:
                enriched_ids_q = sa.select(audio_features_cache.c.catalog_id).where(
                    sa.and_(
                        audio_features_cache.c.user_id == user_id,
                        audio_features_cache.c.energy.isnot(None),
                    )
                )
                result = await conn.execute(
                    sa.select(song_metadata_cache).where(
                        sa.and_(
                            song_metadata_cache.c.user_id == user_id,
                            song_metadata_cache.c.catalog_id.notin_(enriched_ids_q),
                        )
                    ).limit(BATCH_SIZE * 4)  # Cap per user on startup
                )
                rows = result.fetchall()

            if not rows:
                continue

            tracks = []
            for row in rows:
                genres = row.genre_names
                if isinstance(genres, str):
                    try:
                        genres = json.loads(genres)
                    except (json.JSONDecodeError, TypeError):
                        genres = []
                tracks.append({
                    "catalog_id": row.catalog_id,
                    "name": row.name or "",
                    "artist_name": row.artist_name or "",
                    "isrc": row.isrc or "",
                    "service_source": getattr(row, "service_source", ""),
                    "genre_names": genres,
                })

            stats["unenriched_found"] += len(tracks)
            logger.info(
                "User %s: %d unenriched tracks found in startup scan",
                user_id[:8], len(tracks),
            )

            enrich_result = await enrich_tracks(
                engine, tracks, user_id=user_id,
            )
            enriched = (
                enrich_result.get("deezer", 0)
                + enrich_result.get("reccobeats", 0)
                + enrich_result.get("soundstat", 0)
            )
            stats["enriched"] += enriched

        except Exception:
            logger.warning(
                "Startup scan failed for user %s", user_id[:8], exc_info=True,
            )

    return stats


# ── Cycle Logic ───────────────────────────────────────────────────────────


async def _run_cycle(
    engine,
    encryption,
    settings,
    *,
    log_writer=None,
) -> dict[str, int]:
    """One enrichment cycle across all users."""
    from musicmind.db.schema import users

    stats = {
        "artists_processed": 0,
        "tracks_fetched": 0,
        "tracks_enriched": 0,
        "tracks_skipped": 0,
    }

    async with engine.begin() as conn:
        result = await conn.execute(sa.select(users.c.id))
        user_ids = [row.id for row in result]

    if not user_ids:
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
            logger.warning("Failed to process user %s", user_id[:8], exc_info=True)

    return stats


async def _process_user(
    engine,
    encryption,
    settings,
    *,
    user_id: str,
    log_writer=None,
) -> dict[str, int]:
    """Process one user: get ALL artists from library, fetch discographies, enrich."""
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
    from musicmind.engine.profile import parse_artists

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

    # ── Get ALL distinct artists from user's library ──────────────────
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(sa.distinct(song_metadata_cache.c.artist_name)).where(
                song_metadata_cache.c.user_id == user_id,
            )
        )
        raw_artist_names = [row[0] for row in result if row[0]]

    if not raw_artist_names:
        return stats

    # Parse featuring artists: "Drake feat. Future" → ["Drake", "Future"]
    # Deduplicate by lowercase to avoid searching "drake" twice
    seen_artists: set[str] = set()
    artist_names: list[str] = []
    for raw_name in raw_artist_names:
        parsed = parse_artists(raw_name)
        for name, _weight in parsed:
            key = name.strip().lower()
            if key and key not in seen_artists and len(key) > 1:
                seen_artists.add(key)
                artist_names.append(name.strip())

    logger.info(
        "User %s: %d raw artist names → %d unique artists (after featuring parse)",
        user_id[:8], len(raw_artist_names), len(artist_names),
    )

    # ── For each artist: search → fetch top tracks → cache → enrich ───
    all_tracks: list[dict[str, Any]] = []

    for artist_name in artist_names:
        try:
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
            async with engine.begin() as conn:
                for track in tracks:
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
                            genre_names=json.dumps(track.get("genre_names", [])),
                            duration_ms=track.get("duration_ms"),
                            release_date=track.get("release_date"),
                            isrc=track.get("isrc"),
                            preview_url=track.get("preview_url", ""),
                            service_source=service,
                        )
                    )
                    stats["tracks_fetched"] += 1

            all_tracks.extend(tracks)

            # Log progress every 10 artists
            if stats["artists_processed"] % 10 == 0:
                logger.info(
                    "User %s: processed %d/%d artists, %d tracks so far",
                    user_id[:8], stats["artists_processed"],
                    len(artist_names), len(all_tracks),
                )

        except Exception:
            logger.debug(
                "Failed to process artist '%s' for user %s",
                artist_name, user_id[:8],
            )

    if not all_tracks:
        return stats

    # ── Check which tracks need enrichment ────────────────────────────
    tracks_to_enrich: list[dict[str, Any]] = []
    async with engine.begin() as conn:
        catalog_ids = [
            t.get("catalog_id", "") for t in all_tracks if t.get("catalog_id")
        ]
        if catalog_ids:
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

    # Deduplicate tracks by catalog_id before enrichment
    seen_cids: set[str] = set()
    for t in all_tracks:
        cid = t.get("catalog_id", "")
        if cid and cid not in enriched_ids and cid not in seen_cids:
            seen_cids.add(cid)
            tracks_to_enrich.append(t)
        else:
            stats["tracks_skipped"] += 1

    if tracks_to_enrich:
        # Enumerate all songs that will be enriched
        logger.info(
            "User %s: ENRICHMENT PLAN — %d tracks to enrich, %d already done, "
            "%d total from %d artists. Songs:",
            user_id[:8], len(tracks_to_enrich), stats["tracks_skipped"],
            len(all_tracks), stats["artists_processed"],
        )
        for i, t in enumerate(tracks_to_enrich[:50]):  # Log first 50
            logger.info(
                "  [%d] %s - %s (isrc=%s)",
                i + 1, t.get("artist_name", "?"), t.get("name", "?"),
                t.get("isrc", "—"),
            )
        if len(tracks_to_enrich) > 50:
            logger.info("  ... and %d more", len(tracks_to_enrich) - 50)
        enrich_result = await enrich_tracks(
            engine, tracks_to_enrich, user_id=user_id,
            soundstat_api_key=settings.soundstat_api_key,
        )
        stats["tracks_enriched"] = (
            enrich_result.get("deezer", 0)
            + enrich_result.get("reccobeats", 0)
            + enrich_result.get("soundstat", 0)
        )

        # Last.fm enrichment: tags + similar tracks (collaborative filtering)
        if settings.lastfm_api_key:
            await _enrich_lastfm(
                engine, all_tracks,
                api_key=settings.lastfm_api_key,
            )

        if log_writer:
            log_writer.log_enrichment(
                user_id=user_id,
                catalog_id=f"batch_{len(tracks_to_enrich)}",
                stage="worker_cycle",
                result=f"enriched_{stats['tracks_enriched']}",
            )
    else:
        logger.info("User %s: all tracks already enriched", user_id[:8])

    return stats


# ── Last.fm Enrichment ────────────────────────────────────────────────────


async def _enrich_lastfm(
    engine,
    tracks: list[dict[str, Any]],
    *,
    api_key: str,
) -> None:
    """Fetch Last.fm tags + similar tracks for a batch of tracks.

    Runs after audio enrichment. Fetches tags for each track
    and similar tracks for the user's top songs.
    """
    from musicmind.engine.enrichment.lastfm import (
        fetch_similar_tracks,
        fetch_track_tags,
    )

    tag_count = 0
    similar_count = 0

    # Deduplicate by (artist, title) to avoid redundant API calls
    seen: set[str] = set()

    for track in tracks:
        artist = track.get("artist_name", "")
        title = track.get("name", "")
        if not artist or not title:
            continue

        key = f"{artist.lower()}:{title.lower()}"
        if key in seen:
            continue
        seen.add(key)

        # Fetch tags (always — they're cheap and useful)
        tags = await fetch_track_tags(api_key, artist, title, engine=engine)
        if tags:
            tag_count += 1

        # Fetch similar tracks (for enrichment — builds collaborative graph)
        similar = await fetch_similar_tracks(
            api_key, artist, title, engine=engine, limit=15,
        )
        if similar:
            similar_count += 1

    if tag_count > 0 or similar_count > 0:
        logger.info(
            "Last.fm enrichment: %d tracks tagged, %d with similar tracks",
            tag_count, similar_count,
        )


# ── Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(main())
