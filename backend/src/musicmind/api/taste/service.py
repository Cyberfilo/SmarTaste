"""Taste profile pipeline: staleness check, fetch, cache, compute, return.

Supports single-service profiles (spotify, apple_music) and unified profiles
that merge data from both services with cross-service genre normalization
and track deduplication.

Auto-rebuild (V 6.387): after returning any cached snapshot, the service
compares snapshot.computed_at against the max timestamp of centroid-
affecting sources (audio_embeddings.analyzed_at, audio_features_cache,
user_calibration). If any source is newer, a background rebuild is
fired fire-and-forget — the user gets the cached snapshot immediately,
and the next request sees the refreshed one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from musicmind.api.services.service import (
    generate_apple_developer_token,
    get_user_connections,
    refresh_spotify_token,
    upsert_service_connection,
)
from musicmind.api.taste.fetch import (
    enrich_spotify_genres,
    fetch_apple_music_library,
    fetch_apple_music_recently_played,
    fetch_spotify_recently_played,
    fetch_spotify_saved_tracks,
    fetch_spotify_top_artists,
    fetch_spotify_top_tracks,
)
from musicmind.db.schema import (
    audio_embeddings,
    service_connections,
    song_metadata_cache,
    taste_profile_snapshots,
)
from musicmind.engine.dedup import deduplicate_tracks
from musicmind.engine.genres import normalize_genre_list
from musicmind.engine.profile import build_taste_profile
from musicmind.security.encryption import EncryptionService

logger = logging.getLogger(__name__)

STALENESS_HOURS = 24  # Per D-06

# V 6.387 — auto-rebuild dedup. Module-level set tracks which
# (user_id, service) pairs have an in-flight background rebuild so we don't
# fan out duplicate work when multiple requests notice the same staleness.
_IN_FLIGHT_REBUILDS: set[str] = set()
_REBUILD_LOCK = asyncio.Lock()


def _as_utc(dt: datetime | str | None) -> datetime | None:
    """Normalize to tz-aware UTC datetime; return None on failure."""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class TasteService:
    """Orchestrates the taste profile pipeline.

    Pipeline: staleness check -> fetch from service API -> cache raw data ->
    compute profile via engine -> save snapshot -> return structured JSON.
    """

    async def get_profile(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Get the user's taste profile — returns instantly from cache.

        Never blocks the user waiting for a full API re-fetch. If the
        cached profile is stale (>24h), returns it immediately and the
        caller can trigger a background refresh separately.

        Only blocks on first-ever profile (no snapshot exists at all),
        which happens once after service connection.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for token decryption.
            settings: Application settings.
            user_id: SmarTaste user ID.
            service: Target service (spotify or apple_music). Auto-detected if None.
            force_refresh: Force full re-fetch (used by background tasks only).

        Returns:
            Taste profile dict with genre_vector, top_artists, etc.
        """
        resolved_service = await self._resolve_service(
            engine, user_id=user_id, service=service
        )

        if not force_refresh:
            # Try fresh cache first (<24h)
            snapshot = await self._get_fresh_snapshot(
                engine, user_id=user_id, service_source=resolved_service
            )
            if snapshot is not None:
                # Even if fresh, check if any centroid-affecting signal has
                # changed since the snapshot was computed. If so, fire a
                # background rebuild — user still gets the cached response.
                await self._maybe_trigger_rebuild(
                    engine, encryption, settings,
                    user_id=user_id,
                    service=resolved_service,
                    snapshot_computed_at=snapshot.get("computed_at"),
                )
                return snapshot

            # Return ANY existing snapshot (even stale) — never block the user
            stale = await self._get_any_snapshot(
                engine, user_id=user_id, service_source=resolved_service
            )
            if stale is not None:
                logger.info(
                    "Returning stale profile for user %s (will refresh in background)",
                    user_id,
                )
                # Stale by definition → always kick off a background refresh
                await self._maybe_trigger_rebuild(
                    engine, encryption, settings,
                    user_id=user_id,
                    service=resolved_service,
                    snapshot_computed_at=stale.get("computed_at"),
                    force=True,
                )
                return stale

        # No snapshot at all — must build from scratch (first-time only)
        logger.info("Building first profile for user %s service %s", user_id, resolved_service)

        if resolved_service == "unified":
            return await self._build_unified_profile(
                engine, encryption, settings, user_id=user_id,
            )

        songs, history = await self._fetch_and_cache_data(
            engine,
            encryption,
            settings,
            user_id=user_id,
            service=resolved_service,
        )

        profile = await self._compute_and_save_profile(
            engine,
            user_id=user_id,
            service=resolved_service,
            songs=songs,
            history=history,
            settings=settings,
        )
        return profile

    async def _resolve_service(
        self, engine, *, user_id: str, service: str | None
    ) -> str:
        """Resolve which service to use for the taste profile.

        If service is explicitly provided, return it directly.
        If None, auto-detect: both services connected -> "unified",
        single service -> that service name.
        """
        if service is not None:
            return service

        connections = await get_user_connections(engine, user_id=user_id)
        if not connections:
            raise ValueError("No connected service found")

        services = {c["service"] for c in connections}
        if "spotify" in services and "apple_music" in services:
            return "unified"

        return connections[0]["service"]

    async def _build_unified_profile(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
    ) -> dict[str, Any]:
        """Build a unified profile from both connected services.

        Fetches data from both Spotify and Apple Music, applies cross-service
        track deduplication and genre normalization, then builds a single
        merged taste profile.

        Returns:
            Unified taste profile dict with services_included field.
        """
        all_songs: list[dict] = []
        all_history: list[dict] = []
        services_included: list[str] = []

        # Try fetching from each service, tolerate individual failures
        for svc in ("spotify", "apple_music"):
            try:
                songs, history = await self._fetch_and_cache_data(
                    engine, encryption, settings,
                    user_id=user_id, service=svc,
                )
                all_songs.extend(songs)
                all_history.extend(history)
                services_included.append(svc)
            except ValueError:
                logger.info(
                    "No %s connection for user %s, skipping in unified profile",
                    svc, user_id,
                )
            except Exception:
                logger.exception(
                    "Failed to fetch %s data for unified profile, skipping", svc,
                )

        if not all_songs and not all_history:
            raise ValueError("No data fetched from any connected service")

        # Cross-service deduplication
        deduplicated_songs = deduplicate_tracks(all_songs)

        # Normalize genres on all songs
        for song in deduplicated_songs:
            genres = song.get("genre_names") or []
            if isinstance(genres, str):
                genres = [genres]
            song["genre_names"] = normalize_genre_list(genres)

        for entry in all_history:
            genres = entry.get("genre_names") or []
            if isinstance(genres, str):
                genres = [genres]
            entry["genre_names"] = normalize_genre_list(genres)

        # Build unified profile
        profile = await self._compute_and_save_profile(
            engine,
            user_id=user_id,
            service="unified",
            songs=deduplicated_songs,
            history=all_history,
            settings=settings,
        )

        profile["services_included"] = services_included
        return profile

    @staticmethod
    async def _profile_needs_rebuild(
        engine,
        *,
        user_id: str,
        snapshot_computed_at: datetime | str | None,
    ) -> tuple[bool, str | None]:
        """Return (needs_rebuild, reason) for auto-rebuild.

        Compares `snapshot_computed_at` against the max timestamp of any
        centroid-affecting source:
        - `audio_embeddings.analyzed_at` — new CLAP/MERT/EffNet for this user
        - `audio_features_cache.analyzed_at` — new scalar features
        - `user_calibration.created_at` — onboarding calibration changes

        All three are checked in a single query. Fast (<10ms). A ``None``
        snapshot timestamp always triggers a rebuild.
        """
        snapshot_dt = _as_utc(snapshot_computed_at)
        if snapshot_dt is None:
            return True, "no snapshot timestamp"

        async with engine.begin() as conn:
            row = (await conn.execute(sa.text("""
                SELECT
                  (SELECT MAX(analyzed_at) FROM audio_embeddings
                    WHERE user_id = :uid) AS emb_ts,
                  (SELECT MAX(analyzed_at) FROM audio_features_cache
                    WHERE user_id = :uid) AS feat_ts,
                  (SELECT MAX(created_at) FROM user_calibration
                    WHERE user_id = :uid) AS cal_ts
            """), {"uid": user_id})).first()

        if row is None:
            return False, None

        sources = [
            ("embeddings", row.emb_ts),
            ("features", row.feat_ts),
            ("calibration", row.cal_ts),
        ]
        for label, ts in sources:
            ts_utc = _as_utc(ts)
            if ts_utc is not None and ts_utc > snapshot_dt:
                return True, f"{label} changed at {ts_utc.isoformat()}"

        return False, None

    async def _maybe_trigger_rebuild(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
        snapshot_computed_at: datetime | str | None,
        force: bool = False,
    ) -> None:
        """Fire background rebuild if sources changed (or always when force=True).

        Idempotent: if a rebuild is already in flight for the same
        (user_id, service) pair, this is a no-op. Never blocks the caller.
        """
        if not force:
            needs_rebuild, reason = await self._profile_needs_rebuild(
                engine, user_id=user_id,
                snapshot_computed_at=snapshot_computed_at,
            )
            if not needs_rebuild:
                return
            logger.info(
                "Auto-rebuild for user %s (%s): %s",
                user_id[:8], service, reason,
            )

        key = f"{user_id}:{service}"
        async with _REBUILD_LOCK:
            if key in _IN_FLIGHT_REBUILDS:
                return
            _IN_FLIGHT_REBUILDS.add(key)

        async def _do_rebuild() -> None:
            try:
                await self.get_profile(
                    engine, encryption, settings,
                    user_id=user_id, service=service, force_refresh=True,
                )
                logger.info(
                    "Auto-rebuild complete for user %s (%s)",
                    user_id[:8], service,
                )
            except Exception:
                logger.exception(
                    "Auto-rebuild failed for user %s (%s)",
                    user_id[:8], service,
                )
            finally:
                async with _REBUILD_LOCK:
                    _IN_FLIGHT_REBUILDS.discard(key)

        asyncio.create_task(_do_rebuild())

    async def _get_any_snapshot(
        self, engine, *, user_id: str, service_source: str
    ) -> dict[str, Any] | None:
        """Return the latest profile snapshot regardless of age.

        Used as a fallback when fresh cache is expired — returns stale
        data instantly so the user isn't blocked.
        """
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(taste_profile_snapshots)
                .where(
                    sa.and_(
                        taste_profile_snapshots.c.user_id == user_id,
                        taste_profile_snapshots.c.service_source == service_source,
                    )
                )
                .order_by(taste_profile_snapshots.c.computed_at.desc())
                .limit(1)
            )
            row = result.first()

        if row is None:
            return None
        return self._row_to_profile(row)

    async def _get_fresh_snapshot(
        self, engine, *, user_id: str, service_source: str
    ) -> dict[str, Any] | None:
        """Return a cached profile snapshot if fresh (< 24h old)."""
        cutoff = datetime.now(UTC) - timedelta(hours=STALENESS_HOURS)
        cutoff_naive = cutoff.replace(tzinfo=None)

        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(taste_profile_snapshots)
                .where(
                    sa.and_(
                        taste_profile_snapshots.c.user_id == user_id,
                        taste_profile_snapshots.c.service_source == service_source,
                        taste_profile_snapshots.c.computed_at >= cutoff_naive,
                    )
                )
                .order_by(taste_profile_snapshots.c.computed_at.desc())
                .limit(1)
            )
            row = result.first()

        if row is None:
            return None
        return self._row_to_profile(row)

    @staticmethod
    def _row_to_profile(row) -> dict[str, Any]:
        """Convert a taste_profile_snapshots row to a profile dict."""
        mapping = row._mapping

        def _pj(val: Any, default: Any) -> Any:
            if val is None:
                return default
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    return default
            return val

        return {
            "service": mapping["service_source"],
            "computed_at": (
                mapping["computed_at"].isoformat()
                if mapping["computed_at"]
                else datetime.now(UTC).isoformat()
            ),
            "genre_vector": _pj(mapping["genre_vector"], {}),
            "top_artists": _pj(mapping["top_artists"], []),
            "audio_trait_preferences": _pj(
                mapping["audio_trait_preferences"], {}
            ),
            "release_year_distribution": _pj(
                mapping["release_year_distribution"], {}
            ),
            "familiarity_score": mapping["familiarity_score"] or 0.0,
            "total_songs_analyzed": mapping["total_songs_analyzed"] or 0,
            "listening_hours_estimated": mapping["listening_hours_estimated"] or 0.0,
            "audio_centroid": _pj(mapping.get("audio_centroid"), {}),
            "embedding_centroid": _pj(mapping.get("embedding_centroid"), None),
            "clap_centroid": _pj(mapping.get("clap_centroid"), None),
            "mert_centroid": _pj(mapping.get("mert_centroid"), None),
            "mood_distribution": _pj(mapping.get("mood_distribution"), None),
        }

    async def _fetch_and_cache_data(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
    ) -> tuple[list[dict], list[dict]]:
        """Fetch listening data from the service API and cache it locally.

        Handles Spotify token refresh if expired. For Apple Music, generates
        a fresh developer token.

        Returns:
            Tuple of (songs, history) lists.
        """
        # Get connection from DB
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

        if row is None:
            raise ValueError(f"No {service} connection found for user")

        access_token = encryption.decrypt(row.access_token_encrypted)

        if service == "spotify":
            songs, history = await self._fetch_spotify_data(
                engine, encryption, settings,
                user_id=user_id,
                row=row,
                access_token=access_token,
            )
        elif service == "apple_music":
            songs, history = await self._fetch_apple_music_data(
                settings, access_token=access_token
            )
        else:
            raise ValueError(f"Unsupported service: {service}")

        # Cache songs
        await self._cache_songs(engine, user_id=user_id, service=service, songs=songs)
        # Cache history
        await self._cache_history(
            engine, user_id=user_id, service=service, history=history
        )

        return songs, history

    async def _fetch_spotify_data(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        row,
        access_token: str,
    ) -> tuple[list[dict], list[dict]]:
        """Fetch data from Spotify, refreshing token if needed."""
        # Check token expiration, refresh if needed
        token_expires_at = row.token_expires_at
        now = datetime.now(UTC)
        if token_expires_at is not None:
            if token_expires_at.tzinfo is None:
                token_expires_at = token_expires_at.replace(tzinfo=UTC)
            if token_expires_at < now + timedelta(seconds=60):
                logger.info("Spotify token expired or expiring soon, refreshing")
                refresh_token_encrypted = row.refresh_token_encrypted
                if refresh_token_encrypted:
                    refresh_token_value = encryption.decrypt(refresh_token_encrypted)
                    token_data = await refresh_spotify_token(
                        refresh_token_value, settings.spotify_client_id
                    )
                    if token_data:
                        access_token = token_data["access_token"]
                        await upsert_service_connection(
                            engine,
                            encryption,
                            user_id=user_id,
                            service="spotify",
                            access_token=access_token,
                            refresh_token=token_data.get(
                                "refresh_token", refresh_token_value
                            ),
                            expires_in=token_data.get("expires_in"),
                            service_user_id=row.service_user_id,
                        )

        # Fetch from Spotify
        top_tracks = await fetch_spotify_top_tracks(access_token)
        artists = await fetch_spotify_top_artists(access_token)
        saved_tracks = await fetch_spotify_saved_tracks(access_token)
        recently_played = await fetch_spotify_recently_played(access_token)

        # Enrich with genres from artist data
        enrich_spotify_genres(top_tracks + saved_tracks, artists)

        # Deduplicate songs by catalog_id
        seen_ids: set[str] = set()
        songs: list[dict] = []
        for track in top_tracks + saved_tracks:
            catalog_id = track.get("catalog_id", "")
            if catalog_id and catalog_id not in seen_ids:
                seen_ids.add(catalog_id)
                songs.append(track)

        return songs, recently_played

    async def _fetch_apple_music_data(
        self, settings, *, access_token: str
    ) -> tuple[list[dict], list[dict]]:
        """Fetch data from Apple Music using developer token.

        Also fetches ratings (Love/Dislike) and applies them to songs.
        """
        developer_token = generate_apple_developer_token(
            settings.apple_team_id,
            settings.apple_key_id,
            settings.apple_private_key_path,
            private_key_b64=settings.apple_private_key_b64,
        )
        music_user_token = access_token

        library_songs = await fetch_apple_music_library(
            developer_token, music_user_token
        )
        recently_played = await fetch_apple_music_recently_played(
            developer_token, music_user_token
        )

        # Fetch ratings (Love = 1, Dislike = -1) and apply to songs
        try:
            from musicmind.api.taste.fetch import fetch_apple_music_ratings

            ratings = await fetch_apple_music_ratings(
                developer_token, music_user_token,
            )
            if ratings:
                rating_map = {r["catalog_id"]: r["rating"] for r in ratings}
                for song in library_songs:
                    cid = song.get("catalog_id", "")
                    if cid in rating_map:
                        song["user_rating"] = rating_map[cid]
                logger.info("Applied %d Apple Music ratings to library songs", len(ratings))
        except Exception:
            logger.debug("Failed to fetch Apple Music ratings (optional)")

        return library_songs, recently_played

    async def _cache_songs(
        self, engine, *, user_id: str, service: str, songs: list[dict]
    ) -> None:
        """Cache song metadata using dialect-agnostic SELECT-then-INSERT/UPDATE."""
        async with engine.begin() as conn:
            for song in songs:
                catalog_id = song.get("catalog_id", "")
                if not catalog_id:
                    continue

                existing = await conn.execute(
                    sa.select(song_metadata_cache.c.catalog_id).where(
                        sa.and_(
                            song_metadata_cache.c.catalog_id == catalog_id,
                            song_metadata_cache.c.user_id == user_id,
                        )
                    )
                )
                row = existing.first()

                values = {
                    "name": song.get("name", ""),
                    "artist_name": song.get("artist_name", ""),
                    "album_name": song.get("album_name", ""),
                    "genre_names": json.dumps(song.get("genre_names", [])),
                    "duration_ms": song.get("duration_ms"),
                    "release_date": song.get("release_date"),
                    "isrc": song.get("isrc"),
                    "editorial_notes": song.get("editorial_notes", ""),
                    "audio_traits": json.dumps(song.get("audio_traits", [])),
                    "has_lyrics": song.get("has_lyrics", False),
                    "content_rating": song.get("content_rating"),
                    "artwork_bg_color": song.get("artwork_bg_color", ""),
                    "artwork_url_template": song.get("artwork_url_template", ""),
                    "preview_url": song.get("preview_url", ""),
                    "user_rating": song.get("user_rating"),
                    "date_added_to_library": song.get("date_added_to_library"),
                    "service_source": service,
                    "library_id": song.get("library_id"),
                }

                if row:
                    await conn.execute(
                        song_metadata_cache.update()
                        .where(
                            sa.and_(
                                song_metadata_cache.c.catalog_id == catalog_id,
                                song_metadata_cache.c.user_id == user_id,
                            )
                        )
                        .values(**values)
                    )
                else:
                    await conn.execute(
                        song_metadata_cache.insert().values(
                            catalog_id=catalog_id,
                            user_id=user_id,
                            **values,
                        )
                    )

    async def _cache_history(
        self, engine, *, user_id: str, service: str, history: list[dict]
    ) -> None:
        """No-op: listening_history and play_count_proxy tables were removed.

        Previously cached history entries and updated play count proxy.
        Kept as a stub so callers don't break.
        """
        logger.debug(
            "Skipping history cache for user %s (%d entries) — tables removed",
            user_id, len(history),
        )

    async def _apply_engagement_weights(
        self,
        engine,
        *,
        user_id: str,
        songs: list[dict],
    ) -> list[dict]:
        """Return songs with uniform weight (1.0 each).

        play_count_proxy table was removed. Previously weighted songs by
        observed play frequency. Now returns the input list unchanged so
        all songs contribute equally to the profile.
        """
        return songs

    async def _apply_calibration_weights(
        self,
        engine,
        *,
        user_id: str,
        songs: list[dict],
    ) -> list[dict]:
        """Apply user calibration weights to song list.

        - Playlist calibrations (weight 5.0): duplicate all songs by those artists/playlists
        - Top artist picks (weight 5.0): boost top-3 artists' songs
        - Artist ranking (weight based on position): ordered priority
        - Playlist song calibrations (weight 3.0): duplicate selected songs

        Uses song duplication to amplify weights — simpler than threading
        weight params through every profile sub-function.
        """
        from musicmind.db.schema import user_calibration

        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(user_calibration).where(
                    user_calibration.c.user_id == user_id
                )
            )
            entries = result.fetchall()

        if not entries:
            return songs

        # Build lookup maps
        artist_weights: dict[str, float] = {}
        song_weights: dict[str, float] = {}

        for entry in entries:
            if entry.calibration_type == "top_artist":
                artist_weights[entry.item_id.lower()] = entry.weight
            elif entry.calibration_type == "artist_rank":
                artist_weights[entry.item_id.lower()] = entry.weight
            elif entry.calibration_type == "playlist_song":
                song_weights[entry.item_id] = entry.weight

        calibrated: list[dict] = []
        for song in songs:
            artist_name = (song.get("artist_name") or "").lower()
            catalog_id = song.get("catalog_id", "")

            # Determine multiplier
            multiplier = 1.0
            if artist_name in artist_weights:
                multiplier = max(multiplier, artist_weights[artist_name])
            if catalog_id in song_weights:
                multiplier = max(multiplier, song_weights[catalog_id])

            # Duplicate songs based on integer part of multiplier
            copies = max(1, int(multiplier))
            for _ in range(copies):
                calibrated.append(song)

        return calibrated

    async def _compute_and_save_profile(
        self,
        engine,
        *,
        user_id: str,
        service: str,
        songs: list[dict],
        history: list[dict],
        settings: Any = None,
    ) -> dict[str, Any]:
        """Compute taste profile and save snapshot to database.

        Uses build_taste_profile with temporal decay enabled (D-12).
        Audio enrichment is NOT run here — it runs separately via
        _background_sync_library (on /me) or _background_initial_sync
        (on service connection) to avoid OOM in constrained containers.
        """
        # Load any previously enriched audio features for centroid computation
        audio_features_map: dict[str, dict] = {}
        embedding_map: dict[str, list[float]] = {}
        clap_map: dict[str, list[float]] = {}
        mert_map: dict[str, list[float]] = {}
        try:
            from musicmind.db.schema import audio_features_cache

            catalog_ids = [
                s.get("catalog_id", "") for s in songs if s.get("catalog_id")
            ]
            if catalog_ids:
                async with engine.begin() as conn:
                    result = await conn.execute(
                        sa.select(audio_features_cache).where(
                            sa.and_(
                                audio_features_cache.c.catalog_id.in_(catalog_ids),
                                audio_features_cache.c.user_id == user_id,
                            )
                        )
                    )
                    for row in result:
                        features: dict[str, Any] = {}
                        for field in (
                            "tempo", "energy", "brightness", "danceability",
                            "acousticness", "valence_proxy", "beat_strength",
                        ):
                            val = getattr(row, field, None)
                            if val is not None:
                                features[field] = val
                        if features:
                            audio_features_map[row.catalog_id] = features

                async with engine.begin() as conn:
                    emb_result = await conn.execute(
                        sa.select(audio_embeddings).where(
                            sa.and_(
                                audio_embeddings.c.catalog_id.in_(catalog_ids),
                                audio_embeddings.c.user_id == user_id,
                            )
                        )
                    )
                    for row in emb_result:
                        cid = row.catalog_id
                        emb = row.embedding
                        if isinstance(emb, str):
                            emb = json.loads(emb)
                        if emb and isinstance(emb, list) and len(emb) >= 128:
                            embedding_map[cid] = emb
                        clap = row.clap_embedding
                        if isinstance(clap, str):
                            clap = json.loads(clap)
                        if clap and isinstance(clap, list) and len(clap) > 0:
                            clap_map[cid] = clap
                        mert = row.mert_embedding
                        if isinstance(mert, str):
                            mert = json.loads(mert)
                        if mert and isinstance(mert, list) and len(mert) > 0:
                            mert_map[cid] = mert
        except Exception:
            logger.warning(
                "Failed to load audio features/embeddings for centroid, "
                "continuing without",
            )

        # Apply engagement weights (play count proxy) before profile building
        engaged_songs = await self._apply_engagement_weights(
            engine, user_id=user_id, songs=songs,
        )

        # Apply calibration weights on top of engagement weights
        calibrated_songs = await self._apply_calibration_weights(
            engine, user_id=user_id, songs=engaged_songs,
        )

        # V 6.388: load mood_tags from global_song_cache (per catalog_id
        # AND per ISRC for library songs that haven't been promoted).
        mood_tags_map: dict[str, list[str]] = {}
        try:
            from musicmind.db.schema import global_song_cache
            isrcs = [s.get("isrc") for s in songs if s.get("isrc")]
            if catalog_ids or isrcs:
                async with engine.begin() as conn:
                    by_cid = await conn.execute(
                        sa.select(
                            global_song_cache.c.catalog_id,
                            global_song_cache.c.isrc,
                            global_song_cache.c.mood_tags,
                        ).where(
                            sa.or_(
                                global_song_cache.c.catalog_id.in_(catalog_ids)
                                if catalog_ids else sa.false(),
                                global_song_cache.c.isrc.in_(isrcs)
                                if isrcs else sa.false(),
                            )
                        )
                    )
                    by_isrc: dict[str, list[str]] = {}
                    for row in by_cid:
                        tags = row.mood_tags
                        if isinstance(tags, str):
                            try:
                                tags = json.loads(tags)
                            except (ValueError, TypeError):
                                tags = []
                        if not tags or not isinstance(tags, list):
                            continue
                        mood_tags_map[row.catalog_id] = tags
                        if row.isrc:
                            by_isrc[row.isrc] = tags
                    # ISRC fallback for library rows not keyed by catalog_id
                    for s in songs:
                        cid = s.get("catalog_id", "")
                        if cid in mood_tags_map:
                            continue
                        isrc = s.get("isrc")
                        if isrc and isrc in by_isrc:
                            mood_tags_map[cid] = by_isrc[isrc]
        except Exception:
            logger.warning(
                "Failed to load mood_tags for profile, continuing without",
            )

        profile = build_taste_profile(
            calibrated_songs, history,
            use_temporal_decay=True,
            audio_features_map=audio_features_map or None,
            embedding_map=embedding_map or None,
            clap_map=clap_map or None,
            mert_map=mert_map or None,
            mood_tags_map=mood_tags_map or None,
        )

        # total_songs_analyzed should reflect unique songs, not amplified duplicates
        profile["total_songs_analyzed"] = len(songs)

        now = datetime.now(UTC)
        # Save snapshot -- strip tzinfo for SQLite compat
        now_naive = now.replace(tzinfo=None)

        async with engine.begin() as conn:
            await conn.execute(
                taste_profile_snapshots.insert().values(
                    user_id=user_id,
                    service_source=service,
                    computed_at=now_naive,
                    genre_vector=json.dumps(profile["genre_vector"]),
                    top_artists=json.dumps(profile["top_artists"]),
                    audio_trait_preferences=json.dumps(
                        profile["audio_trait_preferences"]
                    ),
                    release_year_distribution=json.dumps(
                        profile["release_year_distribution"]
                    ),
                    familiarity_score=profile["familiarity_score"],
                    total_songs_analyzed=profile["total_songs_analyzed"],
                    listening_hours_estimated=profile["listening_hours_estimated"],
                    audio_centroid=json.dumps(profile.get("audio_centroid", {})),
                    embedding_centroid=json.dumps(
                        profile.get("embedding_centroid")
                    ) if profile.get("embedding_centroid") else None,
                    clap_centroid=json.dumps(
                        profile.get("clap_centroid")
                    ) if profile.get("clap_centroid") else None,
                    mert_centroid=json.dumps(
                        profile.get("mert_centroid")
                    ) if profile.get("mert_centroid") else None,
                    mood_distribution=json.dumps(
                        profile.get("mood_distribution")
                    ) if profile.get("mood_distribution") else None,
                )
            )

        return {
            "service": service,
            "computed_at": now.isoformat(),
            **profile,
        }
