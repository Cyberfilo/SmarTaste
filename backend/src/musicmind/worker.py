"""Global enrichment worker — builds artist cobwebs and enriches globally.

Separate from per-user indexing (see indexer.py). The worker:
1. For each user, builds an artist cobweb (related artists via feats)
2. Enriches each cobweb artist's top 50 songs GLOBALLY (no user_id)
3. Stores songs in global_song_cache, features in audio_features_global
4. Promotes featured artists who appear alongside library artists
5. Caps discovered artists at library_artists * 0.2 per user

Enrichment uses Essentia (local CPU) + Modal GPU for embeddings.

Results are available to ALL users for recommendation scoring.
The worker does NOT touch per-user tables (song_metadata_cache, audio_features_cache).

Usage:
    python -m musicmind.worker

Environment variables:
    DATABASE_URL              — PostgreSQL connection string
    MUSICMIND_FERNET_KEY      — Fernet key for decrypting service tokens
    MUSICMIND_LOGS_DATABASE_URL — Optional logging database
    WORKER_POLL_INTERVAL      — Seconds between cycles (default 120)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

import httpx
import sqlalchemy as sa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("musicmind.worker")

POLL_INTERVAL = int(os.environ.get("WORKER_POLL_INTERVAL", "120"))
MAX_COBWEB_SONGS = 50  # top songs per cobweb artist

# Sentinel written to song_metadata_cache.isrc / global_song_cache.isrc
# when ISRC lookup definitively fails (Deezer + MusicBrainz both miss).
# Excluded from future backfill queries so we don't retry forever.
# Manual recovery: UPDATE ... SET isrc = NULL WHERE isrc = '__NO_ISRC__';
NO_ISRC_SENTINEL = "__NO_ISRC__"

# ── Enrichment Failure Tracking ─────────────────────────────────────────
# Tracks that fail enrichment repeatedly are skipped to avoid ~300
# IntegrityError log entries/day from the same 13 stuck tracks.
# Key: "catalog_id:user_id", Value: failure count.
# Resets every _FAIL_RESET_CYCLES cycles to allow retries after code fixes.
_failed_tracks: dict[str, int] = {}
_fail_cycle_counter: int = 0
_FAIL_MAX_RETRIES = 3
_FAIL_RESET_CYCLES = 50


# ── Worker Status Heartbeat ──────────────────────────────────────────────


async def _set_status(
    engine,
    phase: str,
    detail: str = "",
    *,
    current: int = 0,
    total: int = 0,
    cycle: int = 0,
) -> None:
    """Update the worker_status singleton row in the main database."""
    from datetime import UTC, datetime

    from musicmind.db.schema import worker_status

    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(worker_status).where(worker_status.c.id == 1).values(
                    phase=phase,
                    detail=detail,
                    progress_current=current,
                    progress_total=total,
                    cycle=cycle,
                    updated_at=datetime.now(UTC),
                )
            )
    except Exception:
        pass


# ── Main Loop ─────────────────────────────────────────────────────────────


async def main() -> None:
    """Build artist cobwebs per user, enrich globally."""
    from musicmind.config import Settings
    from musicmind.db.engine import create_engine

    settings = Settings()
    engine = create_engine(settings.database_url)

    # Optional: connect to logs DB
    log_writer = None
    if settings.logs_database_url:
        try:
            from musicmind.db.logs import (
                DatabaseLogHandler,
                LogWriter,
                create_logs_engine,
                init_logs_schema,
            )

            logs_engine = create_logs_engine(settings.logs_database_url)
            await init_logs_schema(logs_engine)
            log_writer = LogWriter(logs_engine)
            log_writer.start()

            # Forward all Python logs (WARNING+ global, INFO+ musicmind) to DB
            db_handler = DatabaseLogHandler(log_writer)
            db_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            logging.getLogger().addHandler(db_handler)
            logger.info("Logging database connected (all logs forwarded)")
        except Exception:
            logger.warning("Failed to connect logging database", exc_info=True)

    # Log enrichment capability at startup
    from musicmind.engine.audio.essentia_extractor import is_essentia_available, is_onnx_available
    logger.info(
        "Worker started: poll=%ds, max_songs=%d, essentia=%s, onnx=%s, modal=%s",
        POLL_INTERVAL, MAX_COBWEB_SONGS,
        is_essentia_available(), is_onnx_available(),
        bool(getattr(settings, "modal_endpoint_url", None)),
    )

    # Ensure worker_status row exists
    try:
        from musicmind.db.schema import worker_status
        async with engine.begin() as conn:
            row = await conn.execute(sa.select(worker_status.c.id).limit(1))
            if not row.first():
                await conn.execute(worker_status.insert().values(id=1))
    except Exception:
        logger.debug("worker_status init skipped", exc_info=True)

    # Ensure preview_audio_cache table exists (worker may deploy before
    # the backend runs alembic upgrade head)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "CREATE TABLE IF NOT EXISTS preview_audio_cache ("
                "  catalog_id TEXT PRIMARY KEY,"
                "  audio_data BYTEA NOT NULL,"
                "  content_type TEXT DEFAULT 'audio/mpeg',"
                "  source_url TEXT,"
                "  downloaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                "  enrichment_complete BOOLEAN NOT NULL DEFAULT false"
                ")"
            ))
        logger.info("preview_audio_cache table ensured")
    except Exception:
        logger.warning("Could not ensure preview_audio_cache table", exc_info=True)

    # ── Startup Phase 0: Cleanup + discography migration ──────────────
    await _set_status(engine, "cleanup", "Removing orphaned audio feature rows")
    try:
        from musicmind.api.admin.progress import cleanup_orphaned_features
        result = await cleanup_orphaned_features(engine)
        if result["total_deleted"] > 0:
            logger.info("Orphan cleanup: deleted %d rows", result["total_deleted"])
        if log_writer:
            log_writer.log_enrichment(
                user_id="system", catalog_id="cleanup",
                stage="orphan_cleanup",
                result=f"deleted_{result['total_deleted']}",
            )
    except Exception:
        logger.exception("Orphan cleanup failed")

    await _set_status(engine, "cleanup", "Purging preview audio cache")
    try:
        deleted_previews = await _cleanup_preview_cache(engine)
        if deleted_previews > 0:
            logger.info("Preview cache cleanup: deleted %d entries", deleted_previews)
    except Exception:
        logger.debug("Preview cache cleanup skipped", exc_info=True)

    await _set_status(engine, "cleanup", "Unlinking excess discovered songs")
    try:
        deleted_total = await _unlink_excess_discoveries(engine)
        if deleted_total > 0:
            logger.info("Unlinked %d excess discovered songs", deleted_total)
    except Exception:
        logger.exception("Excess discovery cleanup failed")

    # Clean discography rows from per-user tables: user-linked should only
    # be songs actually in the user's library. Discography songs (from
    # indexer steps 2-5) belong in global_song_cache only.
    await _set_status(engine, "cleanup", "Migrating discography to global")
    try:
        migrated = await _migrate_discography_to_global(engine)
        if migrated > 0:
            logger.info("Discography migration: moved %d songs to global", migrated)
    except Exception:
        logger.exception("Discography migration failed")

    # ── Startup Phase 1: USER-LINKED drain ──────────────────────────
    # Fill every gap for songs in the user's library before spending a
    # single cycle on global/discovered data. Strict order inside the
    # drain: ISRC → preview URL/audio → Essentia → EffNet → GPU. Each
    # step depends on the one before, so running them in the right
    # sequence avoids wasted calls (e.g., don't ask the GPU to process
    # a track that hasn't been Essentia'd yet).
    #
    # The old design kicked all five off as independent startup phases
    # AND fired a duplicate chain from the backend's lifespan — both
    # services hammered the same tables in parallel, which Postgres
    # serialized but the redundant work wasted API quota and GPU
    # budget. V 6.370 makes the worker the single executor; the backend
    # only logs the plan.
    logger.info("── Startup: USER-LINKED drain ──")
    try:
        await _drain_user_linked(engine, settings)
    except Exception:
        logger.exception("User-linked drain crashed")

    # ── Startup Phase 2: DISCOVERED drain ───────────────────────────
    # Global tracks (cobweb + discography-fetched) come strictly AFTER
    # user-linked. Uses the V 6.367 URL-fallback path so GPU rows whose
    # preview_audio_cache expired can still MERT-backfill via the
    # preview URL stored on global_song_cache.
    logger.info("── Startup: DISCOVERED drain ──")
    try:
        await _drain_discovered(engine, settings)
    except Exception:
        logger.exception("Discovered drain crashed")

    logger.info("Startup drains complete — entering main loop")

    # ── Main loop: ISRC → audio → essentia → GPU → cobweb ──
    cycle = 0
    while True:
        cycle += 1
        start = time.monotonic()

        # Reset failure cache periodically to allow retries after code fixes
        global _fail_cycle_counter
        _fail_cycle_counter += 1
        if _fail_cycle_counter >= _FAIL_RESET_CYCLES:
            if _failed_tracks:
                logger.info(
                    "Resetting enrichment failure cache (%d entries)"
                    " after %d cycles",
                    len(_failed_tracks), _FAIL_RESET_CYCLES,
                )
            _failed_tracks.clear()
            _fail_cycle_counter = 0

        # ── Phase 1: ISRC backfill ─────────────────────────────────
        await _set_status(
            engine, "isrc_backfill", "Looking up missing ISRCs", cycle=cycle,
        )
        try:
            # Batch 500 (was default 100). With 274 library rows still
            # NULL + new rows from library sync, a 100/cycle rate took
            # several hours to drain. 500 catches up in a cycle or two.
            isrc_found = await _backfill_isrcs(engine, batch_limit=500)
            if isrc_found > 0:
                logger.info("Cycle %d: found %d ISRCs", cycle, isrc_found)
        except Exception:
            logger.exception("Cycle %d ISRC backfill failed", cycle)

        # ── Phase 2: Download + cache preview audio ────────────────
        await _set_status(
            engine, "audio_download", "Downloading missing previews",
            cycle=cycle,
        )
        try:
            downloaded = await _download_all_previews(engine, limit=100)
            if downloaded > 0:
                logger.info(
                    "Cycle %d: downloaded %d previews", cycle, downloaded,
                )
        except Exception:
            logger.exception("Cycle %d audio download failed", cycle)

        # ── Phase 3: Essentia/ONNX enrichment (CPU) ────────────────
        await _set_status(
            engine, "library_gaps",
            "Enriching missing user library songs", cycle=cycle,
        )
        try:
            lib_enriched = await _fill_library_gaps(engine, settings)
            if lib_enriched > 0:
                logger.info(
                    "Cycle %d: enriched %d user library gaps",
                    cycle, lib_enriched,
                )
        except Exception:
            logger.exception("Cycle %d library gap-fill failed", cycle)

        # ── Phase 4: EffNet embedding backfill ──────────────────────
        await _set_status(
            engine, "embedding_backfill",
            "EffNet embedding backfill", cycle=cycle,
        )
        try:
            backfilled = await _backfill_embeddings(engine, settings)
            if backfilled > 0:
                logger.info(
                    "Cycle %d: backfilled %d EffNet embeddings",
                    cycle, backfilled,
                )
        except Exception:
            logger.exception("Cycle %d EffNet backfill failed", cycle)

        # ── Phase 5: GPU backfill (CLAP + MERT via Modal) ──────────
        await _set_status(
            engine, "gpu_backfill",
            "GPU enrichment (CLAP + MERT)", cycle=cycle,
        )
        try:
            gpu_stored = await _backfill_gpu_embeddings(engine, settings)
            if gpu_stored > 0:
                logger.info(
                    "Cycle %d: GPU backfill stored %d CLAP/MERT",
                    cycle, gpu_stored,
                )
        except Exception:
            logger.exception("Cycle %d GPU backfill failed", cycle)

        # ── Phase 5b: Global GPU backfill (CLAP/MERT for discography) ──
        # V 6.379: bring the V 6.374 download-first + mert-only-split
        # backfiller into the main-loop so new global gaps drain every
        # cycle instead of only at worker startup.
        try:
            gpu_global = await _backfill_gpu_embeddings_global(engine, settings)
            if gpu_global > 0:
                logger.info(
                    "Cycle %d: global GPU backfill stored %d CLAP/MERT",
                    cycle, gpu_global,
                )
        except Exception:
            logger.exception("Cycle %d global GPU backfill failed", cycle)

        # ── Check if user-linked work is done ──────────────────────
        user_work_remaining = await _count_user_linked_gaps(engine)

        if user_work_remaining > 0:
            # User-linked work remains — skip cobweb, short sleep, loop back
            logger.info(
                "Cycle %d: %d user-linked gaps remain, skipping cobweb",
                cycle, user_work_remaining,
            )
            await _set_status(
                engine, "user_priority",
                f"{user_work_remaining} user songs still need processing",
                cycle=cycle,
            )
            await asyncio.sleep(10)  # Short pause, then re-check
            continue

        # ── Phase 2: Build cobwebs + enrich globally ────────────────
        # Only runs when ALL user-linked songs are fully enriched
        await _set_status(engine, "cobweb", "Building artist cobwebs", cycle=cycle)

        try:
            stats = await _run_cobweb_cycle(engine, settings, log_writer=log_writer)
            duration = round(time.monotonic() - start, 1)
            logger.info(
                "Cycle %d complete in %.1fs: %d cobweb artists, %d songs cached, "
                "%d enriched globally",
                cycle, duration,
                stats["cobweb_artists"],
                stats["songs_cached"],
                stats["songs_enriched"],
            )
            if log_writer:
                log_writer.log_enrichment(
                    user_id="system", catalog_id=f"cycle_{cycle}",
                    stage="worker_cycle",
                    result=(
                        f"enriched_{stats['songs_enriched']}"
                        if stats["songs_enriched"] > 0
                        else "idle"
                    ),
                    duration_ms=int(duration * 1000),
                )
        except Exception:
            logger.exception("Cycle %d failed", cycle)

        # ── Phase 3: Library sync (every 2h) ──────────────────────────
        # Re-fetch user library from Apple/Spotify and enrich new songs.
        try:
            await _sync_user_libraries(engine, settings)
        except Exception:
            logger.exception("Cycle %d library sync failed", cycle)

        # Log summary of permanently-skipped tracks once per cycle
        perm_failed = sum(
            1 for v in _failed_tracks.values()
            if v >= _FAIL_MAX_RETRIES
        )
        if perm_failed > 0:
            cycles_until_reset = (
                _FAIL_RESET_CYCLES - (_fail_cycle_counter % _FAIL_RESET_CYCLES)
            )
            logger.info(
                "Cycle %d: %d tracks permanently skipped"
                " (will retry in %d cycles)",
                cycle, perm_failed, cycles_until_reset,
            )

        await _set_status(engine, "idle", f"Sleeping {POLL_INTERVAL}s", cycle=cycle)
        await asyncio.sleep(POLL_INTERVAL)


# ── Discography Migration (per-user → global) ─────────────────────────────


async def _migrate_discography_to_global(engine) -> int:
    """Move discography songs from per-user tables to global-only.

    User-linked tables (song_metadata_cache, audio_features_cache,
    audio_embeddings) should only contain songs actually in the user's
    library. Discography tracks from indexer steps 2-5 were historically
    written per-user; this promotes their enrichment to global tables
    and deletes the per-user rows.

    Safe to call repeatedly (idempotent via ON CONFLICT DO NOTHING).
    """
    migrated = 0
    try:
        async with engine.begin() as conn:
            # Step 1: promote enrichment from per-user to global
            # audio_features_cache → audio_features_global (by ISRC)
            # NOTE: both tables use `loudness` (not `loudness_lufs`) and
            # `audio_features_global` has only `analyzed_at` (with NOW()
            # default) — no `enriched_at` column. Omitting the timestamp
            # lets the default fire. Earlier draft hardcoded the wrong
            # names and blew up the first migration run with
            # UndefinedColumnError.
            promoted = await conn.execute(sa.text("""
                INSERT INTO audio_features_global
                  (isrc, tempo, energy, danceability, brightness,
                   beat_strength, key, scale, loudness, acousticness,
                   valence_proxy, feature_source)
                SELECT s.isrc, af.tempo, af.energy, af.danceability, af.brightness,
                       af.beat_strength, af.key, af.scale, af.loudness,
                       af.acousticness, af.valence_proxy, af.feature_source
                FROM song_metadata_cache s
                JOIN audio_features_cache af
                  ON af.catalog_id = s.catalog_id AND af.user_id = s.user_id
                WHERE s.library_id IS NULL
                  AND s.date_added_to_library IS NULL
                  AND s.isrc IS NOT NULL AND s.isrc != '' AND s.isrc != '__NO_ISRC__'
                  AND af.energy IS NOT NULL
                ON CONFLICT (isrc) DO NOTHING
            """))
            if promoted.rowcount > 0:
                logger.info("Promoted %d feature rows to global", promoted.rowcount)

            # audio_embeddings → audio_embeddings_global (by ISRC)
            await conn.execute(sa.text("""
                INSERT INTO audio_embeddings_global
                  (isrc, embedding, clap_embedding, mert_embedding)
                SELECT s.isrc, ae.embedding, ae.clap_embedding, ae.mert_embedding
                FROM song_metadata_cache s
                JOIN audio_embeddings ae
                  ON ae.catalog_id = s.catalog_id AND ae.user_id = s.user_id
                WHERE s.library_id IS NULL
                  AND s.date_added_to_library IS NULL
                  AND s.isrc IS NOT NULL AND s.isrc != '' AND s.isrc != '__NO_ISRC__'
                ON CONFLICT (isrc) DO NOTHING
            """))

            # song_metadata_cache → global_song_cache (metadata + artwork)
            await conn.execute(sa.text("""
                INSERT INTO global_song_cache
                  (catalog_id, name, artist_name, album_name, genre_names,
                   isrc, duration_ms, release_date, preview_url,
                   artwork_url, service_source)
                SELECT catalog_id, name, artist_name, album_name, genre_names,
                       isrc, duration_ms, release_date, preview_url,
                       artwork_url_template, service_source
                FROM song_metadata_cache
                WHERE library_id IS NULL
                  AND date_added_to_library IS NULL
                ON CONFLICT (catalog_id) DO UPDATE SET
                  artwork_url = COALESCE(NULLIF(global_song_cache.artwork_url, ''),
                                         EXCLUDED.artwork_url)
            """))

            # Step 2: delete per-user discography rows
            del_emb = await conn.execute(sa.text("""
                DELETE FROM audio_embeddings ae
                USING song_metadata_cache s
                WHERE ae.catalog_id = s.catalog_id AND ae.user_id = s.user_id
                  AND s.library_id IS NULL AND s.date_added_to_library IS NULL
            """))

            del_af = await conn.execute(sa.text("""
                DELETE FROM audio_features_cache af
                USING song_metadata_cache s
                WHERE af.catalog_id = s.catalog_id AND af.user_id = s.user_id
                  AND s.library_id IS NULL AND s.date_added_to_library IS NULL
            """))

            del_smc = await conn.execute(sa.text("""
                DELETE FROM song_metadata_cache
                WHERE library_id IS NULL AND date_added_to_library IS NULL
            """))

            migrated = del_smc.rowcount
            if migrated > 0:
                logger.info(
                    "Discography cleanup: deleted %d songs, %d features, %d embeddings from per-user",
                    del_smc.rowcount, del_af.rowcount, del_emb.rowcount,
                )
    except Exception:
        logger.exception("Discography migration failed")

    return migrated


# ── Preview Audio Cache Cleanup ──────────────────────────────────────────


async def _cleanup_preview_cache(engine) -> int:
    """Delete fully-enriched and stale (> 7 days) preview audio entries."""
    from datetime import UTC, datetime, timedelta

    from musicmind.db.schema import preview_audio_cache

    cutoff = datetime.now(UTC) - timedelta(days=7)
    total = 0
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.delete(preview_audio_cache).where(
                    sa.or_(
                        preview_audio_cache.c.enrichment_complete == sa.true(),
                        preview_audio_cache.c.downloaded_at < cutoff,
                    )
                )
            )
            total = result.rowcount
    except Exception:
        logger.warning("Preview cache cleanup query failed", exc_info=True)
    return total


# ── Preview Audio Download ────────────────────────────────────────────────


async def _download_all_previews(engine, *, limit: int = 0) -> int:
    """Download and cache preview audio for tracks missing cached bytes.

    For tracks with expired Deezer CDN URLs (403), refreshes via Deezer
    ISRC/search lookup. Cached bytes are used by Essentia (CPU) and
    Modal GPU (bytes-based), eliminating URL expiry failures entirely.
    """
    import asyncio

    from musicmind.db.schema import (
        preview_audio_cache,
        song_metadata_cache,
    )
    from musicmind.engine.enrichment.orchestrator import (
        _cache_preview_audio,
        _download_preview,
    )

    # Find tracks that have no cached audio bytes
    async with engine.begin() as conn:
        cached_ids = sa.select(
            preview_audio_cache.c.catalog_id,
        ).correlate(None)
        q = sa.select(
            song_metadata_cache.c.catalog_id,
            song_metadata_cache.c.user_id,
            song_metadata_cache.c.name,
            song_metadata_cache.c.artist_name,
            song_metadata_cache.c.isrc,
            song_metadata_cache.c.preview_url,
        ).where(
            sa.and_(
                song_metadata_cache.c.catalog_id.notin_(cached_ids),
                song_metadata_cache.c.preview_url.isnot(None),
                song_metadata_cache.c.preview_url != "",
            )
        )
        if limit > 0:
            q = q.limit(limit)
        result = await conn.execute(q)
        rows = result.fetchall()

    if not rows:
        return 0

    logger.info("Downloading previews for %d tracks", len(rows))

    downloaded = 0
    sem = asyncio.Semaphore(15)

    async def _dl_one(row) -> bool:
        async with sem:
            audio = await _download_preview(row.preview_url)

            # Expired Deezer URL? Refresh via ISRC/search
            if audio is None and "dzcdn.net" in (row.preview_url or ""):
                try:
                    from musicmind.engine.enrichment.deezer import (
                        search_preview_url,
                    )
                    fresh = await search_preview_url(
                        name=row.name or "",
                        artist_name=row.artist_name or "",
                        isrc=row.isrc or None,
                    )
                    if fresh:
                        audio = await _download_preview(fresh)
                        if audio:
                            # Update the cached URL in DB
                            async with engine.begin() as conn:
                                await conn.execute(
                                    sa.update(song_metadata_cache)
                                    .where(sa.and_(
                                        song_metadata_cache.c.catalog_id
                                        == row.catalog_id,
                                        song_metadata_cache.c.user_id
                                        == row.user_id,
                                    ))
                                    .values(preview_url=fresh)
                                )
                except Exception:
                    pass

            if audio:
                await _cache_preview_audio(
                    engine, row.catalog_id, audio,
                    row.preview_url or "",
                )
                return True
            return False

    for i in range(0, len(rows), 50):
        batch = rows[i:i + 50]
        results = await asyncio.gather(
            *[_dl_one(r) for r in batch],
            return_exceptions=True,
        )
        ok = sum(1 for r in results if r is True)
        downloaded += ok
        if len(rows) > 50:
            logger.info(
                "Audio download %d/%d: %d cached",
                min(i + 50, len(rows)), len(rows), ok,
            )

    return downloaded


# ── Cobweb Building ──────────────────────────────────────────────────────


async def _run_cobweb_cycle(
    engine,
    settings,
    *,
    log_writer=None,
) -> dict[str, int]:
    """One cobweb expansion cycle across all users."""
    from musicmind.db.schema import user_indexing_status, users

    stats = {"cobweb_artists": 0, "songs_cached": 0, "songs_enriched": 0}

    async with engine.begin() as conn:
        result = await conn.execute(sa.select(users.c.id))
        user_ids = [row.id for row in result]

    # Check which users have active backend indexing (step < 7 = not complete)
    actively_indexing: set[str] = set()
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(user_indexing_status.c.user_id).where(
                    sa.and_(
                        user_indexing_status.c.step < 7,
                        user_indexing_status.c.completed_at.is_(None),
                    )
                )
            )
            actively_indexing = {row.user_id for row in result}
    except Exception:
        logger.debug("Could not check indexing status", exc_info=True)

    for user_id in user_ids:
        if user_id in actively_indexing:
            logger.info(
                "User %s: backend indexing active, worker yielding",
                user_id[:8],
            )
            continue
        try:
            user_stats = await _build_user_cobweb(engine, settings, user_id=user_id)
            for k in stats:
                stats[k] += user_stats.get(k, 0)
        except Exception:
            logger.warning("Cobweb failed for user %s", user_id[:8], exc_info=True)

        # Unified discovery: the cobweb is the single source of candidates.
        # Editorial / chart / genre-adjacent / similar-artist strategies are
        # no longer called here — they pulled unrelated tracks (Dua Lipa,
        # K-pop, US charts) because they never consulted the user's
        # genre/artist profile before writing. Everything a user sees as a
        # recommendation now traces back to an artist in their cobweb, which
        # itself traces back to a library collaboration.
        try:
            cand_stats = await _populate_candidates_from_cobweb(
                engine, user_id=user_id,
            )
            stats["candidates_written"] = (
                stats.get("candidates_written", 0)
                + cand_stats.get("candidates_written", 0)
            )
        except Exception:
            logger.warning(
                "Cobweb candidate population failed for user %s",
                user_id[:8], exc_info=True,
            )

    # Enrich unenriched global songs
    enriched = await _enrich_global_songs(engine, settings)
    stats["songs_enriched"] = enriched

    return stats


async def _populate_candidates_from_cobweb(
    engine,
    *,
    user_id: str,
) -> dict[str, int]:
    """Write recommendation_candidates from enriched cobweb artists' tracks.

    Unified discovery: every candidate a user sees traces back to an artist
    that feature-parsed out of one of their library songs (and was then
    enriched into global_song_cache). No editorial-playlist scraping, no
    chart-filter fallthrough, no parent-genre-overlap pollution.

    The cobweb's priority (double-precision float, typically ~2.0 for
    feat-sourced artists) is normalized to a [0.1, 1.0] discovery_weight.
    Matching is exact on artist_name; compound / featuring strings stay
    assigned to the row where they appear, which is fine because the
    cobweb row itself carries the canonical split artist.
    """
    from musicmind.db.schema import artist_cobweb, recommendation_candidates  # noqa: F401

    stats = {"candidates_written": 0}

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.text(
                "INSERT INTO recommendation_candidates"
                " (user_id, catalog_id, strategy_source, discovery_weight,"
                "  service_source, fetched_at)"
                " SELECT c.user_id, g.catalog_id, 'cobweb',"
                "        LEAST(GREATEST(c.priority / 3.0, 0.1), 1.0),"
                "        COALESCE(NULLIF(g.service_source, ''), ''),"
                "        NOW()"
                " FROM artist_cobweb c"
                " JOIN global_song_cache g"
                "      ON g.artist_name = c.artist_name"
                " WHERE c.user_id = :uid AND c.enriched = true"
                # Exclude anything already in the user's library: match by
                # catalog_id (same-service) OR by non-empty ISRC
                # (cross-service — e.g. Spotify library entry slipping in as
                # an Apple-sourced candidate with a different catalog_id).
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM song_metadata_cache smc"
                "     WHERE smc.user_id = c.user_id"
                "       AND (smc.library_id IS NOT NULL"
                "            OR smc.date_added_to_library IS NOT NULL)"
                "       AND (smc.catalog_id = g.catalog_id"
                "            OR (smc.isrc IS NOT NULL AND smc.isrc <> ''"
                "                AND smc.isrc = g.isrc))"
                "   )"
                " ON CONFLICT (user_id, catalog_id, strategy_source) DO UPDATE"
                "    SET discovery_weight = GREATEST("
                "           recommendation_candidates.discovery_weight,"
                "           EXCLUDED.discovery_weight"
                "        ),"
                "        fetched_at = NOW()"
            ),
            {"uid": user_id},
        )
        stats["candidates_written"] = int(result.rowcount or 0)

    if stats["candidates_written"]:
        logger.info(
            "User %s: wrote %d cobweb-sourced candidates",
            user_id[:8], stats["candidates_written"],
        )
    return stats


# ── Library Sync (2h cadence) ──────────────────────────────────────────

# Track last library sync per user (in-memory; resets on redeploy).
_last_library_sync: dict[str, float] = {}
LIBRARY_SYNC_INTERVAL_SECONDS = 2 * 3600  # 2 hours


async def _sync_user_libraries(engine, settings) -> None:
    """Re-fetch each user's library from their connected service and enrich new songs.

    Cadence: every 2h per user (tracked in-memory; resets on redeploy).
    Uses TasteService.get_profile(force_refresh=True) which:
    1. Fetches library from Apple/Spotify API
    2. Caches new songs in song_metadata_cache
    3. Rebuilds taste_profile_snapshots with new data
    """
    from musicmind.db.schema import users
    from musicmind.security.encryption import EncryptionService

    now = time.monotonic()

    async with engine.begin() as conn:
        user_rows = (await conn.execute(sa.select(users.c.id))).fetchall()

    encryption = EncryptionService(settings.fernet_key)

    for user_row in user_rows:
        uid = user_row.id
        last = _last_library_sync.get(uid, 0.0)
        if now - last < LIBRARY_SYNC_INTERVAL_SECONDS:
            continue

        try:
            from musicmind.api.taste.service import TasteService

            await _set_status(engine, "library_sync", f"Syncing library for {uid[:8]}")
            await TasteService().get_profile(
                engine, encryption, settings,
                user_id=uid, force_refresh=True,
            )
            _last_library_sync[uid] = now
            logger.info("Library sync complete for user %s", uid[:8])
        except Exception:
            logger.debug("Library sync failed for user %s", uid[:8], exc_info=True)


async def _run_discovery_for_user(
    engine,
    settings,
    *,
    user_id: str,
) -> dict[str, int]:
    """Refresh per-user discovery candidates (similar/genre/editorial/charts).

    Replaces the live API calls that the recommendations endpoint used to make.
    Persists results in `recommendation_candidates` keyed by
    (user_id, catalog_id, strategy_source) so the same track surfaced by
    multiple strategies still drives the cross-strategy bonus during scoring.

    Refresh policy: skip if the user already has candidates fetched in the
    last 6 hours.
    """
    from datetime import UTC, datetime, timedelta

    from musicmind.api.recommendations.fetch import (
        discover_chart_filter,
        discover_editorial,
        discover_genre_adjacent,
        discover_similar_artists,
    )
    from musicmind.api.recommendations.service import RecommendationService
    from musicmind.db.schema import (
        recommendation_candidates,
        taste_profile_snapshots,
    )
    from musicmind.security.encryption import EncryptionService

    stats = {"discovery_candidates": 0}

    # ── Refresh cadence guard (6 hours) ─────────────────────────────────
    async with engine.begin() as conn:
        last = (await conn.execute(
            sa.select(sa.func.max(recommendation_candidates.c.fetched_at))
            .where(recommendation_candidates.c.user_id == user_id)
        )).scalar()
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if datetime.now(UTC) - last < timedelta(hours=6):
            return stats

    # ── Load latest snapshot for seeds + genres ─────────────────────────
    async with engine.begin() as conn:
        snap_row = (await conn.execute(
            sa.select(
                taste_profile_snapshots.c.top_artists,
                taste_profile_snapshots.c.genre_vector,
            )
            .where(taste_profile_snapshots.c.user_id == user_id)
            .order_by(taste_profile_snapshots.c.computed_at.desc())
            .limit(1)
        )).first()
    if snap_row is None:
        logger.debug("Discovery skip for %s: no snapshot yet", user_id[:8])
        return stats

    top_artists_raw = snap_row.top_artists
    if isinstance(top_artists_raw, str):
        try:
            top_artists_raw = json.loads(top_artists_raw)
        except (ValueError, TypeError):
            top_artists_raw = []

    genre_vector_raw = snap_row.genre_vector
    if isinstance(genre_vector_raw, str):
        try:
            genre_vector_raw = json.loads(genre_vector_raw)
        except (ValueError, TypeError):
            genre_vector_raw = {}

    # Unified discoverer: favour breadth over narrow strategy gates.
    # Use up to 15 seed artists and 10 top genres so the candidate pool
    # reflects the full shape of the user's profile, not just the peaks.
    seed_scored: list[tuple[str, float]] = [
        (a["name"], float(a.get("score", 0.0)))
        for a in (top_artists_raw or [])[:15]
        if isinstance(a, dict) and a.get("name")
    ]
    if not seed_scored:
        return stats
    top_genre_names = [
        g for g, _ in sorted(
            (genre_vector_raw or {}).items(),
            key=lambda x: -x[1],
        )[:10]
    ]

    # ── Resolve credentials (reuses existing helper) ────────────────────
    encryption = EncryptionService(settings.fernet_key)
    rec_service = RecommendationService()
    try:
        creds_list = await rec_service._resolve_all_credentials(
            engine, encryption, settings, user_id=user_id,
        )
    except Exception:
        logger.debug("No creds for discovery (user %s)", user_id[:8])
        return stats

    # ── Run all 4 strategies × all services in parallel ────────────────
    all_candidates: list[dict[str, Any]] = []

    async def _run_for_service(
        service: str,
        access_token: str,
        dev_token: str | None,
        storefront: str,
    ) -> None:
        results = await asyncio.gather(
            discover_similar_artists(
                service, access_token, seed_scored,
                developer_token=dev_token, storefront=storefront,
                total_budget=200,
            ),
            discover_genre_adjacent(
                service, access_token, top_genre_names,
                developer_token=dev_token, storefront=storefront,
                limit=50,
            ),
            discover_editorial(
                service, access_token, top_genre_names,
                developer_token=dev_token, storefront=storefront,
                limit=50,
            ),
            discover_chart_filter(
                service, access_token, top_genre_names,
                developer_token=dev_token, storefront=storefront,
                limit=50,
            ),
            return_exceptions=True,
        )
        names = ["similar_artist", "genre_adjacent", "editorial", "chart"]
        for strat, r in zip(names, results):
            if isinstance(r, list):
                for t in r:
                    t["_strategy_source"] = strat
                all_candidates.extend(r)
            elif isinstance(r, Exception):
                logger.warning(
                    "Discovery strategy %s failed for user %s on %s: %s",
                    strat, user_id[:8], service, r,
                )

    await asyncio.gather(
        *[_run_for_service(*c) for c in creds_list],
        return_exceptions=True,
    )

    if not all_candidates:
        return stats

    # ── Persist: global_song_cache (metadata) + recommendation_candidates ──
    # Use raw SQL with ON CONFLICT for atomic upserts.
    async with engine.begin() as conn:
        for c in all_candidates:
            cid = c.get("catalog_id") or ""
            if not cid:
                continue
            try:
                await conn.execute(
                    sa.text(
                        "INSERT INTO global_song_cache"
                        " (catalog_id, name, artist_name, album_name,"
                        "  genre_names, isrc, duration_ms, release_date,"
                        "  preview_url, artwork_url, service_source)"
                        " VALUES (:cid, :name, :artist, :album, :genres,"
                        "  :isrc, :duration, :release, :preview, :art, :svc)"
                        " ON CONFLICT (catalog_id) DO UPDATE SET"
                        "  artwork_url = COALESCE("
                        "    NULLIF(global_song_cache.artwork_url, ''),"
                        "    EXCLUDED.artwork_url"
                        "  )"
                    ),
                    {
                        "cid": cid,
                        "name": c.get("name", ""),
                        "artist": c.get("artist_name", ""),
                        "album": c.get("album_name", ""),
                        "genres": json.dumps(c.get("genre_names", [])),
                        "isrc": c.get("isrc"),
                        "duration": c.get("duration_ms"),
                        "release": c.get("release_date"),
                        "preview": c.get("preview_url", ""),
                        "art": c.get("artwork_url_template", "") or c.get("artwork_url", ""),
                        "svc": c.get("service_source", ""),
                    },
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO recommendation_candidates"
                        " (user_id, catalog_id, strategy_source,"
                        "  discovery_weight, service_source, fetched_at)"
                        " VALUES (:uid, :cid, :strat, :dw, :svc, NOW())"
                        " ON CONFLICT (user_id, catalog_id, strategy_source)"
                        " DO UPDATE SET"
                        "  discovery_weight = GREATEST("
                        "      recommendation_candidates.discovery_weight,"
                        "      EXCLUDED.discovery_weight"
                        "  ),"
                        "  fetched_at = NOW()"
                    ),
                    {
                        "uid": user_id, "cid": cid,
                        "strat": c["_strategy_source"],
                        "dw": float(c.get("_discovery_weight", 0.0)),
                        "svc": c.get("service_source", ""),
                    },
                )
                stats["discovery_candidates"] += 1
            except Exception:
                logger.debug(
                    "Failed to persist candidate %s for %s",
                    cid, user_id[:8],
                )

    if stats["discovery_candidates"] > 0:
        logger.info(
            "Discovery refreshed for %s: %d candidates upserted",
            user_id[:8], stats["discovery_candidates"],
        )

    return stats


def _primary_affinity_lookup(raw_name: str, affinity_map: dict[str, float]) -> float:
    """Return the primary artist's affinity score for a raw artist string."""
    from musicmind.engine.profile import parse_artists

    parsed = parse_artists(raw_name)
    if not parsed:
        return 0.1
    return affinity_map.get(parsed[0][0].lower(), 0.1)


