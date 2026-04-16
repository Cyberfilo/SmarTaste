"""Per-user prioritized enrichment pipeline.

Backend-managed indexing that runs when a user connects a service.
All results are user-scoped (stored with user_id).

Enrichment uses Essentia (local CPU) + Modal GPU for embeddings.

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

# Continuous depth fraction (replaces rank-based STEP_DEPTHS).
MIN_DEPTH_FRAC = 0.15
MAX_DEPTH_FRAC = 1.0
DEPTH_SCALE = 1.2
MAX_TRACKS_PER_ARTIST = 50
AFFINITY_INCLUDE_THRESHOLD = 0.05
CAL_BOOST = 0.1


def compute_depth_fraction(affinity_score: float) -> float:
    """Map affinity score to discography enrichment fraction."""
    return max(MIN_DEPTH_FRAC, min(MAX_DEPTH_FRAC, affinity_score * DEPTH_SCALE))


def _rank_artists_by_affinity(
    freq_map: dict[str, int],
    cal_weights: dict[str, float],
) -> list[tuple[str, float]]:
    """Rank artists by affinity score (pure function, no DB).

    Score = frequency * (1 + CAL_BOOST * calibration_weight). Calibration
    boosts existing listening, doesn't replace it.
    """
    if not freq_map and not cal_weights:
        return []

    combined: dict[str, float] = {}
    for name, freq in freq_map.items():
        cal = cal_weights.get(name.lower(), 0.0)
        combined[name] = float(freq) * (1.0 + CAL_BOOST * cal)

    existing_lower = {n.lower() for n in combined}
    for cal_name, cal in cal_weights.items():
        if cal_name not in existing_lower:
            combined[cal_name.title()] = 1.0 * (1.0 + CAL_BOOST * cal)

    if not combined:
        return []

    max_score = max(combined.values())
    if max_score <= 0:
        return []

    return sorted(
        ((name, score / max_score) for name, score in combined.items()),
        key=lambda x: x[1],
        reverse=True,
    )


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

    try:
        access_token = encryption.decrypt(row.access_token_encrypted)
    except ValueError:
        logger.warning("User %s: token decryption failed, skipping indexing", user_id[:8])
        return stats

    # Auto-refresh Spotify token if expired (tokens last ~1 hour)
    if service == "spotify" and row.token_expires_at is not None:
        from datetime import UTC, timedelta

        token_expires = row.token_expires_at
        if token_expires.tzinfo is None:
            token_expires = token_expires.replace(tzinfo=UTC)
        if token_expires < datetime.now(UTC) + timedelta(seconds=60):
            refresh_encrypted = row.refresh_token_encrypted
            if refresh_encrypted:
                try:
                    from musicmind.api.services.service import (
                        refresh_spotify_token,
                        upsert_service_connection,
                    )
                    refresh_value = encryption.decrypt(refresh_encrypted)
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
                        logger.info(
                            "User %s: refreshed Spotify token", user_id[:8],
                        )
                    else:
                        logger.warning(
                            "User %s: Spotify refresh failed", user_id[:8],
                        )
                except Exception:
                    logger.warning(
                        "User %s: Spotify token refresh error",
                        user_id[:8], exc_info=True,
                    )

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

    # ── Steps 2-5: Artist discographies via continuous affinity depth ──
    ranked_artists = await _get_ranked_artists(engine, user_id=user_id)
    if ranked_artists:
        # Include any artist above the affinity threshold — no hard cap.
        # Always include at least the top 3 even if scores are thin.
        artists_to_process: list[tuple[str, float]] = [
            (n, s) for n, s in ranked_artists
            if s >= AFFINITY_INCLUDE_THRESHOLD
        ]
        if len(artists_to_process) < 3:
            artists_to_process = ranked_artists[:3]
        total_artists = len(artists_to_process)

        logger.info(
            "User %s: processing %d/%d artists above threshold %.2f",
            user_id[:8], total_artists, len(ranked_artists),
            AFFINITY_INCLUDE_THRESHOLD,
        )

        for i, (artist_name, affinity_score) in enumerate(artists_to_process):
            step = min(i + 2, 5)
            depth_frac = compute_depth_fraction(affinity_score)
            limit = max(5, int(MAX_TRACKS_PER_ARTIST * depth_frac))

            step_name = f"artist_{i + 1}_of_{total_artists}"
            await _set_indexing_status(
                engine, user_id, step, step_name,
                current=i + 1, total=total_artists,
            )

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
        logger.debug("Failed to update indexing status for user %s", user_id[:8])


async def _enrich_library_songs(
    engine,
    settings,
    *,
    user_id: str,
) -> int:
    """Enrich all library songs that are missing audio features."""
    from musicmind.db.schema import audio_features_cache, song_metadata_cache
    from musicmind.engine.enrichment.orchestrator import enrich_tracks

    # Find unenriched library songs (skip permanently failed ones)
    async with engine.begin() as conn:
        attempted_ids = sa.select(audio_features_cache.c.catalog_id).where(
            sa.and_(
                audio_features_cache.c.user_id == user_id,
                sa.or_(
                    audio_features_cache.c.energy.isnot(None),
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
        return 0

    tracks = _rows_to_track_dicts(rows)

    # Full pipeline: Essentia audio + GPU embeddings
    r = await enrich_tracks(
        engine, tracks, user_id=user_id,
        modal_endpoint_url=getattr(settings, "modal_endpoint_url", None),
    )
    enriched = r.get("essentia", 0)

    return enriched


async def _get_ranked_artists(engine, *, user_id: str) -> list[tuple[str, float]]:
    """Return (artist_name, normalized_score) tuples ranked by affinity."""
    from musicmind.db.schema import song_metadata_cache, user_calibration
    from musicmind.engine.profile import parse_artists

    async with engine.begin() as conn:
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
            )
        )
        cal_weights: dict[str, float] = {
            (row.item_name or "").lower(): float(row.weight or 0.0)
            for row in cal_result
            if row.item_name
        }

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
        )
        raw_freq: dict[str, int] = {}
        for row in freq_result:
            if not row.artist_name:
                continue
            parsed = parse_artists(row.artist_name)
            if not parsed:
                continue
            primary_name = parsed[0][0]
            raw_freq[primary_name] = raw_freq.get(primary_name, 0) + int(row.count)

    return _rank_artists_by_affinity(raw_freq, cal_weights)


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

    # Full pipeline: Essentia audio + GPU embeddings
    await enrich_tracks(
        engine, new_tracks, user_id=user_id,
        modal_endpoint_url=getattr(settings, "modal_endpoint_url", None),
    )

    return len(new_tracks)


async def _suggest_and_enrich_artists(
    engine,
    settings,
    creds: dict,
    *,
    user_id: str,
    ranked_artists: list[tuple[str, float]],
    max_artists: int,
) -> int:
    """Find and enrich suggested artists from featured collaborations.

    Uses the shared rank_cobweb_candidates logic: sum + log1p + primary-affinity
    weighting so feats from top-artist tracks outrank those from tail-artist tracks.
    """
    from musicmind.db.schema import song_metadata_cache
    from musicmind.engine.cobweb import rank_cobweb_candidates
    from musicmind.engine.profile import parse_artists

    library_set = {n.lower() for n, _ in ranked_artists}
    affinity_map = {n.lower(): s for n, s in ranked_artists}

    # Fetch raw artist strings from library (feat info preserved)
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

    # Build library_rows with primary affinity attached to each raw string
    library_rows = []
    for raw in raw_names:
        parsed = parse_artists(raw)
        primary = parsed[0][0].lower() if parsed else ""
        library_rows.append({
            "artist_name": raw,
            "primary_affinity": affinity_map.get(primary, 0.1),
        })

    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names=library_set,
        existing_cobweb_names=set(),
        max_total=max_artists,
    )

    enriched_count = 0
    for artist_name, _priority in ranked:
        limit = max(5, int(MAX_TRACKS_PER_ARTIST * 0.5))
        try:
            fetched = await _fetch_and_enrich_discography(
                engine, settings, creds, user_id=user_id,
                artist_name=artist_name, limit=limit,
            )
            enriched_count += 1 if fetched > 0 else 0
        except Exception:
            logger.debug("Suggested artist enrichment failed for '%s'", artist_name)

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


