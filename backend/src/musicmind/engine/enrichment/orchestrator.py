"""Audio enrichment orchestrator — 2-phase local pipeline.

Pipeline per track:
1. Essentia (CPU): local audio features + classifier labels from preview audio
2. Modal GPU: CLAP + MERT embeddings from preview (if configured)

Features cached globally by ISRC (shared across all users).
Preview URLs come from Apple Music / Spotify (already in song_metadata_cache).
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import audio_features_cache, audio_features_global

logger = logging.getLogger(__name__)

ENRICHABLE_FIELDS = {
    "tempo", "energy", "danceability", "acousticness", "valence_proxy",
    "beat_strength", "brightness", "key", "scale", "instrumentalness", "loudness",
}

# Concurrent tracks in-flight at once (bounded by memory: 15 × ~500KB = 7.5MB)
# With 8 vCPU available, I/O-bound async work scales well to 15 concurrent requests
# With ONNX pinned to 1 thread/inference, 25 concurrent async tasks
# saturate 8 vCPU with I/O overlap. Memory: 25 × 10MB = 250MB.
CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "25"))
BATCH_SIZE = int(os.environ.get("WORKER_BATCH_SIZE", "50"))


async def enrich_tracks(
    engine: Any,
    tracks: list[dict[str, Any]],
    *,
    user_id: str,
    openai_api_key: str | None = None,
    modal_endpoint_url: str | None = None,
) -> dict[str, int]:
    """2-phase enrichment pipeline.

    Phase 1 (CPU — Essentia):
        For each track with preview_url:
        - Download preview audio
        - Run Essentia extract_all → (scalar_features, effnet_embedding)
        - Run Essentia classifier heads → mood/genre/acousticness labels
        - Store scalar features in audio_features_cache
        - Store EffNet embedding in audio_embeddings
        - Store classifier labels as ai_tags in song_metadata_cache
        - Generate AI caption via OpenAI if configured

    Phase 2 (GPU — Modal):
        If modal_endpoint_url configured:
        - Batch all preview URLs that lack CLAP embeddings
        - Call enrich_batch_via_gpu
        - Store CLAP + MERT embeddings in audio_embeddings

    Returns stats dict with enrichment counts.
    """
    stats = {
        "essentia": 0, "captions": 0,
        "skipped": 0, "failed": 0, "total": len(tracks),
        "gpu_enriched": 0,
    }

    # ── Phase 1: Essentia audio features (CPU, local) ─────────────────
    audio_sem = asyncio.Semaphore(CONCURRENCY)

    async def _bounded_audio(track: dict[str, Any]) -> str:
        async with audio_sem:
            return await _enrich_single_track(
                engine, track, user_id=user_id,
                openai_api_key=openai_api_key,
            )

    for batch_start in range(0, len(tracks), BATCH_SIZE):
        batch = tracks[batch_start:batch_start + BATCH_SIZE]
        results = await asyncio.gather(
            *[_bounded_audio(t) for t in batch],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, str):
                stats[r] = stats.get(r, 0) + 1
            else:
                stats["failed"] += 1
        gc.collect()

    logger.info(
        "Phase 1 done: %d/%d Essentia enriched (%d skipped, %d failed)",
        stats["essentia"], stats["total"], stats["skipped"], stats["failed"],
    )

    # ── Phase 2: GPU embeddings — CLAP + MERT via Modal ───────────────
    if modal_endpoint_url:
        from musicmind.db.schema import audio_embeddings
        from musicmind.engine.enrichment.gpu_client import enrich_batch_via_gpu

        # Collect preview URLs for tracks that have them but lack CLAP embeddings
        gpu_candidates: list[tuple[dict[str, Any], str]] = []
        for t in tracks:
            preview = t.get("preview_url", "")
            if preview:
                gpu_candidates.append((t, preview))

        if gpu_candidates:
            # Batch check which already have CLAP embeddings
            catalog_ids = [t.get("catalog_id", "") for t, _ in gpu_candidates]
            cached_clap: set[str] = set()
            for i in range(0, len(catalog_ids), 500):
                chunk = catalog_ids[i:i + 500]
                async with engine.begin() as conn:
                    result = await conn.execute(
                        sa.select(audio_embeddings.c.catalog_id).where(
                            sa.and_(
                                audio_embeddings.c.user_id == user_id,
                                audio_embeddings.c.catalog_id.in_(chunk),
                                audio_embeddings.c.clap_embedding.isnot(None),
                            )
                        )
                    )
                    cached_clap.update(row.catalog_id for row in result)

            uncached_gpu = [
                (t, url) for t, url in gpu_candidates
                if t.get("catalog_id", "") not in cached_clap
            ]

            if uncached_gpu:
                logger.info(
                    "Phase 2: %d tracks need GPU enrichment (CLAP + MERT)",
                    len(uncached_gpu),
                )
                preview_urls = [url for _, url in uncached_gpu]
                try:
                    gpu_results = await enrich_batch_via_gpu(
                        preview_urls, modal_endpoint_url,
                    )
                    for (t, _), gpu_data in zip(uncached_gpu, gpu_results):
                        if gpu_data and not gpu_data.get("error"):
                            cid = t.get("catalog_id", "")
                            clap = gpu_data.get("clap_512")
                            mert = gpu_data.get("mert_768")
                            if clap or mert:
                                async with engine.begin() as conn:
                                    existing = await conn.execute(
                                        sa.select(audio_embeddings.c.catalog_id).where(
                                            sa.and_(
                                                audio_embeddings.c.catalog_id == cid,
                                                audio_embeddings.c.user_id == user_id,
                                            )
                                        )
                                    )
                                    if existing.first():
                                        await conn.execute(
                                            sa.update(audio_embeddings)
                                            .where(sa.and_(
                                                audio_embeddings.c.catalog_id == cid,
                                                audio_embeddings.c.user_id == user_id,
                                            ))
                                            .values(
                                                clap_embedding=clap,
                                                mert_embedding=mert,
                                            )
                                        )
                                    else:
                                        await conn.execute(
                                            audio_embeddings.insert().values(
                                                catalog_id=cid,
                                                user_id=user_id,
                                                embedding=[],
                                                clap_embedding=clap,
                                                mert_embedding=mert,
                                            )
                                        )
                                stats["gpu_enriched"] += 1
                except Exception:
                    logger.warning(
                        "Phase 2 GPU batch enrichment failed", exc_info=True,
                    )
                logger.info(
                    "Phase 2 done: %d CLAP/MERT embeddings stored",
                    stats["gpu_enriched"],
                )

    logger.info(
        "Pipeline complete: %d essentia, %d gpu, %d captions "
        "(%d skipped, %d failed) out of %d tracks",
        stats["essentia"], stats["gpu_enriched"], stats["captions"],
        stats["skipped"], stats["failed"], stats["total"],
    )
    return stats


async def enrich_candidates(
    engine: Any,
    candidates: list[dict[str, Any]],
    *,
    user_id: str,
    max_to_enrich: int = 30,
) -> dict[str, dict[str, Any]]:
    """Lazy-enrich recommendation candidates and return audio features map.

    Checks global ISRC cache first. Only enriches candidates missing features.
    Returns catalog_id -> features dict for scoring.
    """
    af_map: dict[str, dict[str, Any]] = {}

    # Load already-cached features (by user_id for now, ISRC cache below)
    catalog_ids = [c.get("catalog_id", "") for c in candidates if c.get("catalog_id")]
    if catalog_ids:
        cached = await _load_features_batch(engine, catalog_ids, user_id)
        af_map.update(cached)

    # Find candidates needing enrichment
    to_enrich = []
    for c in candidates:
        cid = c.get("catalog_id", "")
        if cid and cid not in af_map:
            to_enrich.append(c)
        if len(to_enrich) >= max_to_enrich:
            break

    if not to_enrich:
        return af_map

    logger.info("Lazy-enriching %d candidates at recommendation time", len(to_enrich))

    # Enrich concurrently (bounded by semaphore)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _bounded(track: dict[str, Any]) -> tuple[str, str]:
        cid = track.get("catalog_id", "")
        async with semaphore:
            result = await _enrich_single_track(engine, track, user_id=user_id)
        return cid, result

    results = await asyncio.gather(
        *[_bounded(t) for t in to_enrich],
        return_exceptions=True,
    )

    # Extract from tuples
    for item in results:
        if isinstance(item, Exception):
            continue
        cid, result = item
        if result == "essentia":
            features = await _get_existing_features(engine, cid, user_id)
            if features:
                clean = {k: v for k, v in features.items()
                         if k in ENRICHABLE_FIELDS and v is not None}
                if clean:
                    af_map[cid] = clean

    return af_map


async def _enrich_single_track(
    engine: Any,
    track: dict[str, Any],
    *,
    user_id: str,
    openai_api_key: str | None = None,
) -> str:
    """Enrich one track via Essentia. Returns result category string."""
    catalog_id = track.get("catalog_id", "")
    isrc = track.get("isrc") or ""

    if not catalog_id:
        return "skipped"

    # Skip fully enriched (per-user cache)
    existing = await _get_existing_features(engine, catalog_id, user_id)
    if existing and not _missing_fields(existing):
        return "skipped"

    # Check global ISRC cache — if another user already enriched this song, reuse it
    if isrc:
        global_hit = await _get_global_features(engine, isrc)
        if global_hit and not _missing_fields(global_hit):
            await _store_features(
                engine, catalog_id, user_id,
                dict(global_hit), _get_source_dict(global_hit),
            )
            return "skipped"

    features = dict(existing) if existing else {}
    feature_source = _get_source_dict(existing)
    enriched_by = "failed"

    # Resolve preview URL — from track dict or song_metadata_cache
    preview_url = track.get("preview_url") or None
    if not preview_url:
        async with engine.begin() as conn:
            from musicmind.db.schema import song_metadata_cache as smc
            cached_url = await conn.execute(
                sa.select(smc.c.preview_url).where(
                    sa.and_(smc.c.catalog_id == catalog_id, smc.c.preview_url.isnot(None))
                ).limit(1)
            )
            row = cached_url.first()
            if row and row.preview_url:
                preview_url = row.preview_url

    # Fallback: search Deezer for preview URL if none from service
    if not preview_url:
        try:
            from musicmind.engine.enrichment.deezer import search_preview_url
            preview_url = await search_preview_url(
                name=track.get("name", ""),
                artist_name=track.get("artist_name", ""),
                isrc=isrc or None,
            )
            if preview_url:
                # Cache the Deezer URL for future use
                async with engine.begin() as conn:
                    from musicmind.db.schema import song_metadata_cache as smc
                    await conn.execute(
                        sa.update(smc)
                        .where(sa.and_(smc.c.catalog_id == catalog_id))
                        .values(preview_url=preview_url)
                    )
        except Exception:
            logger.debug("Deezer preview fallback failed for %s", catalog_id)

    # No preview URL from any source → permanently mark as failed
    if not preview_url:
        await _store_features(
            engine, catalog_id, user_id,
            {}, {"_status": "no_data_available", "_reason": "no_preview_url"}, isrc="",
        )
        return "failed"

    # Audio analysis via Essentia (local, no API limits)
    if preview_url:
        try:
            audio_bytes = await _download_preview(preview_url)

            # Cached Deezer URL expired (403)? Get a fresh one
            if audio_bytes is None and "dzcdn.net" in preview_url:
                try:
                    from musicmind.engine.enrichment.deezer import search_preview_url
                    fresh_url = await search_preview_url(
                        name=track.get("name", ""),
                        artist_name=track.get("artist_name", ""),
                        isrc=isrc or None,
                    )
                    if fresh_url:
                        audio_bytes = await _download_preview(fresh_url)
                        if audio_bytes:
                            # Update cached URL
                            async with engine.begin() as conn:
                                from musicmind.db.schema import song_metadata_cache as smc
                                await conn.execute(
                                    sa.update(smc)
                                    .where(sa.and_(smc.c.catalog_id == catalog_id))
                                    .values(preview_url=fresh_url)
                                )
                except Exception:
                    pass
            if audio_bytes:
                from musicmind.engine.audio.essentia_extractor import (
                    is_essentia_available,
                )
                if is_essentia_available():
                    try:
                        from musicmind.engine.audio.essentia_extractor import (
                            extract_all,
                        )
                        extracted, embedding = extract_all(audio_bytes)
                        if extracted:
                            scalar = extracted.to_scalar_dict()
                            if _merge_features(
                                features, feature_source, scalar, "essentia",
                            ):
                                enriched_by = "essentia"
                            if openai_api_key:
                                try:
                                    from musicmind.engine.explanations import (
                                        generate_track_caption,
                                    )
                                    caption = await generate_track_caption(
                                        scalar, openai_api_key,
                                    )
                                    if caption:
                                        await _store_caption(
                                            engine, catalog_id, user_id, caption,
                                        )
                                except Exception:
                                    logger.debug(
                                        "Caption generation failed for %s", catalog_id,
                                    )
                            if scalar:
                                try:
                                    await _store_ai_tags(
                                        engine, catalog_id, user_id, scalar,
                                    )
                                except Exception:
                                    logger.debug(
                                        "AI tags storage failed for %s", catalog_id,
                                    )
                        if embedding and len(embedding) >= 128:
                            await _store_embedding(
                                engine, catalog_id, user_id,
                                embedding, isrc=isrc,
                            )
                    except Exception:
                        logger.debug("Essentia failed for %s", catalog_id)

                del audio_bytes
        except Exception:
            logger.debug("Audio enrichment failed for %s", catalog_id)

    if feature_source:
        await _store_features(
            engine, catalog_id, user_id, features, feature_source, isrc=isrc,
        )
    elif enriched_by == "failed":
        await _store_features(
            engine, catalog_id, user_id,
            {}, {"_status": "no_data_available"}, isrc="",
        )

    return enriched_by


# ── Helpers ──────────────────────────────────────────────────────────────────

def _missing_fields(features: dict[str, Any]) -> set[str]:
    return {f for f in ENRICHABLE_FIELDS if features.get(f) is None}


def _get_source_dict(existing: dict[str, Any] | None) -> dict[str, str]:
    if not existing:
        return {}
    fs = existing.get("feature_source", {})
    if isinstance(fs, str):
        try:
            return json.loads(fs)
        except (json.JSONDecodeError, TypeError):
            return {}
    return dict(fs) if fs else {}


def _merge_features(
    features: dict[str, Any], feature_source: dict[str, str],
    new_data: dict[str, Any], source_name: str,
) -> int:
    filled = 0
    for field, value in new_data.items():
        if field not in ENRICHABLE_FIELDS or features.get(field) is not None:
            continue
        features[field] = value
        feature_source[field] = source_name
        filled += 1
    return filled


async def _download_preview(url: str) -> bytes | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content
            return content if len(content) > 1000 else None
    except Exception:
        return None


async def _load_features_batch(
    engine: Any, catalog_ids: list[str], user_id: str,
) -> dict[str, dict[str, Any]]:
    """Load cached features for multiple tracks at once."""
    af_map: dict[str, dict[str, Any]] = {}
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(audio_features_cache).where(
                sa.and_(
                    audio_features_cache.c.catalog_id.in_(catalog_ids),
                    audio_features_cache.c.user_id == user_id,
                )
            )
        )
        for row in result:
            row_features: dict[str, Any] = {}
            for field in ENRICHABLE_FIELDS:
                val = getattr(row, field, None)
                if val is not None:
                    row_features[field] = val
            if row_features:
                af_map[row.catalog_id] = row_features
    return af_map


async def _get_existing_features(
    engine: Any, catalog_id: str, user_id: str,
) -> dict[str, Any] | None:
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(audio_features_cache).where(
                sa.and_(
                    audio_features_cache.c.catalog_id == catalog_id,
                    audio_features_cache.c.user_id == user_id,
                )
            )
        )
        row = result.first()
    if row is None:
        return None
    fs = row.feature_source
    if isinstance(fs, str):
        try:
            fs = json.loads(fs)
        except (json.JSONDecodeError, TypeError):
            fs = {}
    return {
        "tempo": row.tempo, "energy": row.energy,
        "brightness": row.brightness, "danceability": row.danceability,
        "acousticness": row.acousticness, "valence_proxy": row.valence_proxy,
        "beat_strength": row.beat_strength, "key": row.key,
        "scale": row.scale, "instrumentalness": row.instrumentalness,
        "loudness": row.loudness, "feature_source": fs or {},
    }


async def _get_global_features(
    engine: Any, isrc: str,
) -> dict[str, Any] | None:
    """Check the global ISRC-keyed cache for existing enrichment."""
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(audio_features_global).where(
                audio_features_global.c.isrc == isrc
            )
        )
        row = result.first()
    if row is None:
        return None
    fs = row.feature_source
    if isinstance(fs, str):
        try:
            fs = json.loads(fs)
        except (json.JSONDecodeError, TypeError):
            fs = {}
    return {
        "tempo": row.tempo, "energy": row.energy,
        "brightness": row.brightness, "danceability": row.danceability,
        "acousticness": row.acousticness, "valence_proxy": row.valence_proxy,
        "beat_strength": row.beat_strength, "key": row.key,
        "scale": row.scale, "instrumentalness": row.instrumentalness,
        "loudness": row.loudness, "feature_source": fs or {},
    }


async def _store_global_features(
    engine: Any, isrc: str,
    features: dict[str, Any], feature_source: dict[str, str],
) -> None:
    """Write enriched features to the global ISRC cache."""
    if not isrc:
        return
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO audio_features_global"
            " (isrc, tempo, energy, brightness, danceability,"
            "  acousticness, valence_proxy, beat_strength,"
            "  key, scale, instrumentalness, loudness,"
            "  feature_source, analyzed_at)"
            " VALUES (:isrc, :tempo, :energy, :brightness,"
            "  :danceability, :acousticness, :valence_proxy, :beat_strength,"
            "  :key, :scale, :instrumentalness, :loudness,"
            "  :feature_source, :analyzed_at)"
            " ON CONFLICT (isrc) DO UPDATE SET"
            " tempo = COALESCE(EXCLUDED.tempo, audio_features_global.tempo),"
            " energy = COALESCE(EXCLUDED.energy, audio_features_global.energy),"
            " brightness = COALESCE(EXCLUDED.brightness,"
            "   audio_features_global.brightness),"
            " danceability = COALESCE(EXCLUDED.danceability,"
            "   audio_features_global.danceability),"
            " acousticness = COALESCE(EXCLUDED.acousticness,"
            "   audio_features_global.acousticness),"
            " valence_proxy = COALESCE(EXCLUDED.valence_proxy,"
            "   audio_features_global.valence_proxy),"
            " beat_strength = COALESCE(EXCLUDED.beat_strength,"
            "   audio_features_global.beat_strength),"
            " feature_source = :feature_source,"
            " analyzed_at = :analyzed_at"
        ), {
            "isrc": isrc,
            "tempo": features.get("tempo"),
            "energy": features.get("energy"),
            "brightness": features.get("brightness"),
            "danceability": features.get("danceability"),
            "acousticness": features.get("acousticness"),
            "valence_proxy": features.get("valence_proxy"),
            "beat_strength": features.get("beat_strength"),
            "key": features.get("key"),
            "scale": features.get("scale"),
            "instrumentalness": features.get("instrumentalness"),
            "loudness": features.get("loudness"),
            "feature_source": json.dumps(feature_source),
            "analyzed_at": now,
        })


async def _store_features(
    engine: Any, catalog_id: str, user_id: str,
    features: dict[str, Any], feature_source: dict[str, str],
    isrc: str = "",
) -> None:
    now = datetime.now(UTC)
    # Write to global ISRC cache first
    if isrc:
        await _store_global_features(engine, isrc, features, feature_source)
    async with engine.begin() as conn:
        stmt = sa.text(
            "INSERT INTO audio_features_cache"
            " (catalog_id, user_id, tempo, energy, brightness,"
            "  danceability, acousticness, valence_proxy, beat_strength,"
            "  key, scale, instrumentalness, loudness,"
            "  feature_source, enriched_at, analyzed_at)"
            " VALUES"
            " (:catalog_id, :user_id, :tempo, :energy, :brightness,"
            "  :danceability, :acousticness, :valence_proxy, :beat_strength,"
            "  :key, :scale, :instrumentalness, :loudness,"
            "  :feature_source, :enriched_at, :analyzed_at)"
            " ON CONFLICT (catalog_id, user_id) DO UPDATE SET"
            " tempo = COALESCE(EXCLUDED.tempo, audio_features_cache.tempo),"
            " energy = COALESCE(EXCLUDED.energy, audio_features_cache.energy),"
            " brightness = COALESCE(EXCLUDED.brightness,"
            "   audio_features_cache.brightness),"
            " danceability = COALESCE(EXCLUDED.danceability,"
            "   audio_features_cache.danceability),"
            " acousticness = COALESCE(EXCLUDED.acousticness,"
            "   audio_features_cache.acousticness),"
            " valence_proxy = COALESCE(EXCLUDED.valence_proxy,"
            "   audio_features_cache.valence_proxy),"
            " beat_strength = COALESCE(EXCLUDED.beat_strength,"
            "   audio_features_cache.beat_strength),"
            " key = COALESCE(EXCLUDED.key, audio_features_cache.key),"
            " scale = COALESCE(EXCLUDED.scale, audio_features_cache.scale),"
            " instrumentalness = COALESCE(EXCLUDED.instrumentalness,"
            "   audio_features_cache.instrumentalness),"
            " loudness = COALESCE(EXCLUDED.loudness,"
            "   audio_features_cache.loudness),"
            " feature_source = :feature_source,"
            " enriched_at = :enriched_at,"
            " analyzed_at = :analyzed_at"
        )
        await conn.execute(stmt, {
            "catalog_id": catalog_id, "user_id": user_id,
            "tempo": features.get("tempo"),
            "energy": features.get("energy"),
            "brightness": features.get("brightness"),
            "danceability": features.get("danceability"),
            "acousticness": features.get("acousticness"),
            "valence_proxy": features.get("valence_proxy"),
            "beat_strength": features.get("beat_strength"),
            "key": features.get("key"),
            "scale": features.get("scale"),
            "instrumentalness": features.get("instrumentalness"),
            "loudness": features.get("loudness"),
            "feature_source": json.dumps(feature_source),
            "enriched_at": now, "analyzed_at": now,
        })


async def _store_embedding(
    engine: Any,
    catalog_id: str,
    user_id: str,
    embedding: list[float],
    *,
    isrc: str = "",
) -> None:
    """Store embedding in per-user + global (ISRC) tables."""
    now = datetime.now(UTC)
    dim = len(embedding)
    model = f"discogs-effnet-{dim}"
    emb_json = json.dumps(embedding)

    # Per-user storage
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO audio_embeddings"
                " (catalog_id, user_id, embedding, isrc, model_version,"
                "  embedding_dim, analyzed_at)"
                " VALUES (:cid, :uid, :emb, :isrc, :model, :dim, :now)"
                " ON CONFLICT (catalog_id, user_id) DO UPDATE SET"
                " embedding = :emb, model_version = :model,"
                " embedding_dim = :dim, analyzed_at = :now"
            ), {
                "cid": catalog_id, "uid": user_id, "emb": emb_json,
                "isrc": isrc, "model": model, "dim": dim, "now": now,
            })
    except Exception:
        logger.debug("Failed to store per-user embedding for %s", catalog_id)

    # Global ISRC storage
    if isrc:
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text(
                    "INSERT INTO audio_embeddings_global"
                    " (isrc, embedding, embedding_dim, model_version, analyzed_at)"
                    " VALUES (:isrc, :emb, :dim, :model, :now)"
                    " ON CONFLICT (isrc) DO UPDATE SET"
                    " embedding = CASE WHEN :dim > audio_embeddings_global.embedding_dim"
                    "   THEN :emb ELSE audio_embeddings_global.embedding END,"
                    " embedding_dim = GREATEST(:dim, audio_embeddings_global.embedding_dim),"
                    " model_version = CASE WHEN :dim > audio_embeddings_global.embedding_dim"
                    "   THEN :model ELSE audio_embeddings_global.model_version END,"
                    " analyzed_at = :now"
                ), {
                    "isrc": isrc, "emb": emb_json,
                    "dim": dim, "model": model, "now": now,
                })
        except Exception:
            logger.debug("Failed to store global embedding for ISRC %s", isrc)


async def _store_caption(
    engine: Any, catalog_id: str, user_id: str, caption: str,
) -> None:
    """Store AI-generated caption in song_metadata_cache."""
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "UPDATE song_metadata_cache SET ai_caption = :caption"
                    " WHERE catalog_id = :cid AND user_id = :uid"
                ),
                {"caption": caption, "cid": catalog_id, "uid": user_id},
            )
    except Exception:
        logger.debug("Failed to store caption for %s", catalog_id)


async def _store_ai_tags(
    engine: Any, catalog_id: str, user_id: str, tags: dict,
) -> None:
    """Store structured AI tags in song_metadata_cache."""
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "UPDATE song_metadata_cache SET ai_tags = :tags"
                    " WHERE catalog_id = :cid AND user_id = :uid"
                ),
                {"tags": json.dumps(tags), "cid": catalog_id, "uid": user_id},
            )
    except Exception:
        logger.debug("Failed to store AI tags for %s", catalog_id)
