"""Playlist service — CRUD operations + AI-powered expand/improve."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import generated_playlists, playlist_items, song_metadata_cache

logger = logging.getLogger(__name__)


class PlaylistService:
    """Stateless service for playlist management."""

    async def list_playlists(
        self, engine, *, user_id: str
    ) -> list[dict[str, Any]]:
        """List all playlists for a user."""
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(generated_playlists)
                .where(generated_playlists.c.user_id == user_id)
                .order_by(generated_playlists.c.created_at.desc())
            )
            rows = result.fetchall()

        playlists = []
        for row in rows:
            playlists.append({
                "id": row.id,
                "name": row.name,
                "description": row.description or "",
                "ai_context": row.ai_context or "",
                "track_count": row.track_count or 0,
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            })
        return playlists

    async def get_playlist(
        self, engine, *, user_id: str, playlist_id: int
    ) -> dict[str, Any] | None:
        """Get a playlist with all its tracks."""
        async with engine.begin() as conn:
            # Get playlist
            result = await conn.execute(
                sa.select(generated_playlists).where(
                    sa.and_(
                        generated_playlists.c.id == playlist_id,
                        generated_playlists.c.user_id == user_id,
                    )
                )
            )
            row = result.first()
            if row is None:
                return None

            # Get items ordered by position
            items_result = await conn.execute(
                sa.select(playlist_items)
                .where(playlist_items.c.playlist_id == playlist_id)
                .order_by(playlist_items.c.position)
            )
            items = items_result.fetchall()

        playlist = {
            "id": row.id,
            "name": row.name,
            "description": row.description or "",
            "ai_context": row.ai_context or "",
            "track_count": row.track_count or 0,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "items": [],
        }

        for item in items:
            genres = item.genre_names
            if isinstance(genres, str):
                try:
                    genres = json.loads(genres)
                except (json.JSONDecodeError, TypeError):
                    genres = []
            playlist["items"].append({
                "id": item.id,
                "catalog_id": item.catalog_id,
                "position": item.position,
                "name": item.name or "",
                "artist_name": item.artist_name or "",
                "album_name": item.album_name or "",
                "artwork_url": item.artwork_url or "",
                "genre_names": genres or [],
                "added_by": item.added_by or "user",
            })

        return playlist

    async def create_playlist(
        self,
        engine,
        *,
        user_id: str,
        name: str,
        description: str = "",
        ai_context: str = "",
    ) -> dict[str, Any]:
        """Create a new empty playlist."""
        now = datetime.now(UTC)
        async with engine.begin() as conn:
            result = await conn.execute(
                generated_playlists.insert()
                .values(
                    user_id=user_id,
                    name=name,
                    description=description,
                    ai_context=ai_context,
                    vibe_prompt=ai_context,
                    track_count=0,
                    created_at=now,
                    updated_at=now,
                )
                .returning(generated_playlists.c.id)
            )
            row = result.first()
            playlist_id = row[0]

        logger.info("Created playlist %d for user %s", playlist_id, user_id)
        return {
            "id": playlist_id,
            "name": name,
            "description": description,
            "ai_context": ai_context,
            "track_count": 0,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

    async def update_playlist(
        self,
        engine,
        *,
        user_id: str,
        playlist_id: int,
        name: str | None = None,
        description: str | None = None,
        ai_context: str | None = None,
    ) -> bool:
        """Update playlist metadata. Returns True if found and updated."""
        values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if name is not None:
            values["name"] = name
        if description is not None:
            values["description"] = description
        if ai_context is not None:
            values["ai_context"] = ai_context
            values["vibe_prompt"] = ai_context

        async with engine.begin() as conn:
            result = await conn.execute(
                generated_playlists.update()
                .where(
                    sa.and_(
                        generated_playlists.c.id == playlist_id,
                        generated_playlists.c.user_id == user_id,
                    )
                )
                .values(**values)
            )
        return result.rowcount > 0

    async def delete_playlist(
        self, engine, *, user_id: str, playlist_id: int
    ) -> bool:
        """Delete a playlist and all its items. Returns True if found."""
        async with engine.begin() as conn:
            result = await conn.execute(
                generated_playlists.delete().where(
                    sa.and_(
                        generated_playlists.c.id == playlist_id,
                        generated_playlists.c.user_id == user_id,
                    )
                )
            )
        return result.rowcount > 0

    async def add_tracks(
        self,
        engine,
        *,
        user_id: str,
        playlist_id: int,
        catalog_ids: list[str],
        added_by: str = "user",
    ) -> int:
        """Add tracks to a playlist. Returns count of tracks added."""
        # Verify playlist belongs to user
        async with engine.begin() as conn:
            pl_result = await conn.execute(
                sa.select(generated_playlists.c.id).where(
                    sa.and_(
                        generated_playlists.c.id == playlist_id,
                        generated_playlists.c.user_id == user_id,
                    )
                )
            )
            if pl_result.first() is None:
                raise ValueError("Playlist not found")

            # Get current max position
            pos_result = await conn.execute(
                sa.select(sa.func.max(playlist_items.c.position)).where(
                    playlist_items.c.playlist_id == playlist_id
                )
            )
            max_pos = pos_result.scalar() or -1

            # Look up track metadata
            tracks_result = await conn.execute(
                sa.select(song_metadata_cache).where(
                    sa.and_(
                        song_metadata_cache.c.catalog_id.in_(catalog_ids),
                        song_metadata_cache.c.user_id == user_id,
                    )
                )
            )
            track_map = {}
            for row in tracks_result:
                genres = row.genre_names
                if isinstance(genres, str):
                    try:
                        genres = json.loads(genres)
                    except (json.JSONDecodeError, TypeError):
                        genres = []
                track_map[row.catalog_id] = {
                    "name": row.name or "",
                    "artist_name": row.artist_name or "",
                    "album_name": row.album_name or "",
                    "artwork_url": getattr(row, "artwork_url_template", "") or "",
                    "genre_names": genres,
                }

            # Insert items
            added = 0
            for cid in catalog_ids:
                max_pos += 1
                meta = track_map.get(cid, {})
                await conn.execute(
                    playlist_items.insert().values(
                        playlist_id=playlist_id,
                        catalog_id=cid,
                        position=max_pos,
                        name=meta.get("name", ""),
                        artist_name=meta.get("artist_name", ""),
                        album_name=meta.get("album_name", ""),
                        artwork_url=meta.get("artwork_url", ""),
                        genre_names=json.dumps(meta.get("genre_names", [])),
                        added_by=added_by,
                    )
                )
                added += 1

            # Update track count
            await conn.execute(
                generated_playlists.update()
                .where(generated_playlists.c.id == playlist_id)
                .values(
                    track_count=max_pos + 1,
                    updated_at=datetime.now(UTC),
                )
            )

        return added

    async def remove_track(
        self,
        engine,
        *,
        user_id: str,
        playlist_id: int,
        item_id: int,
    ) -> bool:
        """Remove a track from a playlist by item ID."""
        async with engine.begin() as conn:
            # Verify ownership
            pl_result = await conn.execute(
                sa.select(generated_playlists.c.id).where(
                    sa.and_(
                        generated_playlists.c.id == playlist_id,
                        generated_playlists.c.user_id == user_id,
                    )
                )
            )
            if pl_result.first() is None:
                return False

            result = await conn.execute(
                playlist_items.delete().where(
                    sa.and_(
                        playlist_items.c.id == item_id,
                        playlist_items.c.playlist_id == playlist_id,
                    )
                )
            )

            if result.rowcount > 0:
                # Update count
                count_result = await conn.execute(
                    sa.select(sa.func.count()).where(
                        playlist_items.c.playlist_id == playlist_id
                    )
                )
                new_count = count_result.scalar() or 0
                await conn.execute(
                    generated_playlists.update()
                    .where(generated_playlists.c.id == playlist_id)
                    .values(track_count=new_count, updated_at=datetime.now(UTC))
                )

        return result.rowcount > 0