async def _build_user_cobweb(
    engine,
    settings,
    *,
    user_id: str,
) -> dict[str, int]:
    """Build/expand the artist cobweb for one user.

    Cobweb sources:
    1. Featured artists from library songs (direct collaboration)

    Ranking uses sum + log1p + primary-affinity weighting (see engine/cobweb.py).
    Cap per cycle = library_artists * 0.2; absolute cap = feat density (unique names).
    """
    from musicmind.db.schema import (
        artist_cobweb,
        song_metadata_cache,
        taste_profile_snapshots,
    )
    from musicmind.engine.cobweb import rank_cobweb_candidates

    stats = {"cobweb_artists": 0, "songs_cached": 0}

    # Get raw library artist strings (with feat info preserved)
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
        library_artist_names = [row[0] for row in result if row[0]]

    if not library_artist_names:
        return stats

    # Build affinity_map from latest taste profile snapshot
    affinity_map: dict[str, float] = {}
    async with engine.begin() as conn:
        snap = await conn.execute(
            sa.select(taste_profile_snapshots.c.top_artists)
            .where(taste_profile_snapshots.c.user_id == user_id)
            .order_by(taste_profile_snapshots.c.computed_at.desc())
            .limit(1)
        )
        row = snap.first()
        if row and row[0]:
            raw_top = row[0]
            if isinstance(raw_top, str):
                try:
                    import json as _json
                    raw_top = _json.loads(raw_top)
                except Exception:
                    raw_top = []
            for entry in (raw_top or []):
                name = entry.get("name", "") if isinstance(entry, dict) else ""
                score = entry.get("score", 0.1) if isinstance(entry, dict) else 0.1
                if name:
                    affinity_map[name.lower()] = float(score)

    # Build library_rows pairing raw artist string with primary affinity
    library_rows = [
        {
            "artist_name": raw,
            "primary_affinity": _primary_affinity_lookup(raw, affinity_map),
        }
        for raw in library_artist_names
    ]

    library_set = {a.lower() for a in library_artist_names}
    max_per_cycle = max(2, int(len(library_artist_names) * 0.2))

    # Get existing cobweb artists to avoid re-adding
    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(artist_cobweb.c.artist_name).where(
                artist_cobweb.c.user_id == user_id
            )
        )
        existing_set = {row.artist_name.lower() for row in existing}

    # Rank all candidates using shared logic (density cap, no hard library-size cap)
    all_ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names=library_set,
        existing_cobweb_names=existing_set,
    )

    # Slice to per-cycle budget
    to_add = all_ranked[:max_per_cycle]

    if to_add:
        # Insert into cobweb (use rowcount to track actual inserts)
        for name, priority in to_add:
            source = "feat"
            try:
                async with engine.begin() as conn:
                    result = await conn.execute(
                        sa.text(
                            "INSERT INTO artist_cobweb"
                            " (user_id, artist_name, source, priority)"
                            " VALUES (:uid, :name, :src, :pri)"
                            " ON CONFLICT (user_id, artist_name) DO NOTHING"
                        ),
                        {"uid": user_id, "name": name, "src": source, "pri": priority},
                    )
                    if result.rowcount > 0:
                        stats["cobweb_artists"] += 1
            except Exception:
                logger.debug("Cobweb insert failed for '%s'", name)
    else:
        logger.debug("User %s: no new cobweb candidates to add", user_id[:8])

    # Fetch songs for unenriched cobweb artists
    async with engine.begin() as conn:
        unenriched = await conn.execute(
            sa.select(artist_cobweb.c.artist_name).where(
                sa.and_(
                    artist_cobweb.c.user_id == user_id,
                    artist_cobweb.c.enriched == sa.false(),
                )
            ).order_by(artist_cobweb.c.priority.desc()).limit(5)
        )
        unenriched_artists = [row.artist_name for row in unenriched]

    # Load user's embedding centroid once per cycle so the per-artist enrichment
    # prefilter doesn't repeat the same DB query up to 5 times.
    from musicmind.engine.cobweb import EFFNET_EMBEDDING_DIM
    user_centroid: list[float] | None = None
    async with engine.begin() as conn:
        snap = (await conn.execute(
            sa.select(taste_profile_snapshots.c.embedding_centroid)
            .where(taste_profile_snapshots.c.user_id == user_id)
            .order_by(taste_profile_snapshots.c.computed_at.desc())
            .limit(1)
        )).first()
    if snap and snap.embedding_centroid:
        raw = snap.embedding_centroid
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = None
        if isinstance(raw, list) and len(raw) == EFFNET_EMBEDDING_DIM:
            user_centroid = raw

    for artist_name in unenriched_artists:
        try:
            cached = await _fetch_artist_songs_globally(
                engine, settings, user_id=user_id,
                artist_name=artist_name, limit=MAX_COBWEB_SONGS,
                user_centroid=user_centroid,
            )
            stats["songs_cached"] += cached
            async with engine.begin() as conn:
                await conn.execute(
                    sa.update(artist_cobweb).where(
                        sa.and_(
                            artist_cobweb.c.user_id == user_id,
                            artist_cobweb.c.artist_name == artist_name,
                        )
                    ).values(enriched=True, songs_fetched=cached)
                )
        except Exception:
            logger.debug("Failed to fetch songs for cobweb artist '%s'", artist_name)

    return stats


