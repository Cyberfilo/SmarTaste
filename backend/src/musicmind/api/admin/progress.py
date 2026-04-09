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
    artist_cobweb,
    audio_features_cache,
    kg_relationships,
    lastfm_tags_cache,
    song_metadata_cache,
    user_indexing_status,
    users,
)

logger = logging.getLogger(__name__)


async def get_enrichment_progress(engine) -> list[dict[str, Any]]:
    """Get enrichment progress for all users.

    Uses batched queries (GROUP BY) instead of per-user loops to minimize
    database round-trips. For N users this runs ~5 queries total, not 8N.

    Returns per-user stats with library songs vs total cached songs breakdown.
    """
    async with engine.begin() as conn:
        # ── Batch 1: Song counts per user (total + library) ─────────
        song_stats = await conn.execute(
            sa.select(
                song_metadata_cache.c.user_id,
                sa.func.count().label("total_songs"),
                sa.func.count().filter(sa.or_(
                    song_metadata_cache.c.date_added_to_library.isnot(None),
                    song_metadata_cache.c.library_id.isnot(None),
                )).label("library_songs"),
                sa.func.count(sa.distinct(song_metadata_cache.c.artist_name)).label(
                    "unique_artists"
                ),
                sa.func.count(sa.distinct(
                    sa.case(
                        (sa.or_(
                            song_metadata_cache.c.library_id.isnot(None),
                            song_metadata_cache.c.date_added_to_library.isnot(None),
                        ), song_metadata_cache.c.artist_name),
                    )
                )).label("library_artists"),
            ).group_by(song_metadata_cache.c.user_id)
        )
        song_map: dict[str, dict] = {}
        for row in song_stats:
            song_map[row.user_id] = {
                "total_songs": row.total_songs,
                "library_songs": row.library_songs,
                "unique_artists": row.unique_artists,
                "library_artists": row.library_artists,
            }

        # ── Batch 2: Enriched counts per user ──────────────────────
        enriched_stats = await conn.execute(
            sa.select(
                audio_features_cache.c.user_id,
                sa.func.count().label("enriched"),
            ).where(
                sa.and_(
                    audio_features_cache.c.energy.isnot(None),
                    sa.tuple_(
                        audio_features_cache.c.catalog_id,
                        audio_features_cache.c.user_id,
                    ).in_(
                        sa.select(
                            song_metadata_cache.c.catalog_id,
                            song_metadata_cache.c.user_id,
                        )
                    ),
                )
            ).group_by(audio_features_cache.c.user_id)
        )
        enriched_map: dict[str, int] = {
            row.user_id: row.enriched for row in enriched_stats
        }

        # ── Batch 3: Cobweb stats per user ─────────────────────────
        cobweb_stats = await conn.execute(
            sa.select(
                artist_cobweb.c.user_id,
                sa.func.count().label("cobweb_total"),
                sa.func.count().filter(
                    artist_cobweb.c.enriched == sa.true()
                ).label("cobweb_enriched"),
            ).group_by(artist_cobweb.c.user_id)
        )
        cobweb_map: dict[str, dict] = {
            row.user_id: {
                "cobweb_total": row.cobweb_total,
                "cobweb_enriched": row.cobweb_enriched,
            }
            for row in cobweb_stats
        }

        # ── Batch 4: Indexing status per user ──────────────────────
        idx_stats = await conn.execute(
            sa.select(user_indexing_status).where(
                user_indexing_status.c.step != 99  # Exclude taste rebuild status
            )
        )
        idx_map: dict[str, dict] = {}
        for row in idx_stats:
            idx_map[row.user_id] = {
                "step": row.step,
                "step_name": row.step_name,
                "progress_current": row.progress_current,
                "progress_total": row.progress_total,
            }

        # ── Batch 5: Users ─────────────────────────────────────────
        user_result = await conn.execute(
            sa.select(users.c.id, users.c.email, users.c.display_name)
        )
        all_users = user_result.fetchall()

    # ── Assemble results ───────────────────────────────────────────
    progress: list[dict[str, Any]] = []
    for user in all_users:
        uid = user.id
        songs = song_map.get(uid, {})
        total_songs = songs.get("total_songs", 0)
        if total_songs == 0:
            continue

        library_songs = songs.get("library_songs", 0)
        unique_artists = songs.get("unique_artists", 0)
        library_artists = songs.get("library_artists", 0)
        enriched = enriched_map.get(uid, 0)
        cobweb = cobweb_map.get(uid, {"cobweb_total": 0, "cobweb_enriched": 0})

        progress.append({
            "user_id": uid,
            "email": user.email,
            "display_name": user.display_name or user.email,
            "library_songs": library_songs,
            "worker_songs": total_songs - library_songs,
            "total_songs": total_songs,
            "enriched_songs": enriched,
            "library_artists": library_artists,
            "discovered_artists": unique_artists - library_artists,
            "unique_artists": unique_artists,
            "orphan_features": 0,  # Computed on-demand via cleanup endpoint
            "indexing": idx_map.get(uid),
            "cobweb_total": cobweb["cobweb_total"],
            "cobweb_enriched": cobweb["cobweb_enriched"],
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
