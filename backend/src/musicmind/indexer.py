"""Per-user prioritized enrichment pipeline.

Backend-managed indexing that runs when a user connects a service.
All results are user-scoped (stored with user_id).

6-step pipeline (in priority order):
  1. Library songs — enrich all songs in the user's library
  2. Top artist — 100% discography
  3. 2nd artist — 70% discography
  4. 3rd artist — 50% discography
  5. Other library artists — 30% discography each
  6. Suggested artists — (library_artist_count * 0.2) new artists, 50% each

Each step updates user_indexing_status so the admin dashboard can show progress.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa

logger = logging.getLogger("musicmind.indexer")

# Discography depth per step (fraction of available top songs to fetch)
STEP_DEPTHS = {
    1: 1.0,    # top artist → 100%
    2: 0.70,   # 2nd artist → 70%
    3: 0.50,   # 3rd artist → 50%
    "other": 0.30,  # remaining library artists → 30%
    "suggested": 0.50,  # suggested artists → 50%
}
MAX_TRACKS_PER_ARTIST = 50  # Apple Music / Spotify max for top-songs


# ── Main Entry Point ─────────────────────────────────────────────────────


async def run_indexing(
    engine,
    encryption,
    settings,
    *,
    user_id: str,
) -> dict[str, Any]:
    """Run the full 6-step indexing pipeline for a user.

    Called by the backend as a background task when a user connects a service
    or triggers a re-index.
    """
    from musicmind.api.services.service import (
        detect_apple_music_storefront,
        generate_apple_developer_token,
        get_user_connections,
    )
    from musicmind.db.schema import service_connections, user_indexing_status

    stats = {"library_enriched": 0, "discography_fetched": 0, "suggested": 0}

    # Get user's service connection
    connections = await get_user_connections(engine, user_id=user_id)
    if not connections:
        logger.info("User %s: no service connections, skipping indexing", user_id[:8])
        return stats

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

    creds = {
        "service": service,
        "access_token": access_token,
        "developer_token": developer_token,
        "storefront": storefront,
    }

    # ── Step 0: Ensure indexing status row exists ────────────────────
    await _set_indexing_status(engine, user_id, 0, "starting")

    # ── Step 1: Enrich all library songs ────────────────────────────
    await _set_indexing_status(engine, user_id, 1, "library_songs")
    try:
        enriched = await _enrich_library_songs(engine, settings, user_id=user_id)
        stats["library_enriched"] = enriched
        logger.info("User %s step 1: %d library songs enriched", user_id[:8], enriched)
    except Exception:
        logger.warning("User %s step 1 failed", user_id[:8], exc_info=True)

    # ── Steps 2-5: Artist discographies at decreasing depth ─────────
    ranked_artists = await _get_ranked_artists(engine, user_id=user_id)
    if ranked_artists:
        total_artists = len(ranked_artists)
        for i, artist_name in enumerate(ranked_artists):
            step = min(i + 2, 5)  # Steps 2, 3, 4, 5 (5 = "other")
            if i == 0:
                depth_frac = STEP_DEPTHS[1]
            elif i == 1:
                depth_frac = STEP_DEPTHS[2]
            elif i == 2:
                depth_frac = STEP_DEPTHS[3]
            else:
                depth_frac = STEP_DEPTHS["other"]

            step_name = f"artist_{i + 1}_of_{total_artists}"
            await _set_indexing_status(
                engine, user_id, step, step_name,
                current=i + 1, total=total_artists,
            )

            limit = max(5, int(MAX_TRACKS_PER_ARTIST * depth_frac))
            try:
                fetched = await _fetch_and_enrich_discography(
                    engine, settings, creds, user_id=user_id,
                    artist_name=artist_name, limit=limit,
                )
                stats["discography_fetched"] += fetched
            except Exception:
                logger.debug(
                    "User %s: discography fetch failed for '%s'",
                    user_id[:8], artist_name,
                )

    # ── Step 6: Suggest new artists ─────────────────────────────────
    max_suggested = max(2, int(len(ranked_artists) * 0.2)) if ranked_artists else 0
    if max_suggested > 0:
        await _set_indexing_status(engine, user_id, 6, "suggesting_artists")
        try:
            suggested = await _suggest_and_enrich_artists(
                engine, settings, creds, user_id=user_id,
                ranked_artists=ranked_artists,
                max_artists=max_suggested,
            )
            stats["suggested"] = suggested
            logger.info(
                "User %s step 6: %d suggested artists enriched",
                user_id[:8], suggested,
            )
        except Exception:
            logger.warning("User %s step 6 failed", user_id[:8], exc_info=True)

    # ── Done ────────────────────────────────────────────────────────
    await _set_indexing_status(engine, user_id, 7, "complete")
    async with engine.begin() as conn:
        await conn.execute(
            sa.update(user_indexing_status)
            .where(user_indexing_status.c.user_id == user_id)
            .values(completed_at=datetime.now(UTC))
        )

    logger.info(
        "User %s indexing complete: %d library, %d discography, %d suggested",
        user_id[:8],
        stats["library_enriched"],
        stats["discography_fetched"],
        stats["suggested"],
    )
    return stats


# ── Step Helpers ─────────────────────────────────────────────────────────


async def _set_indexing_status(
    engine,
    user_id: str,
    step: int,
    step_name: str,
    *,
    current: int = 0,
    total: int = 0,
) -> None:
    """Upsert user_indexing_status row."""
    from musicmind.db.schema import user_indexing_status

    now = datetime.now(UTC)
    try:
        async with engine.begin() as conn:
            existing = await conn.execute(
                sa.select(user_indexing_status.c.user_id).where(
                    user_indexing_status.c.user_id == user_id
                )
            )
            if existing.first():
                await conn.execute(
                    sa.update(user_indexing_status)
                    .where(user_indexing_status.c.user_id == user_id)
                    .values(
                        step=step, step_name=step_name,
                        progress_current=current, progress_total=total,
                        updated_at=now,
                    )
                )
            else:
                await conn.execute(
                    user_indexing_status.insert().values(
                        user_id=user_id, step=step, step_name=step_name,
                        progress_current=current, progress_total=total,
                        started_at=now, updated_at=now,
                    )
                )
    except Exception:
        pass


async def _enrich_library_songs(
    engine,
    settings,
    *,
    user_id: str,
) -> int:
    """Enrich all library songs that are missing audio features."""
    from musicmind.db.schema import audio_features_cache, song_metadata_cache
    from musicmind.engine.enrichment.orchestrator import enrich_tracks

    # Find unenriched library songs
    async with engine.begin() as conn:
        enriched_ids = sa.select(audio_features_cache.c.catalog_id).where(
            sa.and_(
                audio_features_cache.c.user_id == user_id,
                audio_features_cache.c.energy.isnot(None),
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
                    song_metadata_cache.c.catalog_id.notin_(enriched_ids),
                )
            )
        )
        rows = result.fetchall()

    if not rows:
        return 0

    tracks = _rows_to_track_dicts(rows)

    r = await enrich_tracks(engine, tracks, user_id=user_id)
    enriched = r.get("deezer", 0) + r.get("reccobeats", 0) + r.get("soundstat", 0)

    # Also run Last.fm + MusicBrainz on library songs
    await _backfill_tags_credits(engine, settings, tracks)

    return enriched


async def _get_ranked_artists(engine, *, user_id: str) -> list[str]:
    """Get user's artists ranked by calibration weight then frequency."""
    from musicmind.db.schema import song_metadata_cache, user_calibration

    artists: list[tuple[str, float]] = []

    async with engine.begin() as conn:
        # Calibration-ranked artists (highest priority)
        cal_result = await conn.execute(
            sa.select(
                user_calibration.c.item_name,
                user_calibration.c.weight,
            ).where(
                sa.and_(
                    user_calibration.c.user_id == user_id,
                    user_calibration.c.calibration_type.in_(
                        ["top_artist", "artist_rank"]
                    ),
                )
            ).order_by(user_calibration.c.weight.desc())
        )
        cal_artists = {row.item_name: row.weight for row in cal_result}

        # All library artists by frequency
        freq_result = await conn.execute(
            sa.select(
                song_metadata_cache.c.artist_name,
                sa.func.count().label("count"),
            ).where(
                sa.and_(
                    song_metadata_cache.c.user_id == user_id,
                    sa.or_(
                        song_metadata_cache.c.library_id.isnot(None),
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                    ),
                )
            ).group_by(song_metadata_cache.c.artist_name)
            .order_by(sa.text("count DESC"))
        )
        freq_artists = {row.artist_name: row.count for row in freq_result}

    # Merge: calibration first, then frequency
    seen: set[str] = set()
    for name, weight in sorted(cal_artists.items(), key=lambda x: -x[1]):
        if name and name.lower() not in seen:
            seen.add(name.lower())
            artists.append((name, weight * 100))

    for name, count in sorted(freq_artists.items(), key=lambda x: -x[1]):
        if name and name.lower() not in seen:
            seen.add(name.lower())
            artists.append((name, count))

    return [name for name, _ in artists]