async def _fetch_artist_songs_globally(
    engine,
    settings,
    *,
    user_id: str,
    artist_name: str,
    limit: int,
    user_centroid: list[float] | None = None,
) -> int:
    """Fetch an artist's top songs and store in global_song_cache.

    Pass `user_centroid` (the user's L2-normalized embedding centroid) to
    enable similarity-based pre-filtering of cached candidates. When omitted,
    no filter is applied (cold-start safe).
    """
    from musicmind.api.recommendations.fetch import (
        _fetch_artist_top_tracks,
        _search_artist_id,
    )
    from musicmind.api.services.service import (
        detect_apple_music_storefront,
        generate_apple_developer_token,
        get_user_connections,
    )
    from musicmind.db.schema import global_song_cache, service_connections
    from musicmind.security.encryption import EncryptionService

    # Get credentials from user's service connection
    connections = await get_user_connections(engine, user_id=user_id)
    if not connections:
        return 0

    conn_data = connections[0]
    service = conn_data["service"]

    async with engine.begin() as conn:
        row = (await conn.execute(
            sa.select(service_connections).where(
                sa.and_(
                    service_connections.c.user_id == user_id,
                    service_connections.c.service == service,
                )
            )
        )).first()
    if not row:
        return 0

    encryption = EncryptionService(settings.fernet_key)
    access_token = encryption.decrypt_or_none(row.access_token_encrypted)
    if not access_token:
        logger.warning("Failed to decrypt token for user %s", user_id[:8])
        return 0

    # Auto-refresh Spotify token if expired (tokens last ~1 hour)
    if service == "spotify" and row.token_expires_at is not None:
        from datetime import UTC, datetime, timedelta

        token_expires = row.token_expires_at
        if token_expires.tzinfo is None:
            token_expires = token_expires.replace(tzinfo=UTC)
        if token_expires < datetime.now(UTC) + timedelta(seconds=60):
            refresh_encrypted = row.refresh_token_encrypted
            refresh_value = (
                encryption.decrypt_or_none(refresh_encrypted)
                if refresh_encrypted else None
            )
            if refresh_value:
                try:
                    from musicmind.api.services.service import (
                        refresh_spotify_token,
                        upsert_service_connection,
                    )
                    token_data = await refresh_spotify_token(
                        refresh_value, settings.spotify_client_id,
                    )
                    if token_data:
                        access_token = token_data["access_token"]
                        await upsert_service_connection(
                            engine, encryption,
                            user_id=user_id, service="spotify",
                            access_token=access_token,
                            refresh_token=token_data.get("refresh_token", refresh_value),
                            expires_in=token_data.get("expires_in"),
                            service_user_id=row.service_user_id,
                        )
                        logger.info("Refreshed Spotify token for worker user %s", user_id[:8])
                except Exception:
                    logger.warning("Spotify token refresh failed for worker user %s", user_id[:8])

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

    artist_id = await _search_artist_id(
        service, access_token, artist_name,
        developer_token=developer_token, storefront=storefront,
    )
    if not artist_id:
        return 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        tracks = await _fetch_artist_top_tracks(
            client, service, access_token, artist_id,
            developer_token=developer_token,
            storefront=storefront, limit=limit,
        )

    if not tracks:
        return 0

    # ── Embedding pre-filter ─────────────────────────────────────────────
    # Look up any existing global EffNet embeddings by ISRC so we can rank
    # candidates against the user's taste centroid before committing to cache
    # (and thus enrichment). Tracks without a known embedding pass through —
    # we need enrichment to learn their embedding. Cold-start users (no
    # centroid passed in) skip filtering entirely.
    from musicmind.db.schema import audio_embeddings_global
    from musicmind.engine.cobweb import (
        EFFNET_EMBEDDING_DIM,
        prefilter_by_centroid_similarity,
    )

    isrcs = [t.get("isrc") for t in tracks if t.get("isrc")]
    emb_by_isrc: dict[str, list[float]] = {}
    if isrcs and user_centroid is not None:
        async with engine.begin() as conn:
            rows = await conn.execute(
                sa.select(
                    audio_embeddings_global.c.isrc,
                    audio_embeddings_global.c.embedding,
                ).where(audio_embeddings_global.c.isrc.in_(isrcs))
            )
            for row in rows:
                emb = row.embedding
                if isinstance(emb, str):
                    try:
                        emb = json.loads(emb)
                    except (ValueError, TypeError):
                        emb = None
                if isinstance(emb, list) and len(emb) == EFFNET_EMBEDDING_DIM:
                    emb_by_isrc[row.isrc] = emb

    for t in tracks:
        isrc = t.get("isrc")
        if isrc and isrc in emb_by_isrc:
            t["effnet_embedding"] = emb_by_isrc[isrc]

    before_count = len(tracks)
    tracks = prefilter_by_centroid_similarity(
        tracks=tracks, centroid=user_centroid, keep_fraction=0.7,
    )
    if before_count != len(tracks):
        logger.info(
            "Cobweb prefilter for %s: %d → %d tracks (centroid=%s)",
            user_id[:8], before_count, len(tracks),
            "yes" if user_centroid else "no",
        )

    # Store in global_song_cache (skip existing)
    cached = 0
    async with engine.begin() as conn:
        for track in tracks:
            cid = track.get("catalog_id", "")
            if not cid:
                continue
            exists = await conn.execute(
                sa.select(global_song_cache.c.catalog_id).where(
                    global_song_cache.c.catalog_id == cid
                )
            )
            if exists.first():
                continue
            await conn.execute(
                global_song_cache.insert().values(
                    catalog_id=cid,
                    name=track.get("name", ""),
                    artist_name=track.get("artist_name", ""),
                    album_name=track.get("album_name", ""),
                    genre_names=json.dumps(track.get("genre_names", [])),
                    duration_ms=track.get("duration_ms"),
                    release_date=track.get("release_date"),
                    isrc=track.get("isrc"),
                    preview_url=track.get("preview_url", ""),
                    artwork_url=(
                        track.get("artwork_url_template", "")
                        or track.get("artwork_url", "")
                    ),
                    service_source=service,
                )
            )
            cached += 1

    return cached


