"""Audio enrichment orchestrator — batch processing with rate limit handling.

Pipeline per track:
1. Deezer: search by name → 30s preview MP3 + BPM (free)
2. ReccoBeats: upload preview → 9 audio features (free, rate limited)
3. SoundStat: Spotify ID lookup for gaps (paid, optional)

Memory-safe: processes in small batches with GC between batches.
Rate-limit-safe: exponential backoff on 429/timeout from ReccoBeats.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import audio_features_cache
from musicmind.engine.enrichment.deezer import fetch_deezer_features
from musicmind.engine.enrichment.musicbrainz import resolve_spotify_id
from musicmind.engine.enrichment.reccobeats import analyze_audio_features
from musicmind.engine.enrichment.soundstat import fetch_soundstat_features

logger = logging.getLogger(__name__)

ENRICHABLE_FIELDS = {
    "tempo", "energy", "danceability", "acousticness", "valence_proxy",
    "beat_strength", "brightness", "key", "scale", "instrumentalness", "loudness",
}

# Batch size: process N tracks then pause for GC + rate limit cooldown
BATCH_SIZE = 5
BATCH_DELAY_SECONDS = 2.0
# Delay between individual tracks (respect ReccoBeats rate limits)
TRACK_DELAY_SECONDS = 1.0


async def enrich_tracks(
    engine: Any,
    tracks: list[dict[str, Any]],
    *,
    user_id: str,
    soundstat_api_key: str | None = None,
    budget_mode: bool = False,
) -> dict[str, int]:
    """Enrich tracks in small batches to avoid OOM and rate limits.

    Processes BATCH_SIZE tracks at a time with delays between batches.
    Each track: Deezer search → download preview → ReccoBeats upload.

    Args:
        engine: SQLAlchemy async engine.
        tracks: Song dicts with catalog_id, name, artist_name, isrc.
        user_id: User ID for cache scoping.
        soundstat_api_key: Optional SoundStat key for gap-filling.
        budget_mode: Not used (backward compat).

    Returns:
        Summary dict with per-source counts.
    """
    stats = {
        "deezer": 0, "reccobeats": 0, "soundstat": 0,
        "skipped": 0, "failed": 0, "total": len(tracks),
    }

    # Process in batches
    for batch_start in range(0, len(tracks), BATCH_SIZE):
        batch = tracks[batch_start:batch_start + BATCH_SIZE]

        for track in batch:
            result = await _enrich_single_track(
                engine, track, user_id=user_id,
                soundstat_api_key=soundstat_api_key,
            )
            stats[result] += 1

            # Delay between tracks to respect rate limits
            await asyncio.sleep(TRACK_DELAY_SECONDS)

        # Between batches: force GC + longer delay
        gc.collect()
        if batch_start + BATCH_SIZE < len(tracks):
            await asyncio.sleep(BATCH_DELAY_SECONDS)

    logger.info(
        "Enriched %d/%d: %d Deezer, %d ReccoBeats, %d SoundStat, "
        "%d skipped, %d failed",
        stats["deezer"] + stats["reccobeats"] + stats["soundstat"],
        stats["total"], stats["deezer"], stats["reccobeats"],
        stats["soundstat"], stats["skipped"], stats["failed"],
    )
    return stats


async def _enrich_single_track(
    engine: Any,
    track: dict[str, Any],
    *,
    user_id: str,
    soundstat_api_key: str | None = None,
) -> str:
    """Enrich a single track. Returns the result category string.

    Returns one of: "deezer", "reccobeats", "soundstat", "skipped", "failed"
    """
    catalog_id = track.get("catalog_id", "")
    name = track.get("name", "")
    artist_name = track.get("artist_name", "")
    isrc = track.get("isrc") or ""
    service_source = track.get("service_source", "")

    if not catalog_id:
        return "skipped"

    # Skip fully enriched tracks
    existing = await _get_existing_features(engine, catalog_id, user_id)
    if existing and not _missing_fields(existing):
        return "skipped"

    features = dict(existing) if existing else {}
    feature_source = _get_source_dict(existing)
    enriched_by = "failed"

    # ── Stage 1: Deezer (BPM + preview URL) ──────────────────
    preview_url = None
    if name and artist_name:
        try:
            deezer_result = await fetch_deezer_features(
                name=name, artist_name=artist_name,
            )
            if deezer_result:
                preview_url = deezer_result.pop("preview_url", None)
                deezer_result.pop("deezer_id", None)
                filled = _merge_features(
                    features, feature_source, deezer_result, "deezer"
                )
                if filled:
                    enriched_by = "deezer"
        except Exception:
            logger.debug("Deezer failed for %s", catalog_id)

    # ── Stage 2: ReccoBeats (upload preview → features) ──────
    if preview_url:
        try:
            audio_bytes = await _download_preview(preview_url)
            if audio_bytes:
                recco_result = await _reccobeats_with_retry(audio_bytes)
                # Release preview bytes immediately
                del audio_bytes
                if recco_result:
                    filled = _merge_features(
                        features, feature_source, recco_result, "reccobeats"
                    )
                    if filled:
                        enriched_by = "reccobeats"
        except Exception:
            logger.debug("ReccoBeats failed for %s", catalog_id)

    # ── Stage 3: SoundStat (paid, gap-fill) ──────────────────
    missing = _missing_fields(features)
    if missing and soundstat_api_key:
        try:
            spotify_id = await _get_spotify_id(
                engine, track, isrc, service_source
            )
            if spotify_id:
                ss_result = await fetch_soundstat_features(
                    spotify_id, api_key=soundstat_api_key
                )
                if ss_result:
                    filled = _merge_features(
                        features, feature_source, ss_result, "soundstat"
                    )
                    if filled:
                        enriched_by = "soundstat"
        except Exception:
            logger.debug("SoundStat failed for %s", catalog_id)

    # Store if anything was enriched
    if feature_source:
        await _store_features(engine, catalog_id, user_id, features, feature_source)

    return enriched_by


async def _reccobeats_with_retry(
    audio_bytes: bytes,
    max_retries: int = 3,
) -> dict[str, Any] | None:
    """Upload to ReccoBeats with exponential backoff on rate limit/timeout."""
    for attempt in range(max_retries):
        result = await analyze_audio_features(audio_bytes)
        if result is not None:
            return result

        # ReccoBeats returned None — could be rate limit or server error
        wait = 2.0 * (2 ** attempt)  # 2s, 4s, 8s
        logger.info("ReccoBeats retry %d/%d, waiting %.0fs", attempt + 1, max_retries, wait)
        await asyncio.sleep(wait)

    return None


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
    features: dict[str, Any],
    feature_source: dict[str, str],
    new_data: dict[str, Any],
    source_name: str,
) -> int:
    filled = 0
    for field, value in new_data.items():
        if field not in ENRICHABLE_FIELDS:
            continue
        if features.get(field) is not None:
            continue
        features[field] = value
        feature_source[field] = source_name
        filled += 1
    return filled


async def _download_preview(url: str) -> bytes | None:
    """Download a 30s audio preview. Returns None on error."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content
            # Sanity check: previews are typically 200KB-600KB
            if len(content) < 1000:
                return None
            return content
    except Exception:
        logger.debug("Preview download failed: %s", url[:60])
        return None


async def _get_existing_features(
    engine: Any, catalog_id: str, user_id: str
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


async def _store_features(
    engine: Any, catalog_id: str, user_id: str,
    features: dict[str, Any], feature_source: dict[str, str],
) -> None:
    now = datetime.now(UTC)
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


async def _get_spotify_id(
    engine: Any, track: dict[str, Any], isrc: str, service_source: str
) -> str | None:
    if service_source == "spotify":
        return track.get("catalog_id")
    if isrc:
        return await resolve_spotify_id(isrc, engine=engine)
    return None
