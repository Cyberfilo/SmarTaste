"""Enrichment progress tracking for admin dashboard.

Provides per-user enrichment status with library vs worker breakdown.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import (
    audio_embeddings,
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


async def get_enrichment_breakdown(engine) -> dict[str, Any]:
    """Get pipeline-level enrichment breakdown across all songs.

    Enrichment pipeline stages:
    1. Audio features (audio_features_cache with energy IS NOT NULL)
    2. Last.fm tags (lastfm_tags_cache with entity_type='track')
    3. MusicBrainz credits (kg_relationships with source_mbid LIKE 'isrc:%')
    4. Lyrics embeddings (audio_embeddings with model_version='lyrics-minilm-v2')

    Returns counts for: unenriched, partially enriched (audio only), fully enriched (all 4).
    """
    async with engine.begin() as conn:
        # Total songs across all users
        total_q = await conn.execute(
            sa.select(sa.func.count()).select_from(song_metadata_cache)
        )
        total_songs = total_q.scalar() or 0

        if total_songs == 0:
            return {"total": 0, "unenriched": 0, "partial": 0, "fully_enriched": 0}

        # Songs with audio features
        audio_q = await conn.execute(
            sa.select(sa.func.count(sa.distinct(audio_features_cache.c.catalog_id))).where(
                audio_features_cache.c.energy.isnot(None)
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

        # Songs with lyrics embeddings
        lyrics_q = await conn.execute(
            sa.select(
                sa.func.count(sa.distinct(audio_embeddings.c.catalog_id))
            ).where(audio_embeddings.c.model_version == "lyrics-minilm-v2")
        )
        has_lyrics = lyrics_q.scalar() or 0

        unenriched = total_songs - has_audio
        partial = has_audio  # will subtract fully enriched below

        # Fully enriched = has all 4 stages. Use min() as rough upper bound,
        # since exact intersection query across 4 tables would be expensive.
        # The true full count is bounded by the smallest stage count.
        fully_enriched = min(has_audio, has_tags, has_credits, has_lyrics)
        partial = has_audio - fully_enriched

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
            "lyrics_embeddings": has_lyrics,
        },
    }