# ── Excess Discovery Cleanup ────────────────────────────────────────────


async def _unlink_excess_discoveries(engine) -> int:
    """Delete user-linked discography songs from artists outside the top 20%.

    Previous indexer runs fetched discographies for ALL library artists.
    Now capped at top 3 + 20%. This cleanup removes songs from excess
    artists, keeping only library songs and top-artist discographies.
    Also cleans up their audio_features_cache rows.
    """
    from musicmind.db.schema import song_metadata_cache, users

    total_deleted = 0

    async with engine.begin() as conn:
        user_rows = (await conn.execute(sa.select(users.c.id))).fetchall()

    for user_row in user_rows:
        uid = user_row.id

        # Get ranked artists (same logic as indexer)
        try:
            from musicmind.indexer import _get_ranked_artists
            ranked = await _get_ranked_artists(engine, user_id=uid)
        except Exception:
            continue

        if not ranked:
            continue

        # Keep all artists above the indexer's affinity threshold (with min-3 fallback),
        # matching indexer.run_indexing so we don't delete songs the indexer just enriched.
        from musicmind.indexer import AFFINITY_INCLUDE_THRESHOLD
        kept_ranked = [(n, s) for n, s in ranked if s >= AFFINITY_INCLUDE_THRESHOLD]
        if len(kept_ranked) < 3:
            kept_ranked = ranked[:3]
        keep_artists = {n.lower() for n, _ in kept_ranked}

        # Also keep all library artist names (never delete library songs)
        async with engine.begin() as conn:
            lib_artists_q = await conn.execute(
                sa.select(sa.distinct(song_metadata_cache.c.artist_name)).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == uid,
                        sa.or_(
                            song_metadata_cache.c.library_id.isnot(None),
                            song_metadata_cache.c.date_added_to_library.isnot(None),
                        ),
                    )
                )
            )
            for row in lib_artists_q:
                if row[0]:
                    keep_artists.add(row[0].lower())

        # Find non-library songs from artists NOT in keep_artists
        async with engine.begin() as conn:
            excess_q = await conn.execute(
                sa.select(
                    song_metadata_cache.c.catalog_id,
                    song_metadata_cache.c.artist_name,
                ).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == uid,
                        song_metadata_cache.c.library_id.is_(None),
                        song_metadata_cache.c.date_added_to_library.is_(None),
                    )
                )
            )
            to_delete = [
                row.catalog_id for row in excess_q
                if row.artist_name and row.artist_name.lower() not in keep_artists
            ]

        if not to_delete:
            continue

        # Delete in batches
        for i in range(0, len(to_delete), 500):
            batch = to_delete[i:i + 500]
            async with engine.begin() as conn:
                # Delete audio features first (FK-safe)
                await conn.execute(
                    sa.text(
                        "DELETE FROM audio_features_cache"
                        " WHERE user_id = :uid"
                        "   AND catalog_id = ANY(:ids)"
                    ),
                    {"uid": uid, "ids": batch},
                )
                # Delete songs
                result = await conn.execute(
                    sa.text(
                        "DELETE FROM song_metadata_cache"
                        " WHERE user_id = :uid"
                        "   AND catalog_id = ANY(:ids)"
                    ),
                    {"uid": uid, "ids": batch},
                )
                total_deleted += result.rowcount

        logger.info(
            "User %s: unlinked %d excess discovered songs (%d artists kept)",
            uid[:8], len(to_delete), len(keep_artists),
        )

    return total_deleted