async def _fetch_and_enrich_discography(
    engine,
    settings,
    creds: dict,
    *,
    user_id: str,
    artist_name: str,
    limit: int,
) -> int:
    """Fetch an artist's top tracks and enrich them."""
    from musicmind.api.recommendations.fetch import (
        _fetch_artist_top_tracks,
        _search_artist_id,
    )
    from musicmind.db.schema import song_metadata_cache
    from musicmind.engine.enrichment.orchestrator import enrich_tracks

    artist_id = await _search_artist_id(
        creds["service"], creds["access_token"], artist_name,
        developer_token=creds["developer_token"],
        storefront=creds["storefront"],
    )
    if not artist_id:
        return 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        tracks = await _fetch_artist_top_tracks(
            client, creds["service"], creds["access_token"], artist_id,
            developer_token=creds["developer_token"],
            storefront=creds["storefront"],
            limit=limit,
        )

    if not tracks:
        return 0

    # Cache new tracks (skip existing)
    new_tracks: list[dict[str, Any]] = []
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
                    catalog_id=cid, user_id=user_id,
                    name=track.get("name", ""),
                    artist_name=track.get("artist_name", ""),
                    album_name=track.get("album_name", ""),
                    genre_names=json.dumps(track.get("genre_names", [])),
                    duration_ms=track.get("duration_ms"),
                    release_date=track.get("release_date"),
                    isrc=track.get("isrc"),
                    preview_url=track.get("preview_url", ""),
                    service_source=creds["service"],
                )
            )
            new_tracks.append(track)

    if not new_tracks:
        return 0

    # Enrich
    await enrich_tracks(engine, new_tracks, user_id=user_id)
    await _backfill_tags_credits(engine, settings, new_tracks)

    return len(new_tracks)


