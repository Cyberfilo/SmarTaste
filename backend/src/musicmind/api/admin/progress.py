"""Enrichment progress tracking for admin dashboard.

Provides per-user enrichment status with library vs worker breakdown.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import (
    audio_features_cache,
    song_metadata_cache,
    users,
)

logger = logging.getLogger(__name__)


async def get_enrichment_progress(engine) -> list[dict[str, Any]]:
    """Get enrichment progress for all users.

    Returns per-user stats with library songs vs total cached songs breakdown:
    - library_songs: songs from the user's actual library import
    - total_songs: all cached songs (library + worker-discovered discographies)
    - enriched_songs: songs with audio features
    """
    async with engine.begin() as conn:
        user_result = await conn.execute(
            sa.select(users.c.id, users.c.email, users.c.display_name)
        )
        all_users = user_result.fetchall()

        progress: list[dict[str, Any]] = []
        for user in all_users:
            # Total cached songs (library + worker-discovered)
            total_q = await conn.execute(
                sa.select(sa.func.count()).select_from(song_metadata_cache).where(
                    song_metadata_cache.c.user_id == user.id
                )
            )
            total_songs = total_q.scalar() or 0

            # Library songs only (have date_added_to_library or from library source)
            library_q = await conn.execute(
                sa.select(sa.func.count()).select_from(song_metadata_cache).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == user.id,
                        sa.or_(
                            song_metadata_cache.c.date_added_to_library.isnot(None),
                            song_metadata_cache.c.library_id.isnot(None),
                        ),
                    )
                )
            )
            library_songs = library_q.scalar() or 0

            # Enriched = songs with real audio data (energy != null)
            enriched_q = await conn.execute(
                sa.select(sa.func.count()).select_from(audio_features_cache).where(
                    sa.and_(
                        audio_features_cache.c.user_id == user.id,
                        audio_features_cache.c.energy.isnot(None),
                    )
                )
            )
            enriched = enriched_q.scalar() or 0

            # Unique artists in library
            artists_q = await conn.execute(
                sa.select(
                    sa.func.count(sa.distinct(song_metadata_cache.c.artist_name))
                ).where(song_metadata_cache.c.user_id == user.id)
            )
            unique_artists = artists_q.scalar() or 0

            if total_songs == 0:
                continue

            worker_songs = total_songs - library_songs

            progress.append({
                "user_id": user.id,
                "email": user.email,
                "display_name": user.display_name or user.email,
                "library_songs": library_songs,
                "worker_songs": worker_songs,
                "total_songs": total_songs,
                "enriched_songs": enriched,
                "unique_artists": unique_artists,
                "percentage": round(
                    enriched / total_songs * 100, 1
                ) if total_songs > 0 else 0,
            })

    return progress