# ── User-Linked Gap Detection ───────────────────────────────────────────


async def _count_user_linked_gaps(engine) -> int:
    """Count LIBRARY songs (not discography) that still need Essentia enrichment.

    Only counts songs the user actually has in their library (library_id or
    date_added_to_library set). Discography tracks from indexer steps 2-5
    are global-only and don't block the main loop.
    """
    async with engine.begin() as conn:
        audio_gap = (await conn.execute(sa.text("""
            SELECT count(*) FROM song_metadata_cache s
            WHERE (s.library_id IS NOT NULL OR s.date_added_to_library IS NOT NULL)
              AND NOT EXISTS (
                SELECT 1 FROM audio_features_cache af
                WHERE af.catalog_id = s.catalog_id
                  AND af.user_id = s.user_id
                  AND (
                    (af.feature_source::text LIKE '%essentia%'
                     AND af.feature_source::text NOT LIKE '%reccobeats%'
                     AND af.feature_source::text NOT LIKE '%deezer%'
                     AND af.feature_source::text NOT LIKE '%soundstat%')
                    OR (af.feature_source::text LIKE '%no_data_available%'
                        AND af.feature_source::text NOT LIKE '%reccobeats%')
                    OR af.feature_source::text LIKE '%permanently_failed%'
                  )
            )
        """))).scalar() or 0

    if audio_gap > 0:
        logger.debug("User-linked gaps: %d library songs missing audio features", audio_gap)

    return audio_gap


async def _count_global_gaps(engine) -> int:
    """Count GLOBAL/discovered tracks that still need enrichment.

    Sum of three categories:
      - GPU gaps: audio_embeddings_global rows missing CLAP or MERT
      - Essentia gaps: global_song_cache with valid ISRC but no audio_features_global row
      - ISRC gaps: global_song_cache with NULL/empty ISRC (not yet attempted)

    Excludes library songs entirely — those are measured by
    _count_user_linked_gaps. This is what the DISCOVERED drain watches.
    """
    async with engine.begin() as conn:
        gpu_gap = (await conn.execute(sa.text("""
            SELECT count(*) FROM audio_embeddings_global
            WHERE (clap_embedding IS NULL OR mert_embedding IS NULL)
              AND embedding IS NOT NULL
        """))).scalar() or 0

        ess_gap = (await conn.execute(sa.text("""
            SELECT count(*) FROM global_song_cache g
            WHERE g.isrc IS NOT NULL AND g.isrc <> '' AND g.isrc <> '__NO_ISRC__'
              AND NOT EXISTS (
                SELECT 1 FROM audio_features_global afg
                WHERE afg.isrc = g.isrc AND afg.energy IS NOT NULL
              )
        """))).scalar() or 0

        isrc_gap = (await conn.execute(sa.text("""
            SELECT count(*) FROM global_song_cache
            WHERE (isrc IS NULL OR isrc = '')
        """))).scalar() or 0

    total = int(gpu_gap + ess_gap + isrc_gap)
    if total > 0:
        logger.debug(
            "Global gaps: %d GPU, %d Essentia, %d ISRC (total %d)",
            gpu_gap, ess_gap, isrc_gap, total,
        )
    return total


# ── Startup Drains ─────────────────────────────────────────────────────
#
# Strict two-phase ordering: finish EVERY user-linked enrichment gap
# (ISRC → preview → Essentia → EffNet → GPU) before touching global /
# discovered data. Matches the main-loop invariant that cobweb is skipped
# while any user gap remains; extends it to startup where previously the
# worker kicked everything off in parallel and the backend fired a
# second, redundant chain on its own.