async def _suggest_and_enrich_artists(
    engine,
    settings,
    creds: dict,
    *,
    user_id: str,
    ranked_artists: list[str],
    max_artists: int,
) -> int:
    """Find and enrich suggested artists from feats and similar tracks."""
    from musicmind.db.schema import lastfm_similar_tracks, song_metadata_cache
    from musicmind.engine.profile import parse_artists

    library_set = {a.lower() for a in ranked_artists}
    candidates: dict[str, float] = {}

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
        raw_names = [row[0] for row in result if row[0]]

    for raw_name in raw_names:
        parsed = parse_artists(raw_name)
        for name, weight in parsed:
            key = name.strip().lower()
            if key and key not in library_set and len(key) > 1:
                candidates[key] = candidates.get(key, 0) + weight * 2

    # Source 2: Last.fm similar tracks' artists
    async with engine.begin() as conn:
        try:
            result = await conn.execute(
                sa.select(
                    lastfm_similar_tracks.c.similar_artist,
                    sa.func.sum(lastfm_similar_tracks.c.similarity_score).label("score"),
                ).group_by(lastfm_similar_tracks.c.similar_artist)
                .order_by(sa.text("score DESC"))
                .limit(max_artists * 3)
            )
            for row in result:
                key = row.similar_artist.strip().lower()
                if key and key not in library_set:
                    candidates[key] = candidates.get(key, 0) + (row.score or 0)
        except Exception:
            pass

    # Rank and cap
    sorted_candidates = sorted(candidates.items(), key=lambda x: -x[1])
    selected = [name for name, _ in sorted_candidates[:max_artists]]

    enriched_count = 0
    for artist_name in selected:
        limit = max(5, int(MAX_TRACKS_PER_ARTIST * STEP_DEPTHS["suggested"]))
        try:
            fetched = await _fetch_and_enrich_discography(
                engine, settings, creds, user_id=user_id,
                artist_name=artist_name.title(), limit=limit,
            )
            enriched_count += 1 if fetched > 0 else 0
        except Exception:
            pass

    return enriched_count


