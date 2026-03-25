"""Reusable query builders for MusicMind database operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from musicmind.db.schema import (
    artist_cache,
    generated_playlists,
    listening_history,
    song_metadata_cache,
    taste_profile_snapshots,
)


class QueryExecutor:
    """Executes database queries against the async engine."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ── Listening History ─────────────────────────────────────────────

    async def insert_listening_history(self, records: list[dict[str, Any]]) -> int:
        """Insert listening history records. Returns count inserted."""
        if not records:
            return 0
        async with self._engine.begin() as conn:
            await conn.execute(listening_history.insert(), records)
        return len(records)

    async def get_listening_history(
        self, since: datetime | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Get listening history, optionally filtered by date."""
        stmt = sa.select(listening_history).order_by(
            listening_history.c.observed_at.desc()
        ).limit(limit)
        if since:
            stmt = stmt.where(listening_history.c.observed_at >= since)

        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            return [dict(row._mapping) for row in result]

    # ── Song Metadata Cache ───────────────────────────────────────────

    async def upsert_song_metadata(self, songs: list[dict[str, Any]]) -> int:
        """Upsert song metadata. Uses SQLite INSERT OR REPLACE."""
        if not songs:
            return 0
        # Ensure fetched_at is set
        for song in songs:
            if "fetched_at" not in song:
                song["fetched_at"] = datetime.now(tz=UTC)

        async with self._engine.begin() as conn:
            for song in songs:
                # Check if exists
                existing = await conn.execute(
                    sa.select(song_metadata_cache.c.catalog_id).where(
                        song_metadata_cache.c.catalog_id == song["catalog_id"]
                    )
                )
                if existing.fetchone():
                    await conn.execute(
                        song_metadata_cache.update()
                        .where(song_metadata_cache.c.catalog_id == song["catalog_id"])
                        .values(**song)
                    )
                else:
                    await conn.execute(song_metadata_cache.insert().values(**song))
        return len(songs)

    async def get_all_cached_songs(self) -> list[dict[str, Any]]:
        """Get all songs from the metadata cache."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(song_metadata_cache).order_by(song_metadata_cache.c.name)
            )
            return [dict(row._mapping) for row in result]

    async def get_cached_song(self, catalog_id: str) -> dict[str, Any] | None:
        """Get a single cached song by catalog ID."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(song_metadata_cache).where(
                    song_metadata_cache.c.catalog_id == catalog_id
                )
            )
            row = result.fetchone()
            return dict(row._mapping) if row else None

    # ── Artist Cache ──────────────────────────────────────────────────

    async def upsert_artist(self, artists: list[dict[str, Any]]) -> int:
        """Upsert artist data."""
        if not artists:
            return 0
        for artist in artists:
            if "fetched_at" not in artist:
                artist["fetched_at"] = datetime.now(tz=UTC)

        async with self._engine.begin() as conn:
            for artist in artists:
                existing = await conn.execute(
                    sa.select(artist_cache.c.artist_id).where(
                        artist_cache.c.artist_id == artist["artist_id"]
                    )
                )
                if existing.fetchone():
                    await conn.execute(
                        artist_cache.update()
                        .where(artist_cache.c.artist_id == artist["artist_id"])
                        .values(**artist)
                    )
                else:
                    await conn.execute(artist_cache.insert().values(**artist))
        return len(artists)

    async def get_all_cached_artists(self) -> list[dict[str, Any]]:
        """Get all cached artists."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(artist_cache).order_by(artist_cache.c.name)
            )
            return [dict(row._mapping) for row in result]

    # ── Taste Profile Snapshots ───────────────────────────────────────

    async def save_taste_snapshot(self, snapshot: dict[str, Any]) -> int:
        """Save a taste profile snapshot. Returns the snapshot ID."""
        if "computed_at" not in snapshot:
            snapshot["computed_at"] = datetime.now(tz=UTC)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                taste_profile_snapshots.insert().values(**snapshot)
            )
            return result.inserted_primary_key[0]

    async def get_latest_taste_snapshot(self) -> dict[str, Any] | None:
        """Get the most recent taste profile snapshot."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(taste_profile_snapshots)
                .order_by(taste_profile_snapshots.c.computed_at.desc())
                .limit(1)
            )
            row = result.fetchone()
            return dict(row._mapping) if row else None

    # ── Generated Playlists ───────────────────────────────────────────

    async def save_generated_playlist(self, playlist: dict[str, Any]) -> int:
        """Save a generated playlist record. Returns the playlist ID."""
        if "created_at" not in playlist:
            playlist["created_at"] = datetime.now(tz=UTC)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                generated_playlists.insert().values(**playlist)
            )
            return result.inserted_primary_key[0]

    async def get_generated_playlists(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent generated playlists."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(generated_playlists)
                .order_by(generated_playlists.c.created_at.desc())
                .limit(limit)
            )
            return [dict(row._mapping) for row in result]

    # ── Stats ─────────────────────────────────────────────────────────

    async def get_cache_stats(self) -> dict[str, int]:
        """Get counts for all cached tables."""
        async with self._engine.connect() as conn:
            songs = (await conn.execute(
                sa.select(sa.func.count()).select_from(song_metadata_cache)
            )).scalar() or 0
            artists = (await conn.execute(
                sa.select(sa.func.count()).select_from(artist_cache)
            )).scalar() or 0
            history = (await conn.execute(
                sa.select(sa.func.count()).select_from(listening_history)
            )).scalar() or 0
            snapshots = (await conn.execute(
                sa.select(sa.func.count()).select_from(taste_profile_snapshots)
            )).scalar() or 0
            playlists = (await conn.execute(
                sa.select(sa.func.count()).select_from(generated_playlists)
            )).scalar() or 0

        return {
            "songs_cached": songs,
            "artists_cached": artists,
            "listening_history_entries": history,
            "taste_snapshots": snapshots,
            "generated_playlists": playlists,
        }
