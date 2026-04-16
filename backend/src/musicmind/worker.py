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

    # ── Startup Phase 0: Cleanup ────────────────────────────────────
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

    # ── Startup Phase 1: ISRC backfill (all missing) ────────────────
    # ISRC enables global dedup — must run before audio download/enrichment
    await _set_status(engine, "startup_isrc", "Backfilling all missing ISRCs")
    try:
        isrc_found = await _backfill_isrcs(engine, batch_limit=500)
        if isrc_found > 0:
            logger.info("Startup ISRC backfill: %d found", isrc_found)
    except Exception:
        logger.exception("Startup ISRC backfill failed")

    # ── Startup Phase 2: Download + cache preview audio ─────────────
    # Refresh expired Deezer URLs and cache bytes for all unenriched tracks.
    # Cached bytes feed both Essentia (Phase 3) and GPU (Phase 4).
    await _set_status(engine, "startup_audio", "Downloading preview audio files")
    try:
        downloaded = await _download_all_previews(engine)
        if downloaded > 0:
            logger.info("Startup audio download: %d previews cached", downloaded)
    except Exception:
        logger.exception("Startup audio download failed")

    # ── Startup Phase 3: Essentia/ONNX (CPU, on Railway) ────────────
    # Uses cached bytes — no URL downloads needed
    await _set_status(engine, "startup_essentia", "Essentia analysis (CPU)")
    try:
        lib_enriched = await _fill_library_gaps(engine, settings)
        if lib_enriched > 0:
            logger.info("Startup Essentia: %d tracks enriched", lib_enriched)
    except Exception:
        logger.exception("Startup Essentia enrichment failed")

    # ── Startup Phase 4a: EffNet embedding backfill ──────────────────
    await _set_status(engine, "startup_effnet", "EffNet embedding backfill")
    try:
        backfilled = await _backfill_embeddings(engine, settings)
        if backfilled > 0:
            logger.info("Startup EffNet backfill: %d tracks", backfilled)
    except Exception:
        logger.exception("Startup EffNet backfill failed")

    # ── Startup Phase 4b: GPU enrichment (CLAP + MERT via Modal) ──
    await _set_status(engine, "startup_gpu", "GPU enrichment (CLAP + MERT)")
    try:
        gpu_stored = await _backfill_gpu_embeddings(engine, settings)
        if gpu_stored > 0:
            logger.info("Startup GPU backfill: %d CLAP/MERT stored", gpu_stored)
    except Exception:
        logger.exception("Startup GPU backfill failed")

    logger.info("Startup pipeline complete — entering main loop")

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
            isrc_found = await _backfill_isrcs(engine)
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

    # Enrich unenriched global songs
    enriched = await _enrich_global_songs(engine, settings)
    stats["songs_enriched"] = enriched

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
    """Count user-linked songs that still need Essentia enrichment.

    Includes: songs with no features, AND songs with stale/mixed features
    from deprecated sources (deezer, reccobeats, soundstat).
    Excludes: purely essentia-enriched AND purely permanently-failed.
    """
    async with engine.begin() as conn:
        audio_gap = (await conn.execute(sa.text("""
            SELECT count(*) FROM song_metadata_cache s
            WHERE NOT EXISTS (
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
        logger.debug("User-linked gaps: %d songs missing audio features", audio_gap)

    return audio_gap


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
    """Find ALL user songs missing audio features and enrich them.

    Processes every song in song_metadata_cache that lacks audio_features_cache
    entries — library AND discography. This matches _count_user_linked_gaps()
    so the main loop doesn't get stuck when non-library songs have gaps.
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
        enrich_batch_bytes_via_gpu,
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

        logger.info(
            "GPU backfill: %d tracks for user %s",
            len(rows), user_id[:8],
        )

        # Build batch: prefer cached bytes, fall back to DB preview URL
        batch_items: list[tuple[str, str | None, bytes | None]] = []
        for row in rows:
            cid = row.catalog_id
            cached = await _get_cached_audio(engine, cid)
            batch_items.append((cid, None, cached))

        # Process in GPU batches of 10
        gpu_batch_size = 10
        for i in range(0, len(batch_items), gpu_batch_size):
            chunk = batch_items[i:i + gpu_batch_size]

            # Split into bytes-based and URL-based
            bytes_chunk = [
                (cid, audio)
                for cid, _, audio in chunk if audio
            ]
            url_chunk = [
                cid for cid, _, audio in chunk if not audio
            ]

            # Bytes-based GPU call
            if bytes_chunk:
                b64_items = [
                    base64.b64encode(audio).decode("ascii")
                    for _, audio in bytes_chunk
                ]
                try:
                    results = await enrich_batch_bytes_via_gpu(
                        b64_items, modal_url,
                    )
                    for (cid, _), gpu_data in zip(
                        bytes_chunk, results,
                    ):
                        if not gpu_data:
                            continue
                        # Store CLAP/MERT independently — one may
                        # succeed while the other fails (e.g., MERT
                        # can't read M4A via soundfile)
                        clap = gpu_data.get("clap_512")
                        mert = gpu_data.get("mert_768")
                        if clap or mert:
                            async with engine.begin() as conn:
                                await conn.execute(sa.text(
                                    "UPDATE audio_embeddings"
                                    " SET clap_embedding ="
                                    "   COALESCE(:clap,"
                                    "     clap_embedding),"
                                    "     mert_embedding ="
                                    "   COALESCE(:mert,"
                                    "     mert_embedding)"
                                    " WHERE catalog_id = :cid"
                                    "   AND user_id = :uid"
                                ), {
                                    "cid": cid, "uid": user_id,
                                    "clap": json.dumps(clap)
                                    if clap else None,
                                    "mert": json.dumps(mert)
                                    if mert else None,
                                })
                            total_stored += 1
                        if gpu_data.get("error"):
                            logger.debug(
                                "GPU partial error for %s: %s",
                                cid,
                                gpu_data["error"][:100],
                            )
                except Exception:
                    logger.warning(
                        "GPU backfill batch failed",
                        exc_info=True,
                    )

            # Log skipped tracks without cached audio
            if url_chunk:
                logger.debug(
                    "GPU backfill: %d tracks without cached audio"
                    " (user %s), skipping",
                    len(url_chunk), user_id[:8],
                )

        if total_stored > 0:
            logger.info(
                "GPU backfill: %d CLAP/MERT stored for user %s",
                total_stored, user_id[:8],
            )

    return total_stored


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
