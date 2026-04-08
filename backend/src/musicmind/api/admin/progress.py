"""Enrichment progress tracking for admin dashboard.

Provides per-user enrichment status with library vs worker breakdown,
library vs discovered artist counts, and pipeline-level enrichment breakdown.

IMPORTANT: enriched counts must only count audio_features_cache rows where the
catalog_id also exists in song_metadata_cache for that user. Otherwise orphaned
audio_features_cache rows (from deleted songs) inflate the count.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import (
    audio_features_cache,
    kg_relationships,
    lastfm_tags_cache,
    song_metadata_cache,
    users,
)

logger = logging.getLogger(__name__)


async def get_enrichment_progress(engine) -> list[dict[str, Any]]:
    """Get enrichment progress for all users.

    Returns per-user stats with library songs vs total cached songs breakdown:
    - library_songs: songs from the user's actual library import
    - total_songs: all cached songs (library + worker-discovered discographies)
    - enriched_songs: songs with audio features (only counting songs that still exist)
    - library_artists / discovered_artists: artist breakdown by source
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

            # Library songs only (have date_added_to_library or library_id)
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

            # Enriched = songs with audio features that STILL EXIST in song_metadata_cache
            # This prevents orphaned audio_features_cache rows from inflating the count
            user_song_ids = sa.select(song_metadata_cache.c.catalog_id).where(
                song_metadata_cache.c.user_id == user.id
            ).correlate(None)
            enriched_q = await conn.execute(
                sa.select(sa.func.count()).select_from(audio_features_cache).where(
                    sa.and_(
                        audio_features_cache.c.user_id == user.id,
                        audio_features_cache.c.energy.isnot(None),
                        audio_features_cache.c.catalog_id.in_(user_song_ids),
                    )
                )
            )
            enriched = enriched_q.scalar() or 0

            # Count orphaned audio_features_cache rows (for diagnostics)
            orphan_q = await conn.execute(
                sa.select(sa.func.count()).select_from(audio_features_cache).where(
                    sa.and_(
                        audio_features_cache.c.user_id == user.id,
                        audio_features_cache.c.catalog_id.notin_(user_song_ids),
                    )
                )
            )
            orphan_count = orphan_q.scalar() or 0

            # Library artists: distinct artists from library songs
            lib_artists_q = await conn.execute(
                sa.select(
                    sa.func.count(sa.distinct(song_metadata_cache.c.artist_name))
                ).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == user.id,
                        sa.or_(
                            song_metadata_cache.c.library_id.isnot(None),
                            song_metadata_cache.c.date_added_to_library.isnot(None),
                        ),
                    )
                )
            )
            library_artists = lib_artists_q.scalar() or 0

            # Total unique artists across all songs
            all_artists_q = await conn.execute(
                sa.select(
                    sa.func.count(sa.distinct(song_metadata_cache.c.artist_name))
                ).where(song_metadata_cache.c.user_id == user.id)
            )
            unique_artists = all_artists_q.scalar() or 0
            discovered_artists = unique_artists - library_artists

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
                "library_artists": library_artists,
                "discovered_artists": discovered_artists,
                "unique_artists": unique_artists,
                "orphan_features": orphan_count,
                "percentage": round(
                    min(enriched / total_songs, 1.0) * 100, 1
                ) if total_songs > 0 else 0,
            })

    return progress


