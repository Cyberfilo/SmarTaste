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

    # ── Phase 0b: Unlink excess discovered artists ───────────────────
    # Previous indexer runs created discography songs for ALL library artists.
    # Now capped at top 20%. Delete non-library songs from artists outside
    # the top artist list, keeping only songs that are also in global_song_cache.
    await _set_status(engine, "cleanup", "Unlinking excess discovered songs")
    try:
        deleted_total = await _unlink_excess_discoveries(engine)
        if deleted_total > 0:
            logger.info("Unlinked %d excess discovered songs", deleted_total)
    except Exception:
        logger.exception("Excess discovery cleanup failed")

    # ── Main loop: library gaps → cobwebs → global enrich ──
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

    Cobweb sources:
    1. Featured artists from library songs (direct collaboration)

    Caps at library_artists * 0.2 non-library artists.
    """
    from musicmind.db.schema import (
        artist_cobweb,
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

        # Top 3 + top 20% of the rest (same cap as indexer)
        max_other = min(30, max(5, int(len(ranked) * 0.2)))
        keep_artists = {a.lower() for a in ranked[:3 + max_other]}

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
    """Count user-linked songs that still need audio enrichment.

    Returns the total number of songs across all users missing audio
    features (excluding permanently failed). When this returns 0, all
    user-linked work is done and the worker can move to cobweb/global
    enrichment.
    """
    async with engine.begin() as conn:
        audio_gap = (await conn.execute(sa.text("""
            SELECT count(*) FROM song_metadata_cache s
            WHERE NOT EXISTS (
                SELECT 1 FROM audio_features_cache af
                WHERE af.catalog_id = s.catalog_id
                  AND af.user_id = s.user_id
                  AND (af.energy IS NOT NULL
                       OR af.feature_source::text LIKE '%no_data_available%')
            )
        """))).scalar() or 0

    if audio_gap > 0:
        logger.debug("User-linked gaps: %d songs missing audio features", audio_gap)

    return audio_gap


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
                "preview_url": getattr(row, "preview_url", "") or "",
                "service_source": getattr(row, "service_source", ""),
                "genre_names": genres,
            })

        try:
            r = await enrich_tracks(
                engine, tracks, user_id=user_id,
                modal_endpoint_url=getattr(settings, "modal_endpoint_url", None),
            )
            total_enriched += r.get("essentia", 0)
        except Exception:
            logger.warning(
                "User %s: library gap-fill enrichment failed", user_id[:8], exc_info=True,
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
            "preview_url": getattr(row, "preview_url", "") or "",
        })

    logger.info("Enriching %d global songs", len(tracks))

    try:
        # Use enrich_tracks with a system user_id for global enrichment.
        # Features are also stored globally by ISRC via _store_global_features.
        r = await enrich_tracks(
            engine, tracks, user_id="__global__",
            modal_endpoint_url=getattr(settings, "modal_endpoint_url", None),
        )
        return r.get("essentia", 0)
    except Exception:
        logger.debug("Global song enrichment failed", exc_info=True)
        return 0




# ── Entry Point ──────────────────────────────────────────────────────────


if __name__ == "__main__":
    asyncio.run(main())
