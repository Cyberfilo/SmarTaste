"""Taste profile pipeline: staleness check, fetch, cache, compute, return."""

from __future__ import annotations

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
    listening_history,
    service_connections,
    song_metadata_cache,
    taste_profile_snapshots,
)
from musicmind.engine.profile import build_taste_profile
from musicmind.security.encryption import EncryptionService

logger = logging.getLogger(__name__)

STALENESS_HOURS = 24  # Per D-06


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
        """Get the user's taste profile, using cache when fresh.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for token decryption.
            settings: Application settings.
            user_id: MusicMind user ID.
            service: Target service (spotify or apple_music). Auto-detected if None.
            force_refresh: Bypass staleness check and re-fetch.

        Returns:
            Taste profile dict with genre_vector, top_artists, etc.

        Raises:
            ValueError: If no connected service is found for the user.
        """
        resolved_service = await self._resolve_service(
            engine, user_id=user_id, service=service
        )

        if not force_refresh:
            snapshot = await self._get_fresh_snapshot(
                engine, user_id=user_id, service_source=resolved_service
            )
            if snapshot is not None:
                logger.info(
                    "Returning cached profile for user %s service %s",
                    user_id,
                    resolved_service,
                )
                return snapshot

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
        )
        return profile

    async def _resolve_service(
        self, engine, *, user_id: str, service: str | None
    ) -> str:
        """Resolve which service to use for the taste profile.

        If service is explicitly provided, return it directly.
        Otherwise query connected services and pick the first one.
        """
        if service is not None:
            return service

        connections = await get_user_connections(engine, user_id=user_id)
        if not connections:
            raise ValueError("No connected service found")

        return connections[0]["service"]

    async def _get_fresh_snapshot(
        self, engine, *, user_id: str, service_source: str
    ) -> dict[str, Any] | None:
        """Return a cached profile snapshot if fresh (< 24h old).

        Returns None if no snapshot exists or the latest is stale.
        SQLite stores timezone-naive datetimes, so we strip tzinfo
        from the cutoff for comparison compatibility.
        """
        cutoff = datetime.now(UTC) - timedelta(hours=STALENESS_HOURS)
        # SQLite compat: strip tzinfo for comparison with naive stored dates
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

        mapping = row._mapping

        def _parse_json(val: Any, default: Any) -> Any:
            """Parse JSON string from DB if needed (SQLite stores JSON as TEXT)."""
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
            "genre_vector": _parse_json(mapping["genre_vector"], {}),
            "top_artists": _parse_json(mapping["top_artists"], []),
            "audio_trait_preferences": _parse_json(
                mapping["audio_trait_preferences"], {}
            ),
            "release_year_distribution": _parse_json(
                mapping["release_year_distribution"], {}
            ),
            "familiarity_score": mapping["familiarity_score"] or 0.0,
            "total_songs_analyzed": mapping["total_songs_analyzed"] or 0,
            "listening_hours_estimated": mapping["listening_hours_estimated"] or 0.0,
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
        """Fetch data from Apple Music using developer token."""
        developer_token = generate_apple_developer_token(
            settings.apple_team_id,
            settings.apple_key_id,
            settings.apple_private_key_path,
        )
        music_user_token = access_token  # stored as access_token in service_connections

        library_songs = await fetch_apple_music_library(
            developer_token, music_user_token
        )
        recently_played = await fetch_apple_music_recently_played(
            developer_token, music_user_token
        )

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
        """Cache listening history entries."""
        async with engine.begin() as conn:
            for entry in history:
                song_id = entry.get("song_id", "")
                if not song_id:
                    continue

                await conn.execute(
                    listening_history.insert().values(
                        user_id=user_id,
                        song_id=song_id,
                        song_name=entry.get("song_name", ""),
                        artist_name=entry.get("artist_name", ""),
                        album_name=entry.get("album_name", ""),
                        genre_names=json.dumps(entry.get("genre_names", [])),
                        duration_ms=entry.get("duration_ms"),
                        service_source=service,
                    )
                )

    async def _compute_and_save_profile(
        self,
        engine,
        *,
        user_id: str,
        service: str,
        songs: list[dict],
        history: list[dict],
    ) -> dict[str, Any]:
        """Compute taste profile and save snapshot to database.

        Uses build_taste_profile with temporal decay enabled (D-12).
        """
        profile = build_taste_profile(songs, history, use_temporal_decay=True)

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
                )
            )

        return {
            "service": service,
            "computed_at": now.isoformat(),
            **profile,
        }
