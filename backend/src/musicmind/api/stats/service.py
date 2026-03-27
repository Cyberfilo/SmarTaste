"""Listening stats service: fetch top tracks, artists, genres by time period."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from musicmind.api.services.service import (
    generate_apple_developer_token,
    get_user_connections,
    refresh_spotify_token,
    upsert_service_connection,
)
from musicmind.api.stats.fetch import (
    compute_apple_music_top_artists,
    compute_apple_music_top_tracks,
    fetch_spotify_top_artists_for_period,
    fetch_spotify_top_tracks_for_period,
)
from musicmind.api.taste.fetch import fetch_apple_music_library
from musicmind.db.schema import service_connections
from musicmind.security.encryption import EncryptionService

logger = logging.getLogger(__name__)


class StatsService:
    """Orchestrates listening stats: fetch top tracks, artists, genres.

    For Spotify, uses the native /me/top/* endpoints with time_range.
    For Apple Music, fetches library data and computes rankings locally.
    No caching -- stats are computed on-demand since Spotify handles
    period filtering natively and Apple Music library is lightweight.
    """

    async def get_top_tracks(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str | None = None,
        period: str = "month",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get top tracks for a user by time period.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for token decryption.
            settings: Application settings.
            user_id: MusicMind user ID.
            service: Target service (spotify or apple_music). Auto-detected
                if None.
            period: Time period -- "month", "6months", or "alltime".
            limit: Maximum items to return (default 20, max 50).

        Returns:
            Dict with service, period, items (list of track dicts), total.

        Raises:
            ValueError: If no connected service is found.
        """
        resolved_service = await self._resolve_service(
            engine, user_id=user_id, service=service
        )
        access_token = await self._get_access_token(
            engine,
            encryption,
            settings,
            user_id=user_id,
            service=resolved_service,
        )

        if resolved_service == "spotify":
            items = await fetch_spotify_top_tracks_for_period(
                access_token, period=period, limit=limit
            )
        elif resolved_service == "apple_music":
            developer_token = generate_apple_developer_token(
                settings.apple_team_id,
                settings.apple_key_id,
                settings.apple_private_key_path,
            )
            songs = await fetch_apple_music_library(
                developer_token, access_token
            )
            items = compute_apple_music_top_tracks(
                songs, period=period
            )
            items = items[:limit]
        else:
            raise ValueError(f"Unsupported service: {resolved_service}")

        logger.info(
            "Returning %d top tracks for user %s service %s period %s",
            len(items),
            user_id,
            resolved_service,
            period,
        )
        return {
            "service": resolved_service,
            "period": period,
            "items": items,
            "total": len(items),
        }

    async def get_top_artists(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str | None = None,
        period: str = "month",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get top artists for a user by time period.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for token decryption.
            settings: Application settings.
            user_id: MusicMind user ID.
            service: Target service (spotify or apple_music). Auto-detected
                if None.
            period: Time period -- "month", "6months", or "alltime".
            limit: Maximum items to return (default 20, max 50).

        Returns:
            Dict with service, period, items (list of artist dicts), total.

        Raises:
            ValueError: If no connected service is found.
        """
        resolved_service = await self._resolve_service(
            engine, user_id=user_id, service=service
        )
        access_token = await self._get_access_token(
            engine,
            encryption,
            settings,
            user_id=user_id,
            service=resolved_service,
        )

        if resolved_service == "spotify":
            items = await fetch_spotify_top_artists_for_period(
                access_token, period=period, limit=limit
            )
        elif resolved_service == "apple_music":
            developer_token = generate_apple_developer_token(
                settings.apple_team_id,
                settings.apple_key_id,
                settings.apple_private_key_path,
            )
            songs = await fetch_apple_music_library(
                developer_token, access_token
            )
            items = compute_apple_music_top_artists(
                songs, period=period
            )
            items = items[:limit]
        else:
            raise ValueError(f"Unsupported service: {resolved_service}")

        logger.info(
            "Returning %d top artists for user %s service %s period %s",
            len(items),
            user_id,
            resolved_service,
            period,
        )
        return {
            "service": resolved_service,
            "period": period,
            "items": items,
            "total": len(items),
        }

    async def get_top_genres(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str | None = None,
        period: str = "month",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get top genres for a user by time period.

        For Spotify: fetches top tracks and top artists, then derives genres
        by cross-referencing track artists with artist genre lists. Genres are
        ranked by track_count (how many top tracks belong to artists with that
        genre) descending.

        For Apple Music: aggregates genre_names directly from filtered library
        tracks.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for token decryption.
            settings: Application settings.
            user_id: MusicMind user ID.
            service: Target service (spotify or apple_music). Auto-detected
                if None.
            period: Time period -- "month", "6months", or "alltime".
            limit: Maximum items to return (default 20, max 50).

        Returns:
            Dict with service, period, items (list of genre dicts with rank,
            genre, track_count, artist_count), total.

        Raises:
            ValueError: If no connected service is found.
        """
        resolved_service = await self._resolve_service(
            engine, user_id=user_id, service=service
        )
        access_token = await self._get_access_token(
            engine,
            encryption,
            settings,
            user_id=user_id,
            service=resolved_service,
        )

        if resolved_service == "spotify":
            items = await self._compute_spotify_genres(
                access_token, period=period, limit=limit
            )
        elif resolved_service == "apple_music":
            developer_token = generate_apple_developer_token(
                settings.apple_team_id,
                settings.apple_key_id,
                settings.apple_private_key_path,
            )
            songs = await fetch_apple_music_library(
                developer_token, access_token
            )
            items = self._compute_apple_music_genres(
                songs, period=period, limit=limit
            )
        else:
            raise ValueError(f"Unsupported service: {resolved_service}")

        logger.info(
            "Returning %d top genres for user %s service %s period %s",
            len(items),
            user_id,
            resolved_service,
            period,
        )
        return {
            "service": resolved_service,
            "period": period,
            "items": items,
            "total": len(items),
        }

    # -- Private Helpers ------------------------------------------------------

    async def _resolve_service(
        self,
        engine,
        *,
        user_id: str,
        service: str | None,
    ) -> str:
        """Resolve which service to use for stats.

        If service is explicitly provided, return it directly.
        Otherwise query connected services and pick the first one.

        Args:
            engine: SQLAlchemy async engine.
            user_id: MusicMind user ID.
            service: Explicit service name or None for auto-detection.

        Returns:
            Resolved service name (spotify or apple_music).

        Raises:
            ValueError: If no connected service found.
        """
        if service is not None:
            return service

        connections = await get_user_connections(engine, user_id=user_id)
        if not connections:
            raise ValueError("No connected service found")

        return connections[0]["service"]

    async def _get_access_token(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
    ) -> str:
        """Get a valid access token for the given service, refreshing if needed.

        For Spotify: checks token_expires_at and refreshes if within 60s of
        expiry. For Apple Music: returns the stored music_user_token as-is.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for token decryption.
            settings: Application settings (for Spotify client_id).
            user_id: MusicMind user ID.
            service: Service name (spotify or apple_music).

        Returns:
            Plaintext access token ready for API calls.

        Raises:
            ValueError: If no connection found for this user+service.
        """
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
            raise ValueError(
                f"No {service} connection found for user {user_id}"
            )

        access_token = encryption.decrypt(row.access_token_encrypted)

        if service == "spotify":
            token_expires_at = row.token_expires_at
            now = datetime.now(UTC)
            if token_expires_at is not None:
                if token_expires_at.tzinfo is None:
                    token_expires_at = token_expires_at.replace(tzinfo=UTC)
                if token_expires_at < now + timedelta(seconds=60):
                    logger.info(
                        "Spotify token expired or expiring soon, refreshing"
                    )
                    refresh_token_encrypted = row.refresh_token_encrypted
                    if refresh_token_encrypted:
                        refresh_token_value = encryption.decrypt(
                            refresh_token_encrypted
                        )
                        token_data = await refresh_spotify_token(
                            refresh_token_value,
                            settings.spotify_client_id,
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
                                    "refresh_token",
                                    refresh_token_value,
                                ),
                                expires_in=token_data.get("expires_in"),
                                service_user_id=row.service_user_id,
                            )

        return access_token

    async def _compute_spotify_genres(
        self,
        access_token: str,
        *,
        period: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Derive top genres from Spotify top tracks and artists.

        Builds an artist_name -> genres mapping from top artists. For each
        top track, looks up its artist's genres. Tallies genre -> track_count
        and genre -> set of artist_names for artist_count.

        Args:
            access_token: Valid Spotify access token.
            period: Time period for the Spotify API.
            limit: Maximum genres to return.

        Returns:
            List of genre dicts with rank, genre, track_count, artist_count.
        """
        tracks = await fetch_spotify_top_tracks_for_period(
            access_token, period=period, limit=50
        )
        artists = await fetch_spotify_top_artists_for_period(
            access_token, period=period, limit=50
        )

        # Build artist_name -> genres mapping
        artist_genres: dict[str, list[str]] = {}
        for artist in artists:
            name = artist.get("name", "")
            genres = artist.get("genres", [])
            if name and genres:
                artist_genres[name] = genres

        # Tally genres from tracks via artist mapping
        genre_track_count: Counter[str] = Counter()
        genre_artists: dict[str, set[str]] = {}

        for track in tracks:
            artist_name = track.get("artist_name", "")
            if not artist_name:
                continue
            genres = artist_genres.get(artist_name, [])
            for genre in genres:
                genre_track_count[genre] += 1
                if genre not in genre_artists:
                    genre_artists[genre] = set()
                genre_artists[genre].add(artist_name)

        # Sort by track_count descending
        sorted_genres = genre_track_count.most_common(limit)

        results: list[dict[str, Any]] = []
        for i, (genre, track_count) in enumerate(sorted_genres, start=1):
            results.append({
                "rank": i,
                "genre": genre,
                "track_count": track_count,
                "artist_count": len(genre_artists.get(genre, set())),
            })

        return results

    def _compute_apple_music_genres(
        self,
        songs: list[dict[str, Any]],
        *,
        period: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Compute top genres from Apple Music library data.

        Apple Music songs carry genre_names directly. Aggregates by genre:
        track_count (how many tracks have this genre) and artist_count
        (unique artists with this genre).

        Args:
            songs: Library song dicts with genre_names and artist_name.
            period: Time period for filtering.
            limit: Maximum genres to return.

        Returns:
            List of genre dicts with rank, genre, track_count, artist_count.
        """
        from musicmind.api.stats.fetch import _filter_songs_by_period

        filtered = _filter_songs_by_period(songs, period=period)

        genre_track_count: Counter[str] = Counter()
        genre_artists: dict[str, set[str]] = {}

        for song in filtered:
            artist_name = song.get("artist_name", "")
            genres = song.get("genre_names", [])
            if isinstance(genres, str):
                try:
                    genres = json.loads(genres)
                except (json.JSONDecodeError, TypeError):
                    genres = []
            for genre in genres:
                genre_track_count[genre] += 1
                if genre not in genre_artists:
                    genre_artists[genre] = set()
                if artist_name:
                    genre_artists[genre].add(artist_name)

        sorted_genres = genre_track_count.most_common(limit)

        results: list[dict[str, Any]] = []
        for i, (genre, track_count) in enumerate(sorted_genres, start=1):
            results.append({
                "rank": i,
                "genre": genre,
                "track_count": track_count,
                "artist_count": len(genre_artists.get(genre, set())),
            })

        return results