# ── Shared Helpers ───────────────────────────────────────────────────────


def _rows_to_track_dicts(rows) -> list[dict[str, Any]]:
    """Convert SQLAlchemy rows to track dicts for the enrichment pipeline."""
    tracks: list[dict[str, Any]] = []
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
    return tracks


async def _backfill_tags_credits(
    engine,
    settings,
    tracks: list[dict[str, Any]],
) -> None:
    """Run Last.fm tags + MusicBrainz credits on a batch of tracks.

    Uses batch gap detection (single query) instead of per-song checks,
    and concurrent Last.fm API calls (5 at a time) for speed.
    """
    import asyncio

    from musicmind.db.schema import kg_relationships, lastfm_tags_cache

    # ── Last.fm tags: batch gap detection ────────────────────────
    if settings.lastfm_api_key:
        from musicmind.engine.enrichment.lastfm import (
            fetch_artist_tags,
            fetch_track_tags,
        )

        # Build entity IDs for all tracks
        track_eids: list[tuple[dict, str]] = []
        for t in tracks:
            if t.get("artist_name") and t.get("name"):
                eid = f"track:{t['artist_name'].lower()}:{t['name'].lower()}"
                track_eids.append((t, eid))

        if track_eids:
            # Single query: find which entity_ids already exist
            all_eids = [eid for _, eid in track_eids]
            async with engine.begin() as conn:
                result = await conn.execute(
                    sa.select(lastfm_tags_cache.c.entity_id).where(
                        lastfm_tags_cache.c.entity_id.in_(all_eids)
                    )
                )
                cached_eids = {row.entity_id for row in result}

            uncached = [
                (t, eid) for t, eid in track_eids if eid not in cached_eids
            ]

            if uncached:
                sem = asyncio.Semaphore(5)

                async def _fetch_and_store_tags(t: dict, eid: str) -> None:
                    async with sem:
                        try:
                            tags = await fetch_track_tags(
                                settings.lastfm_api_key,
                                t["artist_name"], t["name"],
                            )
                            if not tags:
                                tags = await fetch_artist_tags(
                                    settings.lastfm_api_key,
                                    t["artist_name"],
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
                                        {
                                            "t": "track",
                                            "eid": eid,
                                            "tags": json.dumps(tags),
                                        },
                                    )
                        except Exception:
                            pass

                await asyncio.gather(
                    *[_fetch_and_store_tags(t, eid) for t, eid in uncached]
                )

    # ── MusicBrainz credits: batch gap detection ─────────────────
    isrc_tracks = [
        (t, f"isrc:{t['isrc'].upper()}")
        for t in tracks if t.get("isrc")
    ]
    if isrc_tracks:
        all_mbids = [mbid for _, mbid in isrc_tracks]
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(sa.distinct(kg_relationships.c.source_mbid)).where(
                    kg_relationships.c.source_mbid.in_(all_mbids)
                )
            )
            cached_mbids = {row[0] for row in result}

        uncached_isrc = [
            (t, mbid) for t, mbid in isrc_tracks if mbid not in cached_mbids
        ]

        from musicmind.engine.enrichment.musicbrainz_credits import (
            fetch_recording_credits,
        )

        for t, _source_mbid in uncached_isrc:
            try:
                # fetch_recording_credits handles its own storage
                await fetch_recording_credits(t["isrc"], engine=engine)
            except Exception:
                pass
