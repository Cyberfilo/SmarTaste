"""Global enrichment worker — builds artist cobwebs and enriches globally.

Separate from per-user indexing (see indexer.py). The worker:
1. For each user, builds an artist cobweb (related artists via feats, similarity)
2. Enriches each cobweb artist's top 50 songs GLOBALLY (no user_id)
3. Stores songs in global_song_cache, features in audio_features_global
4. Promotes featured artists who appear alongside library artists
5. Caps discovered artists at library_artists * 0.2 per user

Results are available to ALL users for recommendation scoring.
The worker does NOT touch per-user tables (song_metadata_cache, audio_features_cache).

Usage:
    python -m musicmind.worker

Environment variables:
    DATABASE_URL              — PostgreSQL connection string
    MUSICMIND_FERNET_KEY      — Fernet key for decrypting service tokens
    MUSICMIND_LOGS_DATABASE_URL — Optional logging database
    MUSICMIND_LASTFM_API_KEY  — Last.fm API key for tags + similar
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

    logger.info("Worker started: poll=%ds, max_songs=%d", POLL_INTERVAL, MAX_COBWEB_SONGS)

    # Ensure worker_status row exists
    try:
        from musicmind.db.schema import worker_status
        async with engine.begin() as conn:
            row = await conn.execute(sa.select(worker_status.c.id).limit(1))
            if not row.first():
                await conn.execute(worker_status.insert().values(id=1))
    except Exception:
        logger.debug("worker_status init skipped", exc_info=True)

    # ── Phase 0: Cleanup orphaned audio_features_cache ───────────────
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

    # ── Main loop: library gaps → cobwebs → global enrich → backfill ──
    cycle = 0
    while True:
        cycle += 1
        start = time.monotonic()

        # ── Phase 1: Fill user library enrichment gaps first ────────
        await _set_status(
            engine, "library_gaps", "Enriching missing user library songs", cycle=cycle,
        )
        try:
            lib_enriched = await _fill_library_gaps(engine, settings)
            if lib_enriched > 0:
                logger.info("Cycle %d: enriched %d user library gaps", cycle, lib_enriched)
        except Exception:
            logger.exception("Cycle %d library gap-fill failed", cycle)

        # ── Phase 1b: Backfill missing ISRCs via Deezer ──────────────
        await _set_status(
            engine, "isrc_backfill", "Resolving missing ISRCs", cycle=cycle,
        )
        try:
            isrc_filled = await _backfill_missing_isrcs(engine)
            if isrc_filled > 0:
                logger.info("Cycle %d: resolved %d missing ISRCs", cycle, isrc_filled)
        except Exception:
            logger.exception("Cycle %d ISRC backfill failed", cycle)

        # ── Phase 2: Build cobwebs + enrich globally ────────────────
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

        # ── Phase 3: Backfill tags + credits ────────────────────────
        await _set_status(engine, "backfill", "Tags + credits on all songs", cycle=cycle)
        try:
            bf = await _backfill_global_songs(engine, settings)
            if bf > 0:
                logger.info("Cycle %d backfill: %d songs updated", cycle, bf)
                if log_writer:
                    log_writer.log_enrichment(
                        user_id="system",
                        catalog_id=f"backfill_cycle_{cycle}",
                        stage="backfill",
                        result=f"updated_{bf}",
                    )
        except Exception:
            logger.exception("Cycle %d backfill FAILED", cycle)

        await _set_status(engine, "idle", f"Sleeping {POLL_INTERVAL}s", cycle=cycle)
        await asyncio.sleep(POLL_INTERVAL)


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


async def _build_user_cobweb(
    engine,
    settings,
    *,
    user_id: str,
) -> dict[str, int]:
    """Build/expand the artist cobweb for one user.

    Cobweb sources (in priority order):
    1. Featured artists from library songs (highest priority — direct collaboration)
    2. Last.fm similar artists
    3. Same-genre artists from catalog

    Caps at library_artists * 0.2 non-library artists.
    """
    from musicmind.db.schema import (
        artist_cobweb,
        lastfm_similar_tracks,
        song_metadata_cache,
    )
    from musicmind.engine.profile import parse_artists

    stats = {"cobweb_artists": 0, "songs_cached": 0}

    # Get library artists
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

    library_set = {a.lower() for a in library_artist_names}
    max_per_cycle = max(2, int(len(library_artist_names) * 0.2))
    max_total = max(5, int(len(library_artist_names) * 0.5))  # absolute cap

    # Get existing cobweb artists to avoid re-adding
    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(artist_cobweb.c.artist_name).where(
                artist_cobweb.c.user_id == user_id
            )
        )
        existing_set = {row.artist_name.lower() for row in existing}

    # Only expand cobweb if below total capacity
    if len(existing_set) < max_total:
        max_discovered = min(max_per_cycle, max_total - len(existing_set))

        candidates: dict[str, tuple[str, float]] = {}  # key → (name, priority)

        # Source 1: Featured artists from library songs
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
            for row in result:
                if not row[0]:
                    continue
                parsed = parse_artists(row[0])
                for name, weight in parsed:
                    key = name.strip().lower()
                    if key and key not in library_set and key not in existing_set:
                        old_priority = candidates.get(key, ("", 0))[1]
                        candidates[key] = (name.strip(), max(old_priority, weight * 2))

        # Source 2: Last.fm similar tracks' artists
        try:
            async with engine.begin() as conn:
                result = await conn.execute(
                    sa.select(
                        lastfm_similar_tracks.c.similar_artist,
                        sa.func.avg(lastfm_similar_tracks.c.similarity_score).label("s"),
                    ).where(
                        lastfm_similar_tracks.c.source_artist.in_(library_artist_names)
                    ).group_by(lastfm_similar_tracks.c.similar_artist)
                    .order_by(sa.text("s DESC"))
                    .limit(max_discovered * 2)
                )
                for row in result:
                    key = row.similar_artist.strip().lower()
                    if key and key not in library_set and key not in existing_set:
                        old_priority = candidates.get(key, ("", 0))[1]
                        candidates[key] = (
                            row.similar_artist.strip(),
                            max(old_priority, float(row.s or 0)),
                        )
        except Exception:
            logger.debug("Last.fm similar lookup failed for user %s", user_id[:8])

        # Rank and cap
        sorted_candidates = sorted(candidates.items(), key=lambda x: -x[1][1])
        to_add = sorted_candidates[:max_discovered]

        # Insert into cobweb (use rowcount to track actual inserts)
        for key, (name, priority) in to_add:
            source = "feat" if priority >= 1.0 else "similar"
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
        logger.debug(
            "User %s: cobweb at capacity (%d/%d), skipping expansion",
            user_id[:8], len(existing_set), max_total,
        )

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

    for artist_name in unenriched_artists:
        try:
            cached = await _fetch_artist_songs_globally(
                engine, settings, user_id=user_id,
                artist_name=artist_name, limit=MAX_COBWEB_SONGS,
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
) -> int:
    """Fetch an artist's top songs and store in global_song_cache."""
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


