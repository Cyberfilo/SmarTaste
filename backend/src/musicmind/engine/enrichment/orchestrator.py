"""Audio enrichment orchestrator — Deezer preview → ReccoBeats analysis.

Pipeline:
1. Deezer: search by name → get 30s preview MP3 URL + BPM (free)
2. ReccoBeats: upload preview → get all 9 audio features (free, no auth)
3. SoundStat: Spotify ID lookup for remaining gaps (paid, optional)

Runs automatically when a user connects a music service.
Per-field provenance tracked in feature_source dict.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
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


async def enrich_tracks(
    engine: Any,
    tracks: list[dict[str, Any]],
    *,
    user_id: str,
    soundstat_api_key: str | None = None,
    budget_mode: bool = False,
) -> dict[str, int]:
    """Enrich all tracks with audio features.

    Pipeline per track:
    1. Deezer search → BPM + 30s preview URL (free)
    2. Download preview → ReccoBeats upload → 9 audio features (free)
    3. SoundStat for remaining gaps (paid, only if API key set)

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

    for track in tracks:
        catalog_id = track.get("catalog_id", "")
        name = track.get("name", "")
        artist_name = track.get("artist_name", "")
        isrc = track.get("isrc") or ""
        service_source = track.get("service_source", "")

        if not catalog_id:
            stats["skipped"] += 1
            continue

        # Skip fully enriched tracks
        existing = await _get_existing_features(engine, catalog_id, user_id)
        if existing:
            missing = _missing_fields(existing)
            if not missing:
                stats["skipped"] += 1
                continue

        features = dict(existing) if existing else {}
        feature_source = _get_source_dict(existing)

        # ── Stage 1: Deezer (BPM + preview URL) ──────────────
        preview_url = None
        if name and artist_name:
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
                    stats["deezer"] += 1

        # ── Stage 2: ReccoBeats (upload preview → 9 features) ─
        if preview_url:
            audio_bytes = await _download_preview(preview_url)
            if audio_bytes:
                recco_result = await analyze_audio_features(audio_bytes)
                if recco_result:
                    filled = _merge_features(
                        features, feature_source, recco_result, "reccobeats"
                    )
                    if filled:
                        stats["reccobeats"] += 1

        # ── Stage 3: SoundStat (paid, Spotify ID, gap-fill) ──
        missing = _missing_fields(features)
        if missing and soundstat_api_key:
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
                        stats["soundstat"] += 1

        # Store if anything was enriched
        if feature_source:
            await _store_features(
                engine, catalog_id, user_id, features, feature_source
            )
        elif not existing:
            stats["failed"] += 1

    logger.info(
        "Enriched %d/%d: %d Deezer, %d ReccoBeats, %d SoundStat, "
        "%d skipped, %d failed",
        stats["deezer"] + stats["reccobeats"] + stats["soundstat"],
        stats["total"], stats["deezer"], stats["reccobeats"],
        stats["soundstat"], stats["skipped"], stats["failed"],
    )
    return stats


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
    """Download a 30s audio preview (sequential, polite)."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.debug("Failed to download preview from %s", url[:60])
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
