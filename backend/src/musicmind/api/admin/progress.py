"""Enrichment progress tracking for admin dashboard.

Provides per-user enrichment status from the database.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import audio_features_cache, song_metadata_cache, users

logger = logging.getLogger(__name__)


async def get_enrichment_progress(engine) -> list[dict[str, Any]]:
    """Get enrichment progress for all users.

    Returns per-user stats: total songs, enriched songs, percentage.
    """
    async with engine.begin() as conn:
        # Get all users with their song counts
        user_result = await conn.execute(
            sa.select(users.c.id, users.c.email, users.c.display_name)
        )
        all_users = user_result.fetchall()

        progress: list[dict[str, Any]] = []
        for user in all_users:
            # Count songs
            songs_q = await conn.execute(
                sa.select(sa.func.count()).select_from(song_metadata_cache).where(
                    song_metadata_cache.c.user_id == user.id
                )
            )
            total_songs = songs_q.scalar() or 0

            # Count enriched
            enriched_q = await conn.execute(
                sa.select(sa.func.count()).select_from(audio_features_cache).where(
                    audio_features_cache.c.user_id == user.id
                )
            )
            enriched = enriched_q.scalar() or 0

            if total_songs == 0:
                continue

            progress.append({
                "user_id": user.id,
                "email": user.email,
                "display_name": user.display_name or user.email,
                "total_songs": total_songs,
                "enriched_songs": enriched,
                "percentage": round(enriched / total_songs * 100, 1) if total_songs > 0 else 0,
            })

    return progress