async def _drain_user_linked(
    engine, settings, *, max_iterations: int = 10,
) -> dict[str, int]:
    """Loop ISRC → preview → Essentia → EffNet → GPU until no user gaps
    remain (or no progress is made — permanent failures).
    """
    totals: dict[str, int] = {
        "isrc": 0, "previews": 0,
        "essentia": 0, "effnet": 0, "gpu": 0,
    }

    for it in range(max_iterations):
        await _set_status(
            engine, "user_drain",
            f"Pass {it + 1}: filling user-linked gaps",
        )

        progress = 0

        isrc = await _backfill_isrcs(engine, batch_limit=500)
        totals["isrc"] += isrc
        progress += isrc

        prev = await _download_all_previews(engine)
        totals["previews"] += prev
        progress += prev

        ess = await _fill_library_gaps(engine, settings)
        totals["essentia"] += ess
        progress += ess

        effnet = await _backfill_embeddings(engine, settings)
        totals["effnet"] += effnet
        progress += effnet

        gpu = await _backfill_gpu_embeddings(engine, settings)
        totals["gpu"] += gpu
        progress += gpu

        remaining = await _count_user_linked_gaps(engine)
        logger.info(
            "User drain pass %d: +%d isrc, +%d prev, +%d ess, +%d eff, "
            "+%d gpu  (%d user gaps remain)",
            it + 1, isrc, prev, ess, effnet, gpu, remaining,
        )

        if remaining == 0:
            logger.info(
                "✓ User-linked drain complete after %d pass(es) — "
                "totals: %s", it + 1, totals,
            )
            return totals
        if progress == 0:
            logger.info(
                "User-linked drain: no progress this pass, %d gaps remain "
                "(likely permanent failures). Proceeding to discovered.",
                remaining,
            )
            return totals

    logger.warning(
        "User-linked drain: hit max iterations (%d), proceeding anyway",
        max_iterations,
    )
    return totals


async def _drain_discovered(
    engine, settings, *, max_iterations: int = 10,
) -> dict[str, int]:
    """Loop ISRC → Essentia (via _enrich_global_songs) → GPU (with URL
    fallback per V 6.367) until no global gaps remain.
    """
    totals: dict[str, int] = {"isrc": 0, "essentia": 0, "gpu": 0}

    for it in range(max_iterations):
        await _set_status(
            engine, "discovered_drain",
            f"Pass {it + 1}: filling global/discovered gaps",
        )

        progress = 0

        isrc = await _backfill_isrcs(engine, batch_limit=500)
        totals["isrc"] += isrc
        progress += isrc

        ess = await _enrich_global_songs(engine, settings)
        totals["essentia"] += ess
        progress += ess

        gpu = await _backfill_gpu_embeddings_global(engine, settings)
        totals["gpu"] += gpu
        progress += gpu

        remaining = await _count_global_gaps(engine)
        logger.info(
            "Discovered drain pass %d: +%d isrc, +%d ess, +%d gpu  "
            "(%d global gaps remain)",
            it + 1, isrc, ess, gpu, remaining,
        )

        if remaining == 0:
            logger.info(
                "✓ Discovered drain complete after %d pass(es) — totals: %s",
                it + 1, totals,
            )
            return totals
        if progress == 0:
            logger.info(
                "Discovered drain: no progress this pass, %d gaps remain "
                "(permanent failures). Entering main loop.",
                remaining,
            )
            return totals

    logger.warning(
        "Discovered drain: hit max iterations (%d), entering main loop",
        max_iterations,
    )
    return totals


# ── Permanent Failure Marker ───────────────────────────────────────────