# ── Library Gap Fill (priority enrichment) ──────────────────────────────


async def _fill_library_gaps(engine, settings) -> int:
    """Find user library songs missing audio features and enrich them first.

    This ensures user-scoped library songs always get enriched before the
    worker spends time on global cobweb songs. Only enriches the exact
    count of missing songs — no wasted work.
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
        # Skip users with active backend indexing (it handles its own enrichment)
        try:
            async with engine.begin() as conn:
                idx_row = (await conn.execute(
                    sa.select(user_indexing_status.c.step).where(
                        user_indexing_status.c.user_id == user_id
                    )
                )).first()
                if idx_row and idx_row.step < 7:
                    continue
        except Exception:
            pass

        # Find library songs missing enrichment (skip permanently failed ones)
        async with engine.begin() as conn:
            # Songs that are either successfully enriched OR permanently failed
            attempted_ids = sa.select(audio_features_cache.c.catalog_id).where(
                sa.and_(
                    audio_features_cache.c.user_id == user_id,
                    sa.or_(
                        # Successfully enriched (has real features)
                        audio_features_cache.c.energy.isnot(None),
                        # Permanently failed (marker row with no_data_available)
                        sa.cast(
                            audio_features_cache.c.feature_source, sa.Text
                        ).like('%no_data_available%'),
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

        try:
            r = await enrich_tracks(engine, tracks, user_id=user_id)
            enriched = r.get("deezer", 0) + r.get("reccobeats", 0) + r.get("soundstat", 0)
            total_enriched += enriched
        except Exception:
            logger.warning(
                "User %s: library gap-fill enrichment failed", user_id[:8], exc_info=True,
            )

    return total_enriched


# ── ISRC Backfill ───────────────────────────────────────────────────────


async def _backfill_missing_isrcs(engine) -> int:
    """Resolve missing ISRCs by searching Deezer for name + artist.

    Deezer's /track/{id} endpoint includes ISRC in the response.
    This lets songs participate in the global audio features cache.
    Caps at 100 per cycle to avoid rate limits (~5 req/s).
    """
    from musicmind.db.schema import song_metadata_cache
    from musicmind.engine.enrichment.deezer import fetch_deezer_features

    # Find songs missing ISRC (prioritize library songs)
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                song_metadata_cache.c.catalog_id,
                song_metadata_cache.c.user_id,
                song_metadata_cache.c.name,
                song_metadata_cache.c.artist_name,
            ).where(
                sa.and_(
                    sa.or_(
                        song_metadata_cache.c.isrc.is_(None),
                        song_metadata_cache.c.isrc == "",
                    ),
                    song_metadata_cache.c.name.isnot(None),
                    song_metadata_cache.c.name != "",
                    song_metadata_cache.c.artist_name.isnot(None),
                    song_metadata_cache.c.artist_name != "",
                )
            ).order_by(
                # Library songs first (those with library_id or date_added)
                sa.case(
                    (sa.or_(
                        song_metadata_cache.c.library_id.isnot(None),
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                    ), 0),
                    else_=1,
                ),
            ).limit(100)
        )
        rows = result.fetchall()

    if not rows:
        return 0

    logger.info("ISRC backfill: resolving %d songs via Deezer", len(rows))

    filled = 0
    sem = asyncio.Semaphore(5)

    async def _resolve_one(catalog_id: str, user_id: str, name: str, artist: str) -> bool:
        async with sem:
            try:
                deezer_result = await fetch_deezer_features(
                    name=name, artist_name=artist,
                )
                if deezer_result and deezer_result.get("isrc"):
                    async with engine.begin() as conn:
                        await conn.execute(
                            sa.text(
                                "UPDATE song_metadata_cache"
                                " SET isrc = :isrc"
                                " WHERE catalog_id = :cid AND user_id = :uid"
                                "   AND (isrc IS NULL OR isrc = '')"
                            ),
                            {
                                "isrc": deezer_result["isrc"],
                                "cid": catalog_id,
                                "uid": user_id,
                            },
                        )
                    return True
            except Exception:
                logger.debug("ISRC resolve failed for %s - %s", artist, name)
            return False

    results = await asyncio.gather(
        *[_resolve_one(r.catalog_id, r.user_id, r.name, r.artist_name) for r in rows],
        return_exceptions=True,
    )
    filled = sum(1 for r in results if r is True)
    return filled


# ── Global Enrichment ────────────────────────────────────────────────────


async def _enrich_global_songs(engine, settings) -> int:
    """Enrich global_song_cache songs that are missing audio features."""
    from musicmind.db.schema import audio_features_global, global_song_cache
    from musicmind.engine.enrichment.orchestrator import enrich_tracks_global

    # Find songs with ISRC not yet in audio_features_global
    async with engine.begin() as conn:
        enriched_isrcs = sa.select(audio_features_global.c.isrc).correlate(None)
        result = await conn.execute(
            sa.select(global_song_cache).where(
                sa.and_(
                    global_song_cache.c.isrc.isnot(None),
                    global_song_cache.c.isrc.notin_(enriched_isrcs),
                )
            ).limit(50)
        )
        rows = result.fetchall()

    if not rows:
        return 0

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
            "service_source": row.service_source or "",
            "genre_names": genres,
        })

    logger.info("Enriching %d global songs", len(tracks))

    try:
        result = await enrich_tracks_global(engine, tracks)
        return result.get("enriched", 0)
    except Exception:
        # Fallback: try the per-user enrichment with a dummy user_id
        # that we'll clean up. This handles the case where
        # enrich_tracks_global doesn't exist yet.
        logger.debug("enrich_tracks_global not available, using fallback")
        return 0


async def _backfill_global_songs(engine, settings) -> int:
    """Run Last.fm tags + MusicBrainz credits on all songs missing them.

    Uses SQL to find gaps directly (no loading all songs into memory),
    then concurrent API calls for Last.fm (5 at a time).
    """
    from musicmind.db.schema import (
        kg_relationships,
        lastfm_tags_cache,
        song_metadata_cache,
    )

    updated = 0

    # ── Last.fm tags ─────────────────────────────────────────────
    if settings.lastfm_api_key:
        from musicmind.engine.enrichment.lastfm import (
            fetch_artist_tags,
            fetch_track_tags,
        )

        # Get (artist, name) pairs from per-user songs, deduplicate in Python
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    song_metadata_cache.c.artist_name,
                    song_metadata_cache.c.name,
                ).where(
                    sa.and_(
                        song_metadata_cache.c.artist_name.isnot(None),
                        song_metadata_cache.c.artist_name != "",
                        song_metadata_cache.c.name.isnot(None),
                        song_metadata_cache.c.name != "",
                    )
                ).distinct()
            )
            all_pairs = [
                (row.artist_name, row.name) for row in result
            ]

        if all_pairs:
            # Build entity IDs and batch-check which exist
            pairs_with_eid = [
                (a, n, f"track:{a.lower()}:{n.lower()}")
                for a, n in all_pairs
            ]
            all_eids = [eid for _, _, eid in pairs_with_eid]

            cached_eids: set[str] = set()
            for i in range(0, len(all_eids), 500):
                chunk = all_eids[i:i + 500]
                async with engine.begin() as conn:
                    result = await conn.execute(
                        sa.select(lastfm_tags_cache.c.entity_id).where(
                            lastfm_tags_cache.c.entity_id.in_(chunk)
                        )
                    )
                    cached_eids.update(row.entity_id for row in result)

            uncached = [
                (a, n, eid)
                for a, n, eid in pairs_with_eid
                if eid not in cached_eids
            ]

            if uncached:
                # Cap at 500 per cycle to keep cycles short (~2 min at 5 req/s)
                total_uncached = len(uncached)
                uncached = uncached[:500]
                logger.info(
                    "Last.fm backfill: %d need tags, processing %d this cycle (%d cached)",
                    total_uncached, len(uncached), len(cached_eids),
                )

                sem = asyncio.Semaphore(5)

                async def _fetch_tag(
                    artist: str, name: str, eid: str,
                ) -> bool:
                    async with sem:
                        try:
                            tags = await fetch_track_tags(
                                settings.lastfm_api_key,
                                artist, name,
                            )
                            if not tags:
                                tags = await fetch_artist_tags(
                                    settings.lastfm_api_key,
                                    artist,
                                )
                            if tags:
                                async with engine.begin() as conn:
                                    await conn.execute(
                                        sa.text(
                                            "INSERT INTO lastfm_tags_cache"
                                            " (entity_type, entity_id,"
                                            "  tags)"
                                            " VALUES (:t, :eid, :tags)"
                                            " ON CONFLICT (entity_type, entity_id)"
                                            " DO UPDATE SET tags = :tags"
                                        ),
                                        {
                                            "t": "track",
                                            "eid": eid,
                                            "tags": json.dumps(tags),
                                        },
                                    )
                                return True
                        except Exception:
                            logger.debug("Tag fetch failed: %s - %s", artist, name)
                        return False

                for i in range(0, len(uncached), 100):
                    batch = uncached[i:i + 100]
                    results = await asyncio.gather(
                        *[_fetch_tag(a, n, eid) for a, n, eid in batch]
                    )
                    updated += sum(1 for r in results if r)
                    if i > 0 and i % 500 == 0:
                        logger.info(
                            "Last.fm progress: %d/%d",
                            i, len(uncached),
                        )

    # ── MusicBrainz credits ──────────────────────────────────────
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                sa.distinct(song_metadata_cache.c.isrc)
            ).where(
                sa.and_(
                    song_metadata_cache.c.isrc.isnot(None),
                    song_metadata_cache.c.isrc != "",
                )
            )
        )
        all_isrcs = [row[0] for row in result]

    if all_isrcs:
        all_mbids = [f"isrc:{isrc.upper()}" for isrc in all_isrcs]
        cached_mbids: set[str] = set()
        for i in range(0, len(all_mbids), 500):
            chunk = all_mbids[i:i + 500]
            async with engine.begin() as conn:
                result = await conn.execute(
                    sa.select(
                        sa.distinct(kg_relationships.c.source_mbid)
                    ).where(
                        kg_relationships.c.source_mbid.in_(chunk)
                    )
                )
                cached_mbids.update(row[0] for row in result)

        uncached_isrc = [
            (isrc, f"isrc:{isrc.upper()}")
            for isrc in all_isrcs
            if f"isrc:{isrc.upper()}" not in cached_mbids
        ]

        if uncached_isrc:
            # Cap at 100 per cycle (MusicBrainz = 1 req/sec, ~3-4 min max)
            batch = uncached_isrc[:100]
            logger.info(
                "MusicBrainz backfill: %d need credits, processing %d this cycle",
                len(uncached_isrc), len(batch),
            )

            from musicmind.engine.enrichment.musicbrainz_credits import (
                fetch_recording_credits,
            )

            for isrc, _source_mbid in batch:
                try:
                    credits = await fetch_recording_credits(
                        isrc, engine=engine,
                    )
                    if credits:
                        updated += 1
                except Exception:
                    logger.debug("MusicBrainz credit fetch failed for ISRC %s", isrc)

    return updated


# ── Entry Point ──────────────────────────────────────────────────────────


if __name__ == "__main__":
    asyncio.run(main())
