"""Smart enrichment diagnostics — per-user, per-stage, with failure analysis.

Provides deep insight into what's enriched, what's pending, what failed and why.
Cross-references main DB with logs DB for error correlation.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

logger = logging.getLogger(__name__)


async def get_enrichment_diagnostics(
    engine,
    logs_engine=None,
) -> dict[str, Any]:
    """Smart per-user enrichment diagnostics with failure breakdown.

    Returns for EACH user:
    - Per-stage counts: audio/tags/credits (library vs non-library)
    - Failed vs pending vs complete breakdown
    - Failure reasons from feature_source markers
    - Actionable insights (what the system should do next)
    """
    from musicmind.db.schema import (
        artist_cobweb,
        audio_embeddings,
        audio_features_cache,
        lastfm_tags_cache,
        song_metadata_cache,
        user_indexing_status,
        users,
    )

    result: dict[str, Any] = {"users": [], "insights": [], "totals": {}}

    async with engine.begin() as conn:
        # ── All users ──────────────────────────────────────────────
        user_rows = (await conn.execute(
            sa.select(users.c.id, users.c.email, users.c.display_name)
        )).fetchall()

        for user in user_rows:
            uid = user.id

            # ── Song counts by source ──────────────────────────────
            songs_q = await conn.execute(
                sa.select(
                    sa.func.count().label("total"),
                    sa.func.count().filter(sa.or_(
                        song_metadata_cache.c.library_id.isnot(None),
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                    )).label("library"),
                ).where(song_metadata_cache.c.user_id == uid)
            )
            songs = songs_q.first()
            total_songs = songs.total or 0
            library_songs = songs.library or 0
            if total_songs == 0:
                continue
            non_library = total_songs - library_songs

            # ── Audio features breakdown ───────────────────────────
            af_q = await conn.execute(
                sa.select(
                    sa.func.count().label("total_rows"),
                    sa.func.count().filter(
                        audio_features_cache.c.energy.isnot(None)
                    ).label("enriched"),
                    sa.func.count().filter(
                        sa.and_(
                            audio_features_cache.c.energy.is_(None),
                            sa.cast(
                                audio_features_cache.c.feature_source, sa.Text
                            ).like('%no_data_available%'),
                        )
                    ).label("permanently_failed"),
                    sa.func.count().filter(
                        sa.and_(
                            audio_features_cache.c.energy.is_(None),
                            sa.not_(sa.cast(
                                audio_features_cache.c.feature_source, sa.Text
                            ).like('%no_data_available%')),
                        )
                    ).label("partial_features"),
                ).where(audio_features_cache.c.user_id == uid)
            )
            af = af_q.first()
            audio_enriched = af.enriched or 0
            audio_failed = af.permanently_failed or 0
            audio_partial = af.partial_features or 0
            audio_not_attempted = total_songs - (af.total_rows or 0)

            # ── Library vs non-library audio breakdown ─────────────
            lib_enriched_q = await conn.execute(
                sa.select(sa.func.count()).where(
                    sa.and_(
                        audio_features_cache.c.user_id == uid,
                        audio_features_cache.c.energy.isnot(None),
                        audio_features_cache.c.catalog_id.in_(
                            sa.select(song_metadata_cache.c.catalog_id).where(
                                sa.and_(
                                    song_metadata_cache.c.user_id == uid,
                                    sa.or_(
                                        song_metadata_cache.c.library_id.isnot(None),
                                        song_metadata_cache.c.date_added_to_library.isnot(
                                            None
                                        ),
                                    ),
                                )
                            )
                        ),
                    )
                )
            )
            lib_audio_enriched = lib_enriched_q.scalar() or 0

            # Library songs that permanently failed (Deezer couldn't find)
            lib_song_ids = sa.select(song_metadata_cache.c.catalog_id).where(
                sa.and_(
                    song_metadata_cache.c.user_id == uid,
                    sa.or_(
                        song_metadata_cache.c.library_id.isnot(None),
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                    ),
                )
            )
            lib_failed_q = await conn.execute(
                sa.select(sa.func.count()).where(
                    sa.and_(
                        audio_features_cache.c.user_id == uid,
                        audio_features_cache.c.energy.is_(None),
                        sa.cast(
                            audio_features_cache.c.feature_source, sa.Text
                        ).like('%no_data_available%'),
                        audio_features_cache.c.catalog_id.in_(lib_song_ids),
                    )
                )
            )
            lib_audio_failed = lib_failed_q.scalar() or 0

            # ── Last.fm tags count for this user's songs ───────────
            user_eids_q = await conn.execute(
                sa.select(
                    sa.func.count(sa.distinct(
                        sa.func.concat(
                            sa.literal("track:"),
                            sa.func.lower(song_metadata_cache.c.artist_name),
                            sa.literal(":"),
                            sa.func.lower(song_metadata_cache.c.name),
                        )
                    ))
                ).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == uid,
                        song_metadata_cache.c.artist_name.isnot(None),
                        song_metadata_cache.c.name.isnot(None),
                    )
                )
            )
            total_possible_tags = user_eids_q.scalar() or 0

            # Count cached tags (approximate — entity_id matching is expensive)
            tags_count_q = await conn.execute(
                sa.select(sa.func.count()).select_from(lastfm_tags_cache).where(
                    lastfm_tags_cache.c.entity_type == "track"
                )
            )
            total_tags = tags_count_q.scalar() or 0

            # ── GPU embeddings (CLAP + MERT) for this user ─────────
            gpu_q = await conn.execute(
                sa.select(
                    sa.func.count().label("total"),
                    sa.func.count().filter(
                        audio_embeddings.c.clap_embedding.isnot(None)
                    ).label("clap"),
                    sa.func.count().filter(
                        audio_embeddings.c.mert_embedding.isnot(None)
                    ).label("mert"),
                ).where(audio_embeddings.c.user_id == uid)
            )
            gpu = gpu_q.first()

            # ── AI captions for this user ─────────────────────────
            caption_q = await conn.execute(
                sa.select(sa.func.count()).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == uid,
                        song_metadata_cache.c.ai_caption.isnot(None),
                        song_metadata_cache.c.ai_caption != "",
                    )
                )
            )
            ai_captions = caption_q.scalar() or 0

            # ── Cobweb stats ───────────────────────────────────────
            cobweb_q = await conn.execute(
                sa.select(
                    sa.func.count().label("total"),
                    sa.func.count().filter(
                        artist_cobweb.c.enriched == sa.true()
                    ).label("enriched"),
                    sa.func.sum(artist_cobweb.c.songs_fetched).label("songs"),
                ).where(artist_cobweb.c.user_id == uid)
            )
            cw = cobweb_q.first()

            # ── Indexing status ────────────────────────────────────
            idx_q = await conn.execute(
                sa.select(user_indexing_status).where(
                    sa.and_(
                        user_indexing_status.c.user_id == uid,
                        user_indexing_status.c.step != 99,
                    )
                )
            )
            idx = idx_q.first()

            # ── Build user entry ───────────────────────────────────
            user_data = {
                "user_id": uid,
                "email": user.email,
                "display_name": user.display_name or user.email,
                "songs": {
                    "total": total_songs,
                    "library": library_songs,
                    "non_library": non_library,
                },
                "audio_features": {
                    "enriched": audio_enriched,
                    "failed": audio_failed,
                    "partial": audio_partial,
                    "not_attempted": audio_not_attempted,
                    "library_enriched": lib_audio_enriched,
                    "library_failed": lib_audio_failed,
                    "library_pending": max(
                        0, library_songs - lib_audio_enriched - lib_audio_failed,
                    ),
                    "pct": round(audio_enriched / total_songs * 100, 1)
                    if total_songs > 0 else 0,
                },
                "tags": {
                    "possible": total_possible_tags,
                    "global_cached": total_tags,
                },
                "gpu_embeddings": {
                    "total": gpu.total or 0,
                    "clap": gpu.clap or 0,
                    "mert": gpu.mert or 0,
                },
                "ai_captions": ai_captions,
                "cobweb": {
                    "total": cw.total or 0,
                    "enriched": cw.enriched or 0,
                    "songs_fetched": cw.songs or 0,
                },
                "indexing": {
                    "step": idx.step if idx else None,
                    "step_name": idx.step_name if idx else None,
                    "progress": f"{idx.progress_current}/{idx.progress_total}"
                    if idx else None,
                    "completed": idx.completed_at.isoformat()
                    if idx and idx.completed_at else None,
                } if idx else None,
            }
            result["users"].append(user_data)

    # ── Global totals ──────────────────────────────────────────────
    async with engine.begin() as conn:
        global_q = await conn.execute(sa.text("""
            SELECT
                (SELECT count(*) FROM audio_features_global) AS global_features,
                (SELECT count(*) FROM global_song_cache) AS global_songs,
                (SELECT count(*) FROM lastfm_tags_cache
                 WHERE entity_type = 'track') AS total_tags,
                (SELECT count(DISTINCT source_mbid) FROM kg_relationships
                 WHERE source_mbid LIKE 'isrc:%') AS total_credits
        """))
        g = global_q.first()
        result["totals"] = {
            "global_audio_features": g.global_features or 0,
            "global_songs": g.global_songs or 0,
            "total_tags": g.total_tags or 0,
            "total_credits": g.total_credits or 0,
        }

    # ── Failure analysis from logs DB ──────────────────────────────
    if logs_engine:
        try:
            from musicmind.db.logs import enrichment_logs, error_logs

            async with logs_engine.begin() as conn:
                # Recent enrichment failures with details
                fail_q = await conn.execute(
                    sa.select(
                        enrichment_logs.c.stage,
                        enrichment_logs.c.result,
                        sa.func.count().label("count"),
                    ).where(
                        sa.and_(
                            enrichment_logs.c.timestamp >= sa.func.current_date(),
                            enrichment_logs.c.result != "success",
                        )
                    ).group_by(
                        enrichment_logs.c.stage, enrichment_logs.c.result,
                    ).order_by(sa.text("count DESC"))
                )
                failure_breakdown = [
                    {"stage": r.stage, "result": r.result, "count": r.count}
                    for r in fail_q
                ]

                # Recent error log messages (WARNING+)
                error_q = await conn.execute(
                    sa.select(
                        error_logs.c.level,
                        error_logs.c.logger_name,
                        error_logs.c.message,
                        error_logs.c.timestamp,
                    ).where(
                        error_logs.c.level.in_(["WARNING", "ERROR", "CRITICAL"])
                    ).order_by(error_logs.c.timestamp.desc())
                    .limit(20)
                )
                recent_errors = [
                    {
                        "level": r.level,
                        "logger": r.logger_name,
                        "message": r.message[:200],
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                    }
                    for r in error_q
                ]

                result["failure_analysis"] = {
                    "today": failure_breakdown,
                    "recent_errors": recent_errors,
                }
        except Exception:
            logger.debug("Logs DB query failed for diagnostics")

    # ── Generate insights ──────────────────────────────────────────
    insights = []
    for u in result["users"]:
        af = u["audio_features"]
        if af["failed"] > 0:
            insights.append({
                "level": "info",
                "user": u["email"],
                "message": (
                    f"{af['failed']} songs permanently failed audio enrichment "
                    f"(Deezer couldn't find them). These won't retry."
                ),
            })
        if af["not_attempted"] > 0:
            insights.append({
                "level": "warning",
                "user": u["email"],
                "message": (
                    f"{af['not_attempted']} songs haven't been attempted yet. "
                    f"The worker should pick these up next cycle."
                ),
            })
        if af["library_pending"] > 0:
            insights.append({
                "level": "critical",
                "user": u["email"],
                "message": (
                    f"{af['library_pending']} LIBRARY songs still need enrichment. "
                    f"These have priority over non-library songs."
                ),
            })
        if af.get("library_failed", 0) > 0:
            insights.append({
                "level": "warning",
                "user": u["email"],
                "message": (
                    f"{af['library_failed']} library songs failed enrichment "
                    f"(Deezer couldn't find them). "
                    f"Library audio: {af['library_enriched']}/{u['songs']['library']} OK."
                ),
            })
        if u["songs"]["missing_isrc"] > 0:
            insights.append({
                "level": "info",
                "user": u["email"],
                "message": (
                    f"{u['songs']['missing_isrc']} songs missing ISRC. "
                    f"ISRC backfill phase resolves ~100/cycle via Deezer."
                ),
            })

    result["insights"] = insights
    return result
