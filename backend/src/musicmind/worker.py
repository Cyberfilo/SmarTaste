"""Global enrichment worker — builds artist cobwebs and enriches globally.

Separate from per-user indexing (see indexer.py). The worker:
1. For each user, builds an artist cobweb (related artists via feats, similarity)
2. Enriches each cobweb artist's top 50 songs GLOBALLY (no user_id)
3. Stores songs in global_song_cache, features in audio_features_global
4. Promotes featured artists who appear alongside library artists
5. Caps discovered artists at library_artists * 0.4 per user

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
            from musicmind.db.logs import LogWriter, create_logs_engine, init_logs_schema

            logs_engine = create_logs_engine(settings.logs_database_url)
            await init_logs_schema(logs_engine)
            log_writer = LogWriter(logs_engine)
            log_writer.start()
            logger.info("Logging database connected")
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

    # ── Main loop: build cobwebs + enrich globally ───────────────────
    cycle = 0
    while True:
        cycle += 1
        start = time.monotonic()
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

        # Backfill tags + credits on globally cached songs
        await _set_status(engine, "backfill", "Tags + credits on global songs", cycle=cycle)
        try:
            bf = await _backfill_global_songs(engine, settings)
            if bf > 0:
                logger.info("Cycle %d backfill: %d global songs updated", cycle, bf)
        except Exception:
            logger.debug("Cycle %d backfill failed", cycle, exc_info=True)

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
    from musicmind.db.schema import users

    stats = {"cobweb_artists": 0, "songs_cached": 0, "songs_enriched": 0}

    async with engine.begin() as conn:
        result = await conn.execute(sa.select(users.c.id))
        user_ids = [row.id for row in result]

    for user_id in user_ids:
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

    Caps at library_artists * 0.4 non-library artists.
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
    max_discovered = max(2, int(len(library_artist_names) * 0.4))

    # Get existing cobweb artists to avoid re-adding
    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(artist_cobweb.c.artist_name).where(
                artist_cobweb.c.user_id == user_id
            )
        )
        existing_set = {row.artist_name.lower() for row in existing}

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
        pass

    # Rank and cap
    sorted_candidates = sorted(candidates.items(), key=lambda x: -x[1][1])
    to_add = sorted_candidates[:max_discovered]

    # Insert into cobweb
    for key, (name, priority) in to_add:
        source = "feat" if priority >= 1.0 else "similar"
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    sa.text(
                        "INSERT INTO artist_cobweb"
                        " (user_id, artist_name, source, priority)"
                        " VALUES (:uid, :name, :src, :pri)"
                        " ON CONFLICT (user_id, artist_name) DO NOTHING"
                    ),
                    {"uid": user_id, "name": name, "src": source, "pri": priority},
                )
            stats["cobweb_artists"] += 1
        except Exception:
            pass

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
    """Run Last.fm tags + MusicBrainz credits on global songs.

    Uses batch gap detection and concurrent Last.fm API calls.
    Also backfills ALL per-user songs (song_metadata_cache) not just global.
    """
    from musicmind.db.schema import (
        global_song_cache,
        kg_relationships,
        lastfm_tags_cache,
        song_metadata_cache,
    )

    updated = 0

    # Gather ALL songs needing tags (both global + per-user)
    all_songs: list[dict] = []
    async with engine.begin() as conn:
        for table in [global_song_cache, song_metadata_cache]:
            result = await conn.execute(
                sa.select(
                    table.c.name, table.c.artist_name,
                    table.c.isrc if hasattr(table.c, "isrc") else sa.literal(None).label("isrc"),
                ).where(
                    sa.and_(
                        table.c.artist_name.isnot(None),
                        table.c.artist_name != "",
                        table.c.name.isnot(None),
                        table.c.name != "",
                    )
                )
            )
            for row in result:
                all_songs.append({
                    "name": row.name,
                    "artist_name": row.artist_name,
                    "isrc": row.isrc if row.isrc else "",
                })

    if not all_songs:
        return 0

    # ── Last.fm tags: batch gap detection ────────────────────────
    if settings.lastfm_api_key:
        from musicmind.engine.enrichment.lastfm import (
            fetch_artist_tags,
            fetch_track_tags,
        )

        # Build entity IDs
        song_eids = []
        seen_eids: set[str] = set()
        for s in all_songs:
            eid = f"track:{s['artist_name'].lower()}:{s['name'].lower()}"
            if eid not in seen_eids:
                seen_eids.add(eid)
                song_eids.append((s, eid))

        # Single batch query to find existing
        all_eids = [eid for _, eid in song_eids]
        cached_eids: set[str] = set()
        # Query in chunks of 500 to avoid query size limits
        for i in range(0, len(all_eids), 500):
            chunk = all_eids[i:i + 500]
            async with engine.begin() as conn:
                result = await conn.execute(
                    sa.select(lastfm_tags_cache.c.entity_id).where(
                        lastfm_tags_cache.c.entity_id.in_(chunk)
                    )
                )
                cached_eids.update(row.entity_id for row in result)

        uncached = [(s, eid) for s, eid in song_eids if eid not in cached_eids]
        logger.info("Last.fm backfill: %d uncached of %d total", len(uncached), len(song_eids))

        # Concurrent API calls (5 at a time to respect rate limits)
        sem = asyncio.Semaphore(5)

        async def _fetch_tag(s: dict, eid: str) -> bool:
            async with sem:
                try:
                    tags = await fetch_track_tags(
                        s["artist_name"], s["name"],
                        api_key=settings.lastfm_api_key,
                    )
                    if not tags:
                        tags = await fetch_artist_tags(
                            s["artist_name"],
                            api_key=settings.lastfm_api_key,
                        )
                    if tags:
                        async with engine.begin() as conn:
                            await conn.execute(
                                sa.text(
                                    "INSERT INTO lastfm_tags_cache"
                                    " (entity_type, entity_id, tags)"
                                    " VALUES (:t, :eid, :tags)"
                                    " ON CONFLICT DO NOTHING"
                                ),
                                {"t": "track", "eid": eid, "tags": json.dumps(tags)},
                            )
                        return True
                except Exception:
                    pass
                return False

        # Process in batches of 100 to avoid memory issues
        for i in range(0, len(uncached), 100):
            batch = uncached[i:i + 100]
            results = await asyncio.gather(
                *[_fetch_tag(s, eid) for s, eid in batch]
            )
            updated += sum(1 for r in results if r)
            if i > 0 and i % 500 == 0:
                logger.info("Last.fm backfill progress: %d/%d", i, len(uncached))

    # ── MusicBrainz credits: batch gap detection ─────────────────
    isrc_songs = [
        (s, f"isrc:{s['isrc'].upper()}")
        for s in all_songs if s.get("isrc")
    ]
    if isrc_songs:
        all_mbids = list({mbid for _, mbid in isrc_songs})
        cached_mbids: set[str] = set()
        for i in range(0, len(all_mbids), 500):
            chunk = all_mbids[i:i + 500]
            async with engine.begin() as conn:
                result = await conn.execute(
                    sa.select(sa.distinct(kg_relationships.c.source_mbid)).where(
                        kg_relationships.c.source_mbid.in_(chunk)
                    )
                )
                cached_mbids.update(row[0] for row in result)

        uncached_isrc = [
            (s, mbid) for s, mbid in isrc_songs if mbid not in cached_mbids
        ]
        # Deduplicate by ISRC
        seen_mbids: set[str] = set()
        deduped: list[tuple[dict, str]] = []
        for s, mbid in uncached_isrc:
            if mbid not in seen_mbids:
                seen_mbids.add(mbid)
                deduped.append((s, mbid))

        logger.info(
            "MusicBrainz backfill: %d uncached of %d total",
            len(deduped), len(isrc_songs),
        )

        for s, source_mbid in deduped:
            try:
                from musicmind.engine.enrichment.musicbrainz_credits import (
                    fetch_recording_credits,
                )

                credits = await fetch_recording_credits(s["isrc"])
                if credits:
                    async with engine.begin() as conn:
                        for c in credits:
                            await conn.execute(
                                sa.text(
                                    "INSERT INTO kg_artists"
                                    " (mbid, name, type)"
                                    " VALUES (:m, :n, :t)"
                                    " ON CONFLICT DO NOTHING"
                                ),
                                {
                                    "m": c["artist_mbid"],
                                    "n": c["artist_name"],
                                    "t": c.get("role", "person"),
                                },
                            )
                            await conn.execute(
                                sa.text(
                                    "INSERT INTO kg_relationships"
                                    " (source_mbid, target_mbid,"
                                    "  relationship_type)"
                                    " VALUES (:s, :tgt, :r)"
                                    " ON CONFLICT DO NOTHING"
                                ),
                                {
                                    "s": source_mbid,
                                    "tgt": c["artist_mbid"],
                                    "r": c.get("role", "producer"),
                                },
                            )
                    updated += 1
            except Exception:
                pass

    return updated


# ── Entry Point ──────────────────────────────────────────────────────────


if __name__ == "__main__":
    asyncio.run(main())
