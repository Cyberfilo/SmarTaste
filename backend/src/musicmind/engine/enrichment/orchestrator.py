"""Audio enrichment orchestrator — SoundStat-powered feature extraction.

SoundStat is the primary (and only reliable) enrichment source.
Requires Spotify track IDs — Apple Music tracks are resolved via
ISRC → MusicBrainz → Spotify ID.

Per-field provenance tracked in feature_source dict.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import audio_features_cache
from musicmind.engine.enrichment.musicbrainz import resolve_spotify_id
from musicmind.engine.enrichment.soundstat import fetch_soundstat_features

logger = logging.getLogger(__name__)

# Fields that can be enriched
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
    """Enrich a batch of tracks with audio features from SoundStat.

    Only enriches tracks that have missing features. Requires a SoundStat
    API key to function — degrades gracefully without one.

    Args:
        engine: SQLAlchemy async engine.
        tracks: List of song dicts with catalog_id, isrc, service_source.
        user_id: User ID for scoping the feature cache.
        soundstat_api_key: SoundStat API key (None = skip enrichment).
        budget_mode: Not used (kept for backward compatibility).

    Returns:
        Summary dict: {soundstat: N, skipped: N, no_spotify_id: N, total: N}
    """
    stats = {"soundstat": 0, "skipped": 0, "no_spotify_id": 0, "total": len(tracks)}

    if not soundstat_api_key:
        logger.info("No SoundStat API key — skipping audio enrichment")
        stats["skipped"] = len(tracks)
        return stats

    for track in tracks:
        catalog_id = track.get("catalog_id", "")
        isrc = track.get("isrc") or ""
        service_source = track.get("service_source", "")

        if not catalog_id:
            stats["skipped"] += 1
            continue

        # Check if already enriched
        existing = await _get_existing_features(engine, catalog_id, user_id)
        if existing:
            missing = _missing_fields(existing)
            if not missing:
                stats["skipped"] += 1
                continue

        # Resolve Spotify ID
        spotify_id = await _get_spotify_id(engine, track, isrc, service_source)
        if not spotify_id:
            stats["no_spotify_id"] += 1
            continue

        # Fetch from SoundStat
        ss_result = await fetch_soundstat_features(
            spotify_id, api_key=soundstat_api_key
        )
        if not ss_result:
            stats["skipped"] += 1
            continue

        # Build feature dict with provenance
        features = dict(existing) if existing else {}
        feature_source = (
            existing.get("feature_source", {}) if existing else {}
        )
        if isinstance(feature_source, str):
            try:
                feature_source = json.loads(feature_source)
            except (json.JSONDecodeError, TypeError):
                feature_source = {}

        filled = _merge_features(features, feature_source, ss_result, "soundstat")
        if filled:
            await _store_features(engine, catalog_id, user_id, features, feature_source)
            stats["soundstat"] += 1

    logger.info(
        "Enriched %d/%d via SoundStat, %d skipped, %d no Spotify ID",
        stats["soundstat"], stats["total"],
        stats["skipped"], stats["no_spotify_id"],
    )
    return stats


def _missing_fields(features: dict[str, Any]) -> set[str]:
    """Return enrichable fields that are still None/missing."""
    return {f for f in ENRICHABLE_FIELDS if features.get(f) is None}


def _merge_features(
    features: dict[str, Any],
    feature_source: dict[str, str],
    new_data: dict[str, Any],
    source_name: str,
) -> int:
    """Merge new_data into features, only filling gaps. Track provenance."""
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


async def _get_existing_features(
    engine: Any, catalog_id: str, user_id: str
) -> dict[str, Any] | None:
    """Load existing features from audio_features_cache."""
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
        "tempo": row.tempo,
        "energy": row.energy,
        "brightness": row.brightness,
        "danceability": row.danceability,
        "acousticness": row.acousticness,
        "valence_proxy": row.valence_proxy,
        "beat_strength": row.beat_strength,
        "key": row.key,
        "scale": row.scale,
        "instrumentalness": row.instrumentalness,
        "loudness": row.loudness,
        "feature_source": fs or {},
    }


async def _store_features(
    engine: Any,
    catalog_id: str,
    user_id: str,
    features: dict[str, Any],
    feature_source: dict[str, str],
) -> None:
    """Upsert enriched features into audio_features_cache."""
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
            "catalog_id": catalog_id,
            "user_id": user_id,
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
            "enriched_at": now,
            "analyzed_at": now,
        })


async def _get_spotify_id(
    engine: Any, track: dict[str, Any], isrc: str, service_source: str
) -> str | None:
    """Resolve a Spotify ID for a track."""
    if service_source == "spotify":
        return track.get("catalog_id")
    if isrc:
        return await resolve_spotify_id(isrc, engine=engine)
    return None
