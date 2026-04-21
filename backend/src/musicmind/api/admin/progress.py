"""Enrichment progress tracking for admin dashboard.

Provides per-user enrichment status with library vs worker breakdown,
library vs discovered artist counts, and pipeline-level enrichment breakdown.

Pipeline stages (5-stage Essentia-first pipeline):
1. Audio features — Essentia scalar features (energy, tempo, etc.)
2. EffNet embeddings — Essentia 1280-dim embeddings
3. GPU embeddings — CLAP 512-dim + MERT 768-dim
4. AI captions — OpenAI-generated track descriptions
5. Classifier labels — Essentia mood/genre/acousticness heads

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
    audio_embeddings,
    audio_features_cache,
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

        # ── Batch 5: Embedding counts per user ────────────────────
        embed_stats = await conn.execute(
            sa.select(
                audio_embeddings.c.user_id,
                sa.func.count().filter(
                    sa.and_(
                        audio_embeddings.c.embedding.isnot(None),
                        sa.cast(audio_embeddings.c.embedding, sa.Text) != "[]",
                    )
                ).label("effnet"),
                sa.func.count().filter(
                    audio_embeddings.c.clap_embedding.isnot(None)
                ).label("clap"),
                sa.func.count().filter(
                    audio_embeddings.c.mert_embedding.isnot(None)
                ).label("mert"),
            ).group_by(audio_embeddings.c.user_id)
        )
        embed_map: dict[str, dict] = {
            row.user_id: {
                "effnet": row.effnet,
                "clap": row.clap,
                "mert": row.mert,
            }
            for row in embed_stats
        }

        # ── Batch 6: AI caption + AI tag counts per user ──────────
        ai_stats = await conn.execute(
            sa.select(
                song_metadata_cache.c.user_id,
                sa.func.count().filter(
                    sa.and_(
                        song_metadata_cache.c.ai_caption.isnot(None),
                        song_metadata_cache.c.ai_caption != "",
                    )
                ).label("ai_captions"),
                sa.func.count().filter(
                    song_metadata_cache.c.ai_tags.isnot(None)
                ).label("ai_tags"),
            ).group_by(song_metadata_cache.c.user_id)
        )
        ai_map: dict[str, dict] = {
            row.user_id: {
                "ai_captions": row.ai_captions,
                "ai_tags": row.ai_tags,
            }
            for row in ai_stats
        }

        # ── Batch 7: Users ─────────────────────────────────────────
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
        embeds = embed_map.get(uid, {"effnet": 0, "clap": 0, "mert": 0})
        ai = ai_map.get(uid, {"ai_captions": 0, "ai_tags": 0})

        progress.append({
            "user_id": uid,
            "email": user.email,
            "display_name": user.display_name or user.email,
            "library_songs": library_songs,
            "worker_songs": total_songs - library_songs,
            "total_songs": total_songs,
            "enriched_songs": enriched,
            "effnet_embeddings": embeds["effnet"],
            "clap_embeddings": embeds["clap"],
            "mert_embeddings": embeds["mert"],
            "ai_captions": ai["ai_captions"],
            "ai_tags": ai["ai_tags"],
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

    Enrichment pipeline stages (5-stage Essentia-first pipeline):
    1. Audio features — Essentia scalar features (energy IS NOT NULL)
    2. EffNet embeddings — Essentia 1280-dim (embedding IS NOT NULL and not empty)
    3. GPU embeddings — CLAP + MERT (clap_embedding IS NOT NULL)
    4. AI captions — OpenAI-generated descriptions (ai_caption IS NOT NULL)
    5. Classifier labels — Essentia mood/genre heads (ai_tags IS NOT NULL)

    Fully enriched = has all 5 stages complete.
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

        # Stage 1: Songs with audio features (Essentia scalars)
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

        # Stage 2: Songs with EffNet embeddings (1280-dim, non-empty)
        effnet_q = await conn.execute(
            sa.select(
                sa.func.count(sa.distinct(audio_embeddings.c.catalog_id))
            ).where(
                sa.and_(
                    audio_embeddings.c.embedding.isnot(None),
                    sa.cast(audio_embeddings.c.embedding, sa.Text) != "[]",
                    audio_embeddings.c.catalog_id.in_(existing_songs),
                )
            )
        )
        has_effnet = effnet_q.scalar() or 0

        # Stage 3: Songs with GPU embeddings (CLAP + MERT)
        gpu_q = await conn.execute(
            sa.select(
                sa.func.count(sa.distinct(audio_embeddings.c.catalog_id))
            ).where(
                sa.and_(
                    audio_embeddings.c.clap_embedding.isnot(None),
                    audio_embeddings.c.catalog_id.in_(existing_songs),
                )
            )
        )
        has_gpu = gpu_q.scalar() or 0

        # Stage 4: Songs with AI captions (OpenAI)
        caption_q = await conn.execute(
            sa.select(sa.func.count()).where(
                sa.and_(
                    song_metadata_cache.c.ai_caption.isnot(None),
                    song_metadata_cache.c.ai_caption != "",
                )
            )
        )
        has_captions = caption_q.scalar() or 0

        # Stage 5: Songs with classifier labels (Essentia heads)
        labels_q = await conn.execute(
            sa.select(sa.func.count()).where(
                song_metadata_cache.c.ai_tags.isnot(None)
            )
        )
        has_labels = labels_q.scalar() or 0

        unenriched = max(0, total_songs - has_audio)

        # Fully enriched = all 5 stages complete (bounded by smallest count)
        fully_enriched = min(has_audio, has_effnet, has_gpu, has_captions, has_labels)
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
            "effnet_embeddings": has_effnet,
            "gpu_embeddings": has_gpu,
            "ai_captions": has_captions,
            "classifier_labels": has_labels,
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
