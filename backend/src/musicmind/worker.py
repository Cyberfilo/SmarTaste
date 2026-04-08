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

    # ── Phase 0: Clean orphaned audio_features_cache rows ──────────
    logger.info("=== ORPHAN CLEANUP: removing stale audio_features_cache rows ===")
    try:
        from musicmind.api.admin.progress import cleanup_orphaned_features

        cleanup_result = await cleanup_orphaned_features(engine)
        if cleanup_result["total_deleted"] > 0:
            logger.info(
                "Orphan cleanup: deleted %d stale audio_features_cache rows",
                cleanup_result["total_deleted"],
            )
        else:
            logger.info("Orphan cleanup: no orphaned rows found")
        if log_writer:
            log_writer.log_enrichment(
                user_id="system",
                catalog_id="cleanup",
                stage="orphan_cleanup",
                result=f"deleted_{cleanup_result['total_deleted']}",
            )
    except Exception:
        logger.exception("Orphan cleanup failed, continuing")

    # ── Phase 1: Startup scan — enrich all unenriched tracks ──────────
    logger.info("=== STARTUP SCAN: checking all users for unenriched tracks ===")
    try:
        startup_stats = await _startup_scan(engine, log_writer=log_writer)
        logger.info(
            "Startup scan complete: %d unenriched tracks found, %d enriched",
            startup_stats["unenriched_found"],
            startup_stats["enriched"],
        )
        if log_writer:
            log_writer.log_enrichment(
                user_id="system",
                catalog_id=f"batch_{startup_stats['unenriched_found']}",
                stage="startup_scan",
                result=f"enriched_{startup_stats['enriched']}",
            )
    except Exception:
        logger.exception("Startup scan failed, continuing to poll loop")

    # ── Phase 1b: Backfill new signals on already-enriched songs ──────
    logger.info("=== BACKFILL: checking for songs missing new enrichment signals ===")
    try:
        backfill_stats = await _backfill_new_signals(engine, settings)
        tags = backfill_stats.get("tags", 0)
        credits = backfill_stats.get("credits", 0)
        lyrics = backfill_stats.get("lyrics", 0)
        logger.info(
            "Backfill complete: %d tags added, %d credits fetched, %d lyrics embedded",
            tags, credits, lyrics,
        )
        if log_writer:
            log_writer.log_enrichment(
                user_id="system",
                catalog_id=f"backfill_{tags + credits + lyrics}",
                stage="backfill",
                result=f"tags_{tags}_credits_{credits}_lyrics_{lyrics}",
            )
    except Exception:
        logger.exception("Backfill failed, continuing to poll loop")

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
            if log_writer:
                log_writer.log_enrichment(
                    user_id="system",
                    catalog_id=f"cycle_{cycle}",
                    stage="worker_cycle",
                    result=(
                        f"enriched_{stats['tracks_enriched']}"
                        if stats["tracks_enriched"] > 0
                        else "idle"
                    ),
                    duration_ms=int(duration * 1000),
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

    # ── Get distinct artists from user's LIBRARY ONLY ─────────────────
    # Only library songs (not worker-discovered) to prevent snowball effect
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(sa.distinct(song_metadata_cache.c.artist_name)).where(
                sa.and_(
                    song_metadata_cache.c.user_id == user_id,
                    sa.or_(
                        song_metadata_cache.c.library_id.isnot(None),
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                    ),
                )
            )
        )
        raw_artist_names = [row[0] for row in result if row[0]]

    if not raw_artist_names:
        return stats

    # Parse featuring artists: "Drake feat. Future" → ["Drake", "Future"]
    # Primary artists (in library) get full depth, featuring artists get 3 tracks
    seen_artists: set[str] = set()
    primary_artists: list[str] = []  # In library → full ARTIST_DEPTH
    featured_artists: list[str] = []  # From featurings → 3 tracks only

    for raw_name in raw_artist_names:
        parsed = parse_artists(raw_name)
        for i, (name, weight) in enumerate(parsed):
            key = name.strip().lower()
            if key and key not in seen_artists and len(key) > 1:
                seen_artists.add(key)
                if i == 0 and weight >= 1.0:
                    primary_artists.append(name.strip())
                else:
                    featured_artists.append(name.strip())

    # Cap featured artists at 25% of primary count to keep DB focused
    max_featured = max(5, len(primary_artists) // 4)
    featured_artists = featured_artists[:max_featured]

    logger.info(
        "User %s: %d library artists → %d primary + %d featured (capped at %d)",
        user_id[:8], len(raw_artist_names),
        len(primary_artists), len(featured_artists), max_featured,
    )

    # ── For each artist: search → fetch top tracks → cache → enrich ───
    all_tracks: list[dict[str, Any]] = []

    # Primary artists first (full depth), then featured (3 tracks)
    all_artists = [(a, ARTIST_DEPTH) for a in primary_artists] + [
        (a, 3) for a in featured_artists
    ]

    for artist_name, depth in all_artists:
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
                    limit=depth,
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
                    len(all_artists), len(all_tracks),
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

        # MusicBrainz credits: producers, songwriters (knowledge graph)
        await _enrich_musicbrainz_credits(engine, tracks_to_enrich)

        # Genius lyrics: scrape + embed for semantic similarity
        await _enrich_lyrics(engine, tracks_to_enrich, user_id=user_id)

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


# ── Backfill New Signals ──────────────────────────────────────────────────


async def _backfill_new_signals(
    engine,
    settings,
) -> dict[str, int]:
    """Run new enrichment stages on songs that already have audio features.

    Targets songs enriched before Last.fm, MusicBrainz credits, and lyrics
    were added. Checks each signal independently — only runs what's missing.
    """
    from musicmind.db.schema import (
        audio_embeddings,
        lastfm_tags_cache,
        song_metadata_cache,
        users,
    )

    stats = {"tags": 0, "credits": 0, "lyrics": 0}

    async with engine.begin() as conn:
        result = await conn.execute(sa.select(users.c.id))
        user_ids = [row.id for row in result]

    for user_id in user_ids:
        # Get all songs for this user
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    song_metadata_cache.c.catalog_id,
                    song_metadata_cache.c.name,
                    song_metadata_cache.c.artist_name,
                    song_metadata_cache.c.isrc,
                ).where(song_metadata_cache.c.user_id == user_id)
            )
            songs = result.fetchall()

        if not songs:
            continue

        # ── Last.fm tags backfill ─────────────────────────────
        if settings.lastfm_api_key:
            songs_without_tags: list[dict[str, Any]] = []
            async with engine.begin() as conn:
                for song in songs:
                    if not song.artist_name or not song.name:
                        continue
                    entity_id = f"track:{song.artist_name.lower()}:{song.name.lower()}"
                    existing = await conn.execute(
                        sa.select(lastfm_tags_cache.c.entity_id).where(
                            lastfm_tags_cache.c.entity_id == entity_id
                        )
                    )
                    if not existing.first():
                        songs_without_tags.append({
                            "artist_name": song.artist_name,
                            "name": song.name,
                            "catalog_id": song.catalog_id,
                        })

            if songs_without_tags:
                logger.info(
                    "User %s: %d songs missing Last.fm tags, backfilling...",
                    user_id[:8], len(songs_without_tags),
                )
                await _enrich_lastfm(
                    engine, songs_without_tags,
                    api_key=settings.lastfm_api_key,
                )
                stats["tags"] += len(songs_without_tags)

        # ── MusicBrainz credits backfill ──────────────────────
        songs_without_credits: list[dict[str, Any]] = []
        async with engine.begin() as conn:
            from musicmind.db.schema import kg_relationships

            for song in songs:
                isrc = song.isrc
                if not isrc:
                    continue
                source_mbid = f"isrc:{isrc.upper()}"
                existing = await conn.execute(
                    sa.select(kg_relationships.c.id).where(
                        kg_relationships.c.source_mbid == source_mbid
                    ).limit(1)
                )
                if not existing.first():
                    songs_without_credits.append({
                        "isrc": isrc,
                        "catalog_id": song.catalog_id,
                    })

        if songs_without_credits:
            logger.info(
                "User %s: %d songs missing MusicBrainz credits, backfilling...",
                user_id[:8], len(songs_without_credits),
            )
            await _enrich_musicbrainz_credits(engine, songs_without_credits)
            stats["credits"] += len(songs_without_credits)

        # ── Lyrics embeddings backfill ────────────────────────
        songs_without_lyrics: list[dict[str, Any]] = []
        async with engine.begin() as conn:
            for song in songs:
                if not song.artist_name or not song.name:
                    continue
                existing = await conn.execute(
                    sa.select(audio_embeddings.c.catalog_id).where(
                        sa.and_(
                            audio_embeddings.c.catalog_id == song.catalog_id,
                            audio_embeddings.c.user_id == user_id,
                            audio_embeddings.c.model_version == "lyrics-minilm-v2",
                        )
                    )
                )
                if not existing.first():
                    songs_without_lyrics.append({
                        "artist_name": song.artist_name,
                        "name": song.name,
                        "catalog_id": song.catalog_id,
                        "isrc": song.isrc or "",
                    })

        if songs_without_lyrics:
            logger.info(
                "User %s: %d songs missing lyrics embeddings, backfilling...",
                user_id[:8], len(songs_without_lyrics),
            )
            await _enrich_lyrics(engine, songs_without_lyrics, user_id=user_id)
            stats["lyrics"] += len(songs_without_lyrics)

    return stats