async def _mark_permanently_failed(engine, catalog_id: str, user_id: str) -> None:
    """Insert a permanently_failed marker into audio_features_cache.

    This ensures the track is excluded from future gap-fill queries
    (the query already skips rows with feature_source containing
    'no_data_available').
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    feature_source = json.dumps({
        "_status": "permanently_failed",
        "_reason": "enrichment_error_exceeded_retries",
    })
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO audio_features_cache"
                " (catalog_id, user_id, feature_source, enriched_at, analyzed_at)"
                " VALUES (:cid, :uid, :fs, :now, :now)"
                " ON CONFLICT (catalog_id, user_id) DO UPDATE SET"
                " feature_source = :fs, analyzed_at = :now"
            ), {"cid": catalog_id, "uid": user_id, "fs": feature_source, "now": now})
        logger.info(
            "Marked %s (user %s) as permanently failed after %d retries",
            catalog_id, user_id[:8], _FAIL_MAX_RETRIES,
        )
    except Exception:
        logger.debug(
            "Failed to mark %s as permanently failed", catalog_id, exc_info=True,
        )


# ── Library Gap Fill (priority enrichment) ──────────────────────────────


async def _fill_library_gaps(engine, settings) -> int:
    """Find user LIBRARY songs missing audio features and enrich them.

    Only processes songs the user actually has in their library (library_id or
    date_added_to_library set). Discography tracks are global-only and enriched
    by _enrich_global_songs instead.
    """
    from musicmind.db.schema import (
        audio_features_cache,
        song_metadata_cache,
        user_indexing_status,
        users,
    )
    from musicmind.engine.enrichment.orchestrator import enrich_tracks

    total_enriched = 0

    async with engine.begin() as conn:
        result = await conn.execute(sa.select(users.c.id))
        user_ids = [row.id for row in result]

    for user_id in user_ids:
        # Skip users with ACTIVELY running indexing (updated in last 5 min).
        # Stale indexing (stuck from a previous crash) should not block the worker.
        try:
            from datetime import UTC, datetime, timedelta
            async with engine.begin() as conn:
                idx_row = (await conn.execute(
                    sa.select(
                        user_indexing_status.c.step,
                        user_indexing_status.c.updated_at,
                    ).where(user_indexing_status.c.user_id == user_id)
                )).first()
                if idx_row and idx_row.step < 7:
                    updated = idx_row.updated_at
                    if updated and updated.tzinfo is None:
                        updated = updated.replace(tzinfo=UTC)
                    if updated and (datetime.now(UTC) - updated) < timedelta(minutes=5):
                        continue  # Actively running — yield
                    # Stale indexing — force to complete so worker can proceed
                    await conn.execute(
                        sa.update(user_indexing_status)
                        .where(user_indexing_status.c.user_id == user_id)
                        .values(step=7, step_name="complete", updated_at=datetime.now(UTC))
                    )
                    logger.info(
                        "User %s: stale indexing (step %d) force-completed",
                        user_id[:8], idx_row.step,
                    )
        except Exception:
            pass

        # Find songs needing Essentia enrichment.
        # Skip: permanently failed OR fully essentia (no stale sources mixed in).
        async with engine.begin() as conn:
            attempted_ids = sa.select(audio_features_cache.c.catalog_id).where(
                sa.and_(
                    audio_features_cache.c.user_id == user_id,
                    sa.or_(
                        # Fully essentia: has essentia AND no stale sources
                        sa.and_(
                            sa.cast(
                                audio_features_cache.c.feature_source, sa.Text
                            ).like('%essentia%'),
                            ~sa.cast(
                                audio_features_cache.c.feature_source, sa.Text
                            ).like('%reccobeats%'),
                            ~sa.cast(
                                audio_features_cache.c.feature_source, sa.Text
                            ).like('%deezer%'),
                            ~sa.cast(
                                audio_features_cache.c.feature_source, sa.Text
                            ).like('%soundstat%'),
                        ),
                        # Permanently failed (no preview available)
                        sa.and_(
                            sa.cast(
                                audio_features_cache.c.feature_source, sa.Text
                            ).like('%no_data_available%'),
                            ~sa.cast(
                                audio_features_cache.c.feature_source, sa.Text
                            ).like('%reccobeats%'),
                        ),
                        # Permanently failed (enrichment errors exceeded retries)
                        sa.cast(
                            audio_features_cache.c.feature_source, sa.Text
                        ).like('%permanently_failed%'),
                    ),
                )
            )
            result = await conn.execute(
                sa.select(song_metadata_cache).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == user_id,
                        sa.or_(
                            song_metadata_cache.c.library_id.isnot(None),
                            song_metadata_cache.c.date_added_to_library.isnot(None),
                        ),
                        song_metadata_cache.c.catalog_id.notin_(attempted_ids),
                    )
                )
            )
            rows = result.fetchall()

        if not rows:
            continue

        logger.info(
            "User %s: %d library songs missing enrichment, filling gaps",
            user_id[:8], len(rows),
        )

        tracks = []
        for row in rows:
            # Skip tracks that have exceeded the failure threshold
            fail_key = f"{row.catalog_id}:{user_id}"
            if _failed_tracks.get(fail_key, 0) >= _FAIL_MAX_RETRIES:
                continue

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
                "preview_url": getattr(row, "preview_url", "") or "",
                "service_source": getattr(row, "service_source", ""),
                "genre_names": genres,
            })

        # Log summary of skipped tracks (once per cycle, not per-track)
        skipped_count = len(rows) - len(tracks)
        if skipped_count > 0:
            logger.info(
                "User %s: skipping %d tracks that failed enrichment >= %d times",
                user_id[:8], skipped_count, _FAIL_MAX_RETRIES,
            )

        if not tracks:
            continue

        try:
            r = await enrich_tracks(
                engine, tracks, user_id=user_id,
                modal_endpoint_url=getattr(settings, "modal_endpoint_url", None),
            )
            total_enriched += r.get("essentia", 0)

            # Track per-track failures and mark permanently failed
            for cid in r.get("failed_ids", []):
                fail_key = f"{cid}:{user_id}"
                _failed_tracks[fail_key] = _failed_tracks.get(fail_key, 0) + 1
                if _failed_tracks[fail_key] >= _FAIL_MAX_RETRIES:
                    await _mark_permanently_failed(engine, cid, user_id)
        except Exception:
            # Mark all tracks in this batch as failed so they aren't
            # retried every cycle (the root cause of ~300 warnings/day).
            for t in tracks:
                fail_key = f"{t['catalog_id']}:{user_id}"
                _failed_tracks[fail_key] = (
                    _failed_tracks.get(fail_key, 0) + 1
                )
                if _failed_tracks[fail_key] >= _FAIL_MAX_RETRIES:
                    try:
                        await _mark_permanently_failed(
                            engine, t["catalog_id"], user_id,
                        )
                    except Exception:
                        pass
            logger.warning(
                "User %s: library gap-fill enrichment failed"
                " (%d tracks marked as batch failure)",
                user_id[:8], len(tracks),
                exc_info=True,
            )

    return total_enriched


# ── Global Enrichment ────────────────────────────────────────────────────


async def _enrich_global_songs(engine, settings) -> int:
    """Enrich global_song_cache songs that are missing audio features."""
    from musicmind.db.schema import audio_features_global, global_song_cache
    from musicmind.engine.enrichment.orchestrator import enrich_tracks

    # Find songs with ISRC not yet in audio_features_global
    async with engine.begin() as conn:
        enriched_isrcs = sa.select(audio_features_global.c.isrc).correlate(None)
        result = await conn.execute(
            sa.select(global_song_cache).where(
                sa.and_(
                    global_song_cache.c.isrc.isnot(None),
                    global_song_cache.c.isrc != NO_ISRC_SENTINEL,
                    global_song_cache.c.isrc.notin_(enriched_isrcs),
                )
            ).limit(50)
        )
        rows = result.fetchall()

    if not rows:
        return 0

    tracks = []
    global_user = "__global__"
    for row in rows:
        fail_key = f"{row.catalog_id}:{global_user}"
        if _failed_tracks.get(fail_key, 0) >= _FAIL_MAX_RETRIES:
            continue

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
            "service_source": row.service_source or "",
            "genre_names": genres,
            "preview_url": getattr(row, "preview_url", "") or "",
        })

    skipped_count = len(rows) - len(tracks)
    if skipped_count > 0:
        logger.info(
            "Global: skipping %d tracks that failed enrichment >= %d times",
            skipped_count, _FAIL_MAX_RETRIES,
        )

    if not tracks:
        return 0

    logger.info("Enriching %d global songs", len(tracks))

    try:
        # Use enrich_tracks with a system user_id for global enrichment.
        # Features are also stored globally by ISRC via _store_global_features.
        r = await enrich_tracks(
            engine, tracks, user_id=global_user,
            modal_endpoint_url=getattr(settings, "modal_endpoint_url", None),
        )

        # Track per-track failures and mark permanently failed
        for cid in r.get("failed_ids", []):
            fail_key = f"{cid}:{global_user}"
            _failed_tracks[fail_key] = _failed_tracks.get(fail_key, 0) + 1
            if _failed_tracks[fail_key] >= _FAIL_MAX_RETRIES:
                await _mark_permanently_failed(engine, cid, global_user)

        return r.get("essentia", 0)
    except Exception:
        # Mark all tracks in this batch as failed to stop infinite retry.
        for t in tracks:
            fail_key = f"{t['catalog_id']}:{global_user}"
            _failed_tracks[fail_key] = (
                _failed_tracks.get(fail_key, 0) + 1
            )
            if _failed_tracks[fail_key] >= _FAIL_MAX_RETRIES:
                try:
                    await _mark_permanently_failed(
                        engine, t["catalog_id"], global_user,
                    )
                except Exception:
                    pass
        logger.debug(
            "Global song enrichment failed (%d tracks marked as"
            " batch failure)",
            len(tracks),
            exc_info=True,
        )
        return 0




# ── Embedding Backfill (runs every deploy) ─────────────────────────────


async def _backfill_embeddings(engine, settings) -> int:
    """Embedding-only backfill: extract EffNet embeddings for tracks that
    have Essentia scalars but are missing embeddings. Runs once per deploy.

    Does NOT use enrich_tracks (which skips already-enriched tracks).
    Instead: download preview → ONNX EffNet → store embedding directly.
    User-linked songs have top priority.
    """
    from musicmind.db.schema import (
        audio_embeddings,
        audio_features_cache,
        song_metadata_cache,
        users,
    )
    from musicmind.engine.audio.essentia_extractor import (
        is_essentia_available,
        is_onnx_available,
    )

    if not is_essentia_available() or not is_onnx_available():
        logger.warning("Backfill skipped: essentia=%s onnx=%s",
                        is_essentia_available(), is_onnx_available())
        return 0

    total = 0
    concurrency = 10

    async with engine.begin() as conn:
        user_rows = await conn.execute(sa.select(users.c.id))
        user_ids = [r.id for r in user_rows if r.id != "__global__"]

    for user_id in user_ids:
        async with engine.begin() as conn:
            has_embed = sa.select(audio_embeddings.c.catalog_id).where(
                sa.and_(
                    audio_embeddings.c.user_id == user_id,
                    sa.cast(audio_embeddings.c.embedding, sa.Text) != "[]",
                )
            )
            result = await conn.execute(
                sa.select(
                    song_metadata_cache.c.catalog_id,
                    song_metadata_cache.c.name,
                    song_metadata_cache.c.artist_name,
                    song_metadata_cache.c.preview_url,
                    song_metadata_cache.c.isrc,
                ).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == user_id,
                        song_metadata_cache.c.preview_url.isnot(None),
                        song_metadata_cache.c.preview_url != "",
                        song_metadata_cache.c.catalog_id.notin_(has_embed),
                        song_metadata_cache.c.catalog_id.in_(
                            sa.select(audio_features_cache.c.catalog_id).where(
                                sa.and_(
                                    audio_features_cache.c.user_id == user_id,
                                    sa.cast(
                                        audio_features_cache.c.feature_source,
                                        sa.Text,
                                    ).like("%essentia%"),
                                )
                            )
                        ),
                    )
                )
            )
            rows = result.fetchall()

        if not rows:
            continue

        logger.info(
            "Embedding backfill: %d tracks for user %s",
            len(rows), user_id[:8],
        )

        sem = asyncio.Semaphore(concurrency)

        async def _extract_one(
            catalog_id: str, name: str, artist_name: str,
            preview_url: str, isrc: str,
        ) -> bool:
            async with sem:
                try:
                    from musicmind.engine.audio.essentia_extractor import (
                        extract_all,
                    )
                    from musicmind.engine.enrichment.orchestrator import (
                        _cache_preview_audio,
                        _download_preview,
                        _get_cached_audio,
                        _store_embedding,
                    )

                    # Check cached bytes first (avoids expired URL 403s)
                    audio_bytes = await _get_cached_audio(
                        engine, catalog_id,
                    )

                    if audio_bytes is None:
                        audio_bytes = await _download_preview(preview_url)

                        # Expired Deezer URL? Refresh via name/ISRC
                        if (
                            audio_bytes is None
                            and "dzcdn.net" in preview_url
                        ):
                            try:
                                from musicmind.engine.enrichment.deezer import (
                                    search_preview_url,
                                )
                                fresh = await search_preview_url(
                                    name=name,
                                    artist_name=artist_name,
                                    isrc=isrc or None,
                                )
                                if fresh:
                                    audio_bytes = await _download_preview(
                                        fresh,
                                    )
                            except Exception:
                                pass

                        # Cache for reuse by GPU phase
                        if audio_bytes:
                            await _cache_preview_audio(
                                engine, catalog_id,
                                audio_bytes, preview_url,
                            )

                    if not audio_bytes:
                        return False

                    _, embedding = extract_all(audio_bytes)
                    del audio_bytes

                    if embedding and len(embedding) >= 128:
                        await _store_embedding(
                            engine, catalog_id, user_id,
                            embedding, isrc=isrc or "",
                        )
                        return True
                except Exception:
                    logger.debug(
                        "Embedding backfill failed for %s", catalog_id,
                    )
                return False

        # Process in batches of 50
        batch_size = 50
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            results = await asyncio.gather(
                *[_extract_one(
                    r.catalog_id, r.name or "", r.artist_name or "",
                    r.preview_url, r.isrc or "",
                ) for r in batch],
                return_exceptions=True,
            )
            ok = sum(1 for r in results if r is True)
            total += ok
            logger.info(
                "Embedding backfill %d/%d: %d/%d stored",
                i // batch_size + 1,
                (len(rows) + batch_size - 1) // batch_size,
                ok, len(batch),
            )

    logger.info("Embedding backfill complete: %d total", total)
    return total


# ── GPU Backfill (CLAP + MERT) ──────────────────────────────────────────


async def _backfill_gpu_embeddings(engine, settings) -> int:
    """Standalone GPU backfill: send cached audio bytes to Modal for
    tracks that have EffNet embeddings but lack CLAP/MERT.

    Runs independently of enrich_tracks() so GPU enrichment isn't
    blocked by Essentia gap-fill completion.
    User-linked tracks have priority over global.
    """
    import base64

    from musicmind.db.schema import (
        audio_embeddings,
        users,
    )
    from musicmind.engine.enrichment.gpu_client import (
        GPU_MIN_BATCH,
        enrich_bytes_concurrent,
    )
    from musicmind.engine.enrichment.orchestrator import _get_cached_audio

    modal_url = getattr(settings, "modal_endpoint_url", None)
    if not modal_url:
        logger.debug("GPU backfill skipped: no modal_endpoint_url")
        return 0

    total_stored = 0

    async with engine.begin() as conn:
        user_rows = await conn.execute(sa.select(users.c.id))
        user_ids = [r.id for r in user_rows]

    for user_id in user_ids:
        # Find tracks with embeddings row but no CLAP
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    audio_embeddings.c.catalog_id,
                ).where(
                    sa.and_(
                        audio_embeddings.c.user_id == user_id,
                        audio_embeddings.c.clap_embedding.is_(None),
                    )
                ).limit(100)
            )
            rows = result.fetchall()

        if not rows:
            continue

        # Resolve cached audio for each catalog_id (those without bytes
        # are skipped — URL fallback isn't used by the backfill path).
        bytes_batch: list[tuple[str, bytes]] = []
        skipped_no_bytes = 0
        for row in rows:
            cached = await _get_cached_audio(engine, row.catalog_id)
            if cached:
                bytes_batch.append((row.catalog_id, cached))
            else:
                skipped_no_bytes += 1

        if not bytes_batch:
            if skipped_no_bytes:
                logger.debug(
                    "GPU backfill: %d tracks for user %s had no cached audio",
                    skipped_no_bytes, user_id[:8],
                )
            continue

        if len(bytes_batch) < GPU_MIN_BATCH:
            logger.info(
                "GPU backfill: only %d tracks for user %s, deferring to "
                "next cycle (min batch %d)",
                len(bytes_batch), user_id[:8], GPU_MIN_BATCH,
            )
            continue

        logger.info(
            "GPU backfill: %d tracks for user %s (concurrent batched)",
            len(bytes_batch), user_id[:8],
        )

        b64_items = [
            base64.b64encode(audio).decode("ascii")
            for _, audio in bytes_batch
        ]
        try:
            results = await enrich_bytes_concurrent(b64_items, modal_url)
        except Exception:
            logger.warning(
                "GPU backfill dispatch failed for user %s",
                user_id[:8], exc_info=True,
            )
            continue

        user_stored = 0
        for (cid, _), gpu_data in zip(bytes_batch, results):
            if not gpu_data:
                continue
            # Store CLAP and MERT independently — one may succeed while
            # the other fails (e.g., MERT can't read M4A via soundfile).
            clap = gpu_data.get("clap_512")
            mert = gpu_data.get("mert_768")
            if clap or mert:
                async with engine.begin() as conn:
                    # COALESCE(existing, new) — keep whichever column was
                    # already populated; only fill NULLs. Prevents a pointless
                    # overwrite when we re-ran GPU only to get MERT and the
                    # existing CLAP was already good. Argument order matters:
                    # COALESCE(:new, existing) would *always* overwrite; we
                    # want the opposite.
                    await conn.execute(sa.text(
                        "UPDATE audio_embeddings"
                        " SET clap_embedding = COALESCE(clap_embedding, :clap),"
                        "     mert_embedding = COALESCE(mert_embedding, :mert)"
                        " WHERE catalog_id = :cid AND user_id = :uid"
                    ), {
                        "cid": cid, "uid": user_id,
                        "clap": json.dumps(clap) if clap else None,
                        "mert": json.dumps(mert) if mert else None,
                    })
                user_stored += 1
            if gpu_data.get("error"):
                logger.debug(
                    "GPU partial error for %s: %s",
                    cid, gpu_data["error"][:100],
                )

        if skipped_no_bytes:
            logger.debug(
                "GPU backfill: %d tracks without cached audio (user %s), skipped",
                skipped_no_bytes, user_id[:8],
            )

        total_stored += user_stored
        if user_stored > 0:
            logger.info(
                "GPU backfill: %d CLAP/MERT stored for user %s",
                user_stored, user_id[:8],
            )

    return total_stored


async def _backfill_gpu_embeddings_global(engine, settings) -> int:
    """GPU backfill for global songs (audio_embeddings_global).

    Phase 1 — Pre-resolve: fetch fresh Deezer preview URLs for all
    tracks missing cached audio (concurrent, ~10 req/s). iTunes is
    used only when Deezer has no result for an ISRC.

    Phase 2 — GPU dispatch: send the full resolved queue to the GPU
    in one shot (chunked internally by enrich_bytes_concurrent).
    Timeout scales with batch size.
    """
    import base64

    from musicmind.db.schema import audio_embeddings_global, global_song_cache
    from musicmind.engine.enrichment.gpu_client import (
        GPU_MIN_BATCH,
        enrich_bytes_concurrent,
    )
    from musicmind.engine.enrichment.isrc_lookup import deezer_preview_by_isrc
    from musicmind.engine.enrichment.orchestrator import (
        _cache_preview_audio,
        _download_preview,
        _get_cached_audio,
    )

    modal_url = getattr(settings, "modal_endpoint_url", None)
    if not modal_url:
        logger.debug("Global GPU backfill skipped: no modal_endpoint_url")
        return 0

    async with engine.begin() as conn:
        rows_both = (await conn.execute(
            sa.select(audio_embeddings_global.c.isrc).where(
                sa.and_(
                    audio_embeddings_global.c.clap_embedding.is_(None),
                    audio_embeddings_global.c.embedding.isnot(None),
                )
            )
        )).fetchall()
        rows_mert_only = (await conn.execute(
            sa.select(audio_embeddings_global.c.isrc).where(
                sa.and_(
                    audio_embeddings_global.c.clap_embedding.isnot(None),
                    audio_embeddings_global.c.mert_embedding.is_(None),
                    audio_embeddings_global.c.embedding.isnot(None),
                )
            )
        )).fetchall()

    if not rows_both and not rows_mert_only:
        return 0

    logger.info(
        "Global GPU backfill: %d need both, %d need MERT-only",
        len(rows_both), len(rows_mert_only),
    )

    all_isrcs = [r.isrc for r in (rows_both + rows_mert_only) if r.isrc]
    isrc_to_cid: dict[str, str] = {}
    isrc_to_url: dict[str, str] = {}
    isrc_to_meta: dict[str, tuple[str, str]] = {}
    if all_isrcs:
        async with engine.begin() as conn:
            gsc = await conn.execute(
                sa.select(
                    global_song_cache.c.catalog_id,
                    global_song_cache.c.isrc,
                    global_song_cache.c.preview_url,
                    global_song_cache.c.name,
                    global_song_cache.c.artist_name,
                ).where(global_song_cache.c.isrc.in_(all_isrcs))
            )
            for r in gsc:
                if r.isrc:
                    isrc_to_cid[r.isrc] = r.catalog_id
                    if r.preview_url:
                        isrc_to_url[r.isrc] = r.preview_url
                    if r.name and r.artist_name:
                        isrc_to_meta[r.isrc] = (r.name, r.artist_name)

    # ── Phase 1: mass-resolve audio (cache → download → Deezer → iTunes) ──
    resolve_sem = asyncio.Semaphore(10)
    deezer_resolved = 0
    itunes_resolved = 0
    cached_hits = 0
    url_downloaded = 0
    resolved_audio: dict[str, bytes] = {}

    async def _resolve_one(isrc: str) -> None:
        nonlocal deezer_resolved, itunes_resolved, cached_hits, url_downloaded
        cid = isrc_to_cid.get(isrc, "")
        if not cid:
            return

        # 1. Already cached
        cached = await _get_cached_audio(engine, cid)
        if cached:
            resolved_audio[isrc] = cached
            cached_hits += 1
            return

        # 2. Try stored URL (may be stale Deezer CDN)
        url = isrc_to_url.get(isrc, "")
        if url:
            audio = await _download_preview(url)
            if audio:
                await _cache_preview_audio(engine, cid, audio, url)
                resolved_audio[isrc] = audio
                url_downloaded += 1
                return

        # 3. Fresh Deezer URL via ISRC (primary fallback)
        async with resolve_sem:
            fresh_url = await deezer_preview_by_isrc(isrc)
        if fresh_url:
            audio = await _download_preview(fresh_url)
            if audio:
                await _cache_preview_audio(engine, cid, audio, fresh_url)
                async with engine.begin() as conn:
                    await conn.execute(
                        sa.update(global_song_cache)
                        .where(global_song_cache.c.catalog_id == cid)
                        .values(preview_url=fresh_url)
                    )
                resolved_audio[isrc] = audio
                deezer_resolved += 1
                return

        # 4. iTunes (last resort, only when Deezer has nothing)
        meta = isrc_to_meta.get(isrc)
        if not meta:
            return
        from musicmind.engine.enrichment.itunes import (
            search_preview_url as itunes_search,
        )
        try:
            itunes_url = await itunes_search(
                name=meta[0], artist_name=meta[1],
            )
        except Exception:
            return
        if not itunes_url:
            return
        audio = await _download_preview(itunes_url)
        if not audio:
            return
        await _cache_preview_audio(engine, cid, audio, itunes_url)
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(global_song_cache)
                .where(global_song_cache.c.catalog_id == cid)
                .values(preview_url=itunes_url)
            )
        resolved_audio[isrc] = audio
        itunes_resolved += 1

    await asyncio.gather(*[_resolve_one(isrc) for isrc in all_isrcs])

    logger.info(
        "Global GPU pre-resolve: %d/%d resolved "
        "(%d cached, %d url-ok, %d deezer, %d itunes)",
        len(resolved_audio), len(all_isrcs),
        cached_hits, url_downloaded, deezer_resolved, itunes_resolved,
    )

    # ── Phase 2: build queues and dispatch to GPU ────────────────────────
    both_set = {r.isrc for r in rows_both}
    both_queue = [
        (isrc, resolved_audio[isrc])
        for isrc in all_isrcs if isrc in both_set and isrc in resolved_audio
    ]
    mert_queue = [
        (isrc, resolved_audio[isrc])
        for isrc in all_isrcs if isrc not in both_set and isrc in resolved_audio
    ]

    async def _apply_results(queue_items, gpu_results) -> int:
        stored = 0
        clap_count = 0
        mert_count = 0
        async with engine.begin() as conn:
            for (isrc, _), res in zip(queue_items, gpu_results):
                if not res:
                    continue
                clap = res.get("clap_512")
                mert = res.get("mert_768")
                if not (clap or mert):
                    continue
                updates: dict = {}
                if clap:
                    updates["clap_embedding"] = clap
                    clap_count += 1
                if mert:
                    updates["mert_embedding"] = mert
                    mert_count += 1
                if updates:
                    await conn.execute(
                        sa.update(audio_embeddings_global)
                        .where(audio_embeddings_global.c.isrc == isrc)
                        .values(**updates)
                    )
                    stored += 1
        logger.info(
            "GPU apply: %d stored (%d clap, %d mert) from %d results",
            stored, clap_count, mert_count, len(gpu_results),
        )
        return stored

    total = 0

    async def _dispatch(queue, models_wanted: list[str]):
        nonlocal total
        if not queue:
            return
        if len(queue) < GPU_MIN_BATCH:
            logger.info(
                "Global GPU %s queue: %d items, deferring (min batch %d)",
                "/".join(models_wanted), len(queue), GPU_MIN_BATCH,
            )
            return
        b64_items = [base64.b64encode(a).decode("ascii") for _, a in queue]
        logger.info(
            "Global GPU dispatching %d items for %s",
            len(b64_items), "/".join(models_wanted),
        )
        try:
            results = await enrich_bytes_concurrent(
                b64_items, modal_url, models=models_wanted,
            )
            total += await _apply_results(queue, results)
        except Exception:
            logger.warning(
                "Global GPU dispatch failed for %s queue (%d items)",
                "/".join(models_wanted), len(queue), exc_info=True,
            )

    await _dispatch(both_queue, ["clap", "mert"])
    await _dispatch(mert_queue, ["mert"])

    if total > 0:
        logger.info("Global GPU backfill: %d CLAP/MERT stored", total)
    return total


# ── ISRC Backfill ────────────────────────────────────────────────────────


async def _backfill_isrcs(engine, *, batch_limit: int = 100) -> int:
    """Look up missing ISRCs for song_metadata_cache and global_song_cache.

    Queries both tables for rows with NULL/empty ISRC, resolves via
    Deezer (fast) then MusicBrainz (fallback), and updates in place.

    Returns the total number of ISRCs found across both tables.
    """
    from musicmind.db.schema import global_song_cache, song_metadata_cache
    from musicmind.engine.enrichment.isrc_lookup import lookup_isrc

    found_total = 0

    # ── song_metadata_cache ────────────────────────────────────────
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                song_metadata_cache.c.catalog_id,
                song_metadata_cache.c.user_id,
                song_metadata_cache.c.name,
                song_metadata_cache.c.artist_name,
                song_metadata_cache.c.isrc,
            ).where(
                sa.or_(
                    song_metadata_cache.c.isrc.is_(None),
                    song_metadata_cache.c.isrc == "",
                )
            ).limit(batch_limit)
        )
        smc_rows = result.fetchall()

    if smc_rows:
        logger.info("ISRC backfill: %d song_metadata_cache rows to check", len(smc_rows))
        smc_found = 0
        smc_marked = 0
        for row in smc_rows:
            new_isrc: str | None = None
            try:
                new_isrc = await lookup_isrc(
                    row.name or "", row.artist_name or "",
                    existing_isrc=row.isrc,
                )
            except Exception:
                logger.debug(
                    "ISRC lookup raised for %s - %s",
                    row.artist_name, row.name,
                )
                continue  # transient — leave the row for next cycle
            value = new_isrc or NO_ISRC_SENTINEL
            async with engine.begin() as conn:
                await conn.execute(
                    sa.update(song_metadata_cache).where(
                        sa.and_(
                            song_metadata_cache.c.catalog_id == row.catalog_id,
                            song_metadata_cache.c.user_id == row.user_id,
                        )
                    ).values(isrc=value)
                )
            if new_isrc:
                smc_found += 1
            else:
                smc_marked += 1
        found_total += smc_found
        logger.info(
            "ISRC backfill: %d/%d found, %d marked NO_ISRC (song_metadata_cache)",
            smc_found, len(smc_rows), smc_marked,
        )

    # ── global_song_cache ──────────────────────────────────────────
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                global_song_cache.c.catalog_id,
                global_song_cache.c.name,
                global_song_cache.c.artist_name,
                global_song_cache.c.isrc,
            ).where(
                sa.or_(
                    global_song_cache.c.isrc.is_(None),
                    global_song_cache.c.isrc == "",
                )
            ).limit(batch_limit)
        )
        gsc_rows = result.fetchall()

    if gsc_rows:
        logger.info("ISRC backfill: %d global_song_cache rows to check", len(gsc_rows))
        gsc_found = 0
        gsc_marked = 0
        for row in gsc_rows:
            new_isrc: str | None = None
            try:
                new_isrc = await lookup_isrc(
                    row.name or "", row.artist_name or "",
                    existing_isrc=row.isrc,
                )
            except Exception:
                logger.debug(
                    "ISRC lookup raised for global %s - %s",
                    row.artist_name, row.name,
                )
                continue  # transient — leave the row for next cycle
            value = new_isrc or NO_ISRC_SENTINEL
            async with engine.begin() as conn:
                await conn.execute(
                    sa.update(global_song_cache).where(
                        global_song_cache.c.catalog_id == row.catalog_id
                    ).values(isrc=value)
                )
            if new_isrc:
                gsc_found += 1
            else:
                gsc_marked += 1
        found_total += gsc_found
        logger.info(
            "ISRC backfill: %d/%d found, %d marked NO_ISRC (global_song_cache)",
            gsc_found, len(gsc_rows), gsc_marked,
        )

    return found_total


# ── Entry Point ──────────────────────────────────────────────────────────


if __name__ == "__main__":
    asyncio.run(main())
