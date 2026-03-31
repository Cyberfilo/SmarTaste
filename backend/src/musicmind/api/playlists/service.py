"""Playlist service — fetch real playlists from connected music services."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from musicmind.api.playlists.fetch import (
    fetch_apple_music_playlist_tracks,
    fetch_apple_music_playlists,
    fetch_spotify_playlist_tracks,
    fetch_spotify_playlists,
)
from musicmind.api.services.service import (
    generate_apple_developer_token,
    get_user_connections,
    refresh_spotify_token,
    upsert_service_connection,
)
from musicmind.db.schema import service_connections
from musicmind.security.encryption import EncryptionService

logger = logging.getLogger(__name__)


class PlaylistService:
    """Fetches real playlists from Spotify and Apple Music."""

    async def list_playlists(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str | None = None,
    ) -> list[dict[str, Any]]:
        """List the user's playlists from connected services.

        Fetches live from the service API (not cached).
        """
        connections = await get_user_connections(engine, user_id=user_id)
        if not connections:
            raise ValueError("No connected service found")

        all_playlists: list[dict[str, Any]] = []

        for conn_data in connections:
            svc = conn_data["service"]
            if service and svc != service:
                continue

            try:
                playlists = await self._fetch_service_playlists(
                    engine, encryption, settings,
                    user_id=user_id, service=svc,
                )
                all_playlists.extend(playlists)
            except Exception:
                logger.warning("Failed to fetch playlists from %s", svc)

        return all_playlists

    async def get_playlist_tracks(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
        playlist_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get tracks from a specific service playlist."""
        access_token = await self._get_access_token(
            engine, encryption, settings,
            user_id=user_id, service=service,
        )

        if service == "spotify":
            return await fetch_spotify_playlist_tracks(
                access_token, playlist_id, limit=limit,
            )
        elif service == "apple_music":
            developer_token = generate_apple_developer_token(
                settings.apple_team_id,
                settings.apple_key_id,
                settings.apple_private_key_path,
                private_key_b64=settings.apple_private_key_b64,
            )
            return await fetch_apple_music_playlist_tracks(
                developer_token, access_token, playlist_id, limit=limit,
            )
        else:
            raise ValueError(f"Unsupported service: {service}")

    async def _fetch_service_playlists(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
    ) -> list[dict[str, Any]]:
        """Fetch playlists from a specific service."""
        access_token = await self._get_access_token(
            engine, encryption, settings,
            user_id=user_id, service=service,
        )

        if service == "spotify":
            return await fetch_spotify_playlists(access_token)
        elif service == "apple_music":
            developer_token = generate_apple_developer_token(
                settings.apple_team_id,
                settings.apple_key_id,
                settings.apple_private_key_path,
                private_key_b64=settings.apple_private_key_b64,
            )
            return await fetch_apple_music_playlists(
                developer_token, access_token,
            )
        else:
            raise ValueError(f"Unsupported service: {service}")

    async def _get_access_token(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
    ) -> str:
        """Get valid access token, refreshing Spotify if needed."""
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
            raise ValueError(f"No {service} connection found")

        access_token = encryption.decrypt(row.access_token_encrypted)

        if service == "spotify":
            token_expires_at = row.token_expires_at
            now = datetime.now(UTC)
            if token_expires_at is not None:
                if token_expires_at.tzinfo is None:
                    token_expires_at = token_expires_at.replace(tzinfo=UTC)
                if token_expires_at < now + timedelta(seconds=60):
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
                                engine, encryption,
                                user_id=user_id,
                                service="spotify",
                                access_token=access_token,
                                refresh_token=token_data.get(
                                    "refresh_token", refresh_token_value
                                ),
                                expires_in=token_data.get("expires_in"),
                                service_user_id=row.service_user_id,
                            )

        return access_token

    async def get_playlist_recommendations(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
        playlist_id: str,
        limit: int = 10,
    ) -> dict:
        """Get recommendations based on a specific playlist's songs.

        Builds a mini taste profile from the playlist's genres and artists,
        then uses the recommendation engine to score candidates.
        """
        from collections import Counter

        from musicmind.engine.profile import build_genre_vector, expand_genres
        from musicmind.engine.scorer import rank_candidates

        # Fetch playlist tracks
        tracks = await self.get_playlist_tracks(
            engine, encryption, settings,
            user_id=user_id, service=service, playlist_id=playlist_id,
        )

        if not tracks:
            return {"items": [], "total": 0}

        # Build mini genre vector from playlist songs
        genre_counter: Counter[str] = Counter()
        artist_counter: Counter[str] = Counter()
        for t in tracks:
            for g in t.get("genre_names", []):
                genre_counter[g] += 1
            for eg in expand_genres(t.get("genre_names", [])):
                genre_counter[eg] += 0.3
            artist_name = t.get("artist_name", "")
            if artist_name:
                artist_counter[artist_name] += 1

        total_genre = sum(genre_counter.values())
        genre_vector = (
            {g: c / total_genre for g, c in genre_counter.items()}
            if total_genre > 0 else {}
        )

        max_artist = max(artist_counter.values()) if artist_counter else 1
        top_artists = [
            {"name": name, "score": count / max_artist, "song_count": count}
            for name, count in artist_counter.most_common(20)
        ]

        playlist_profile = {
            "genre_vector": genre_vector,
            "top_artists": top_artists,
            "release_year_distribution": {},
            "familiarity_score": 0.5,
        }

        # Use the existing recommendation service to discover candidates
        from musicmind.api.recommendations.service import RecommendationService

        rec_svc = RecommendationService()
        try:
            full_result = await rec_svc.get_recommendations(
                engine, encryption, settings,
                user_id=user_id, limit=limit * 3,  # Fetch more to filter
            )
        except Exception:
            return {"items": [], "total": 0}

        # Re-score candidates against the playlist profile
        candidates = []
        for item in full_result.get("items", []):
            # Skip songs already in the playlist
            if item.get("catalog_id") in {t.get("catalog_id") for t in tracks}:
                continue
            candidates.append(item)

        if not candidates:
            return {"items": [], "total": 0}

        ranked = rank_candidates(candidates, playlist_profile, count=limit)

        items = []
        for r in ranked:
            items.append({
                "catalog_id": r.get("catalog_id", ""),
                "name": r.get("name", ""),
                "artist_name": r.get("artist_name", ""),
                "album_name": r.get("album_name", ""),
                "artwork_url": r.get("artwork_url", ""),
                "score": r.get("_score", 0.0),
                "explanation": r.get("_explanation", ""),
                "genre_names": r.get("genre_names", []),
            })

        return {"items": items, "total": len(items)}
