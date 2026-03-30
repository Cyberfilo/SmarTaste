"""Audio enrichment orchestrator — progressive fill from multiple API sources.

Enrichment cascade:
1. Deezer (ISRC-native, free) → BPM/tempo
2. ReccoBeats (Spotify ID, free) → energy, danceability, valence, acousticness
3. SoundStat (Spotify ID, paid) → complete features (only when budget_mode=True)

Each source only fills fields that are still missing. Per-field provenance
tracked in feature_source dict.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import audio_features_cache
from musicmind.engine.enrichment.deezer import fetch_deezer_features
from musicmind.engine.enrichment.musicbrainz import resolve_spotify_id
from musicmind.engine.enrichment.reccobeats import fetch_reccobeats_features
from musicmind.engine.enrichment.soundstat import fetch_soundstat_features

logger = logging.getLogger(__name__)

# Fields that can be enriched from external APIs
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
    """Enrich a batch of tracks with audio features from external APIs.

    Processes tracks through the enrichment cascade, storing results in
    audio_features_cache. Tracks already fully enriched are skipped.

    Args:
        engine: SQLAlchemy async engine.
        tracks: List of song dicts with at least catalog_id and optionally isrc.
        user_id: User ID for scoping the feature cache.
        soundstat_api_key: SoundStat API key (None = skip SoundStat).
        budget_mode: If True, use SoundStat for remaining gaps (costs money).

    Returns:
        Summary dict: {deezer: N, reccobeats: N, soundstat: N, skipped: N, total: N}
    """
    stats = {"deezer": 0, "reccobeats": 0, "soundstat": 0, "skipped": 0, "total": len(tracks)}

    for track in tracks:
        catalog_id = track.get("catalog_id", "")
        isrc = track.get("isrc") or ""
        service_source = track.get("service_source", "")

        if not catalog_id:
            stats["skipped"] += 1
            continue

        # Load existing features (if any)
        existing = await _get_existing_features(engine, catalog_id, user_id)
        feature_source = existing.get("feature_source", {}) if existing else {}
        features = dict(existing) if existing else {}

        # Determine which fields still need filling
        missing = _missing_fields(features)
        if not missing:
            stats["skipped"] += 1
            continue

        # Stage 1: Deezer (ISRC-based, free, BPM)
        if isrc and "tempo" in missing:
            deezer_result = await fetch_deezer_features(isrc)
            if deezer_result:
                filled = _merge_features(features, feature_source, deezer_result, "deezer")
                if filled:
                    stats["deezer"] += 1
                    missing = _missing_fields(features)

        # Resolve Spotify ID for ReccoBeats/SoundStat
        spotify_id = None
        if missing and ({"energy", "danceability", "acousticness", "valence_proxy"} & missing):
            spotify_id = await _get_spotify_id(engine, track, isrc, service_source)

        # Stage 2: ReccoBeats (Spotify ID, free)
        if spotify_id and missing:
            recco_result = await fetch_reccobeats_features(spotify_id)
            if recco_result:
                filled = _merge_features(features, feature_source, recco_result, "reccobeats")
                if filled:
                    stats["reccobeats"] += 1
                    missing = _missing_fields(features)

        # Stage 3: SoundStat (Spotify ID, paid — only in budget_mode)
        if budget_mode and spotify_id and missing and soundstat_api_key:
            ss_result = await fetch_soundstat_features(spotify_id, api_key=soundstat_api_key)
            if ss_result:
                filled = _merge_features(features, feature_source, ss_result, "soundstat")
                if filled:
                    stats["soundstat"] += 1

        # Store enriched features
        if feature_source:
            await _store_features(engine, catalog_id, user_id, features, feature_source)

    logger.info(
        "Enriched %d/%d: %d Deezer, %d ReccoBeats, %d SoundStat, %d skipped",
        stats["deezer"] + stats["reccobeats"] + stats["soundstat"],
        stats["total"],
        stats["deezer"],
        stats["reccobeats"],
        stats["soundstat"],
        stats["skipped"],
    )
    return stats


def _missing_fields(features: dict[str, Any]) -> set[str]:
    """Return the set of enrichable fields that are still None/missing."""
    return {f for f in ENRICHABLE_FIELDS if features.get(f) is None}


def _merge_features(
    features: dict[str, Any],
    feature_source: dict[str, str],
    new_data: dict[str, Any],
    source_name: str,
) -> int:
    """Merge new_data into features, only filling gaps. Track provenance.

    Returns the count of fields that were actually filled.
    """
    filled = 0
    for field, value in new_data.items():
        if field not in ENRICHABLE_FIELDS:
            continue
        if features.get(field) is not None:
            continue  # Already have this field
        features[field] = value
        feature_source[field] = source_name
        filled += 1
    return filled


async def _get_existing_features(
    engine: Any,
    catalog_id: str,
    user_id: str,
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
        "feature_source": row.feature_source or {},
    }


async def _store_features(
    engine: Any,
    catalog_id: str,
    user_id: str,
    features: dict[str, Any],
    feature_source: dict[str, str],
) -> None:
    """Upsert enriched features into audio_features_cache."""
    values = {
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
        "feature_source": feature_source,
        "enriched_at": datetime.now(UTC),
        "analyzed_at": datetime.now(UTC),
    }

    async with engine.begin() as conn:
        # Try insert, on conflict update.  Alias: t = audio_features_cache
        stmt = sa.text(  # noqa: E501
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
            " brightness = COALESCE(EXCLUDED.brightness, audio_features_cache.brightness),"
            " danceability = COALESCE(EXCLUDED.danceability, audio_features_cache.danceability),"
            " acousticness = COALESCE(EXCLUDED.acousticness, audio_features_cache.acousticness),"
            " valence_proxy = COALESCE(EXCLUDED.valence_proxy, audio_features_cache.valence_proxy),"
            " beat_strength = COALESCE(EXCLUDED.beat_strength, audio_features_cache.beat_strength),"
            " key = COALESCE(EXCLUDED.key, audio_features_cache.key),"
            " scale = COALESCE(EXCLUDED.scale, audio_features_cache.scale),"
            " instrumentalness = COALESCE(EXCLUDED.instrumentalness,"
            "   audio_features_cache.instrumentalness),"
            " loudness = COALESCE(EXCLUDED.loudness, audio_features_cache.loudness),"
            " feature_source = EXCLUDED.feature_source,"
            " enriched_at = EXCLUDED.enriched_at,"
            " analyzed_at = EXCLUDED.analyzed_at"
        )
        # JSON needs to be serialized for raw SQL
        import json

        raw_values = dict(values)
        raw_values["feature_source"] = json.dumps(feature_source)
        await conn.execute(stmt, raw_values)


async def _get_spotify_id(
    engine: Any,
    track: dict[str, Any],
    isrc: str,
    service_source: str,
) -> str | None:
    """Resolve a Spotify ID for a track.

    For Spotify tracks, the catalog_id IS the Spotify ID.
    For Apple Music tracks, resolve via ISRC → MusicBrainz.
    """
    # Spotify tracks already have the ID
    if service_source == "spotify":
        return track.get("catalog_id")

    # Try ISRC → Spotify ID resolution
    if isrc:
        return await resolve_spotify_id(isrc, engine=engine)

    return None