async def get_enrichment_breakdown(engine) -> dict[str, Any]:
    """Get pipeline-level enrichment breakdown across all songs.

    Enrichment pipeline stages (3 active):
    1. Audio features (audio_features_cache with energy IS NOT NULL)
    2. Last.fm tags (lastfm_tags_cache with entity_type='track')
    3. MusicBrainz credits (kg_relationships with source_mbid LIKE 'isrc:%')

    Fully enriched = has all 3 stages complete.
    All counts restricted to songs that exist in song_metadata_cache.
    """
    async with engine.begin() as conn:
        # Total songs across all users
        total_q = await conn.execute(
            sa.select(sa.func.count()).select_from(song_metadata_cache)
        )
        total_songs = total_q.scalar() or 0

        if total_songs == 0:
            return {
                "total": 0, "unenriched": 0, "unenriched_pct": 0,
                "partial": 0, "partial_pct": 0,
                "fully_enriched": 0, "fully_enriched_pct": 0,
                "stages": {},
            }

        # All catalog_ids that exist in song_metadata_cache (de-duped)
        existing_songs = sa.select(
            sa.distinct(song_metadata_cache.c.catalog_id)
        ).correlate(None)

        # Songs with audio features (only those that still exist)
        audio_q = await conn.execute(
            sa.select(
                sa.func.count(sa.distinct(audio_features_cache.c.catalog_id))
            ).where(
                sa.and_(
                    audio_features_cache.c.energy.isnot(None),
                    audio_features_cache.c.catalog_id.in_(existing_songs),
                )
            )
        )
        has_audio = audio_q.scalar() or 0

        # Songs with Last.fm tags
        tags_q = await conn.execute(
            sa.select(sa.func.count()).select_from(lastfm_tags_cache).where(
                lastfm_tags_cache.c.entity_type == "track"
            )
        )
        has_tags = tags_q.scalar() or 0

        # Songs with MusicBrainz credits
        credits_q = await conn.execute(
            sa.select(
                sa.func.count(sa.distinct(kg_relationships.c.source_mbid))
            ).where(kg_relationships.c.source_mbid.like("isrc:%"))
        )
        has_credits = credits_q.scalar() or 0

        unenriched = max(0, total_songs - has_audio)

        # Fully enriched = all 3 stages complete (bounded by smallest count)
        fully_enriched = min(has_audio, has_tags, has_credits)
        partial = max(0, has_audio - fully_enriched)

    pct = lambda n: round(n / total_songs * 100, 1) if total_songs > 0 else 0  # noqa: E731

    return {
        "total": total_songs,
        "unenriched": unenriched,
        "unenriched_pct": pct(unenriched),
        "partial": partial,
        "partial_pct": pct(partial),
        "fully_enriched": fully_enriched,
        "fully_enriched_pct": pct(fully_enriched),
        "stages": {
            "audio_features": has_audio,
            "lastfm_tags": has_tags,
            "musicbrainz_credits": has_credits,
        },
    }


async def cleanup_orphaned_features(engine) -> dict[str, int]:
    """Delete audio_features_cache rows that have no matching song_metadata_cache entry.

    These orphans occur when songs are deleted from song_metadata_cache (e.g. the
    148K junk song cleanup) but their audio_features_cache rows are left behind.
    The audio data is already preserved in audio_features_global (by ISRC), so
    these per-user orphan rows are safe to delete.
    """
    async with engine.begin() as conn:
        # Find all user_ids with potential orphans
        user_q = await conn.execute(
            sa.select(sa.distinct(audio_features_cache.c.user_id))
        )
        user_ids = [row[0] for row in user_q]

    total_deleted = 0
    per_user: dict[str, int] = {}

    for user_id in user_ids:
        async with engine.begin() as conn:
            existing = sa.select(song_metadata_cache.c.catalog_id).where(
                song_metadata_cache.c.user_id == user_id
            ).correlate(None)

            result = await conn.execute(
                sa.delete(audio_features_cache).where(
                    sa.and_(
                        audio_features_cache.c.user_id == user_id,
                        audio_features_cache.c.catalog_id.notin_(existing),
                    )
                )
            )
            deleted = result.rowcount
            if deleted > 0:
                per_user[user_id[:8]] = deleted
                total_deleted += deleted
                logger.info(
                    "Cleaned %d orphaned audio_features_cache rows for user %s",
                    deleted, user_id[:8],
                )

    return {"total_deleted": total_deleted, "per_user": per_user}