# ── Lyrics Enrichment ─────────────────────────────────────────────────────


async def _enrich_lyrics(
    engine,
    tracks: list[dict[str, Any]],
    *,
    user_id: str,
) -> None:
    """Fetch lyrics from Genius and embed with sentence-transformers.

    Stores embeddings in audio_embeddings table with model_version="lyrics-minilm-v2".
    Skips tracks that already have lyric embeddings.
    """
    from musicmind.db.schema import audio_embeddings
    from musicmind.engine.enrichment.genius import fetch_and_embed_lyrics

    embedded = 0
    seen: set[str] = set()

    for track in tracks:
        artist = track.get("artist_name", "")
        title = track.get("name", "")
        catalog_id = track.get("catalog_id", "")
        if not artist or not title or not catalog_id:
            continue

        key = f"{artist.lower()}:{title.lower()}"
        if key in seen:
            continue
        seen.add(key)

        # Check if already embedded
        async with engine.begin() as conn:
            existing = await conn.execute(
                sa.select(audio_embeddings.c.catalog_id).where(
                    sa.and_(
                        audio_embeddings.c.catalog_id == catalog_id,
                        audio_embeddings.c.user_id == user_id,
                        audio_embeddings.c.model_version == "lyrics-minilm-v2",
                    )
                )
            )
            if existing.first():
                continue

        # Fetch + embed
        _lyrics, embedding = await fetch_and_embed_lyrics(artist, title)
        if embedding:
            async with engine.begin() as conn:
                await conn.execute(sa.text(
                    "INSERT INTO audio_embeddings "
                    "(catalog_id, user_id, embedding, isrc, model_version, analyzed_at) "
                    "VALUES (:cid, :uid, :emb, :isrc, :model, NOW()) "
                    "ON CONFLICT (catalog_id, user_id) DO UPDATE SET "
                    "embedding = :emb, model_version = :model, analyzed_at = NOW()"
                ), {
                    "cid": catalog_id,
                    "uid": user_id,
                    "emb": json.dumps(embedding),
                    "isrc": track.get("isrc", ""),
                    "model": "lyrics-minilm-v2",
                })
                embedded += 1

    if embedded > 0:
        logger.info("Lyrics embeddings: %d tracks embedded", embedded)


# ── MusicBrainz Credits Enrichment ─────────────────────────────────────────


async def _enrich_musicbrainz_credits(
    engine,
    tracks: list[dict[str, Any]],
) -> None:
    """Fetch producer/songwriter credits from MusicBrainz for tracks with ISRCs."""
    from musicmind.engine.enrichment.musicbrainz_credits import fetch_recording_credits

    enriched = 0
    for track in tracks:
        isrc = track.get("isrc", "")
        if not isrc:
            continue
        try:
            credits = await fetch_recording_credits(isrc, engine=engine)
            if credits:
                enriched += 1
        except Exception:
            pass  # MusicBrainz is best-effort, don't block on failures

    if enriched > 0:
        logger.info("MusicBrainz credits: %d tracks enriched with producer data", enriched)


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
