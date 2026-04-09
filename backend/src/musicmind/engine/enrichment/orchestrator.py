"""Audio enrichment orchestrator — fast batch processing with Retry-After.

Pipeline per track:
1. Deezer: search by name → 30s preview MP3 + BPM (free)
2. ReccoBeats: upload preview → 9 audio features (free)
3. SoundStat: Spotify ID → complete features (paid, optional)

Features cached globally by ISRC (shared across all users).
No artificial delays — only pauses on Retry-After headers from APIs.
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
from musicmind.engine.enrichment.deezer import fetch_deezer_features
from musicmind.engine.enrichment.musicbrainz import resolve_spotify_id
from musicmind.engine.enrichment.soundstat import fetch_soundstat_features

logger = logging.getLogger(__name__)

ENRICHABLE_FIELDS = {
    "tempo", "energy", "danceability", "acousticness", "valence_proxy",
    "beat_strength", "brightness", "key", "scale", "instrumentalness", "loudness",
}

# Concurrent tracks in-flight at once (bounded by memory: 15 × ~500KB = 7.5MB)
# With 8 vCPU available, I/O-bound async work scales well to 15 concurrent requests
CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "15"))
BATCH_SIZE = int(os.environ.get("WORKER_BATCH_SIZE", "50"))


async def enrich_tracks(
    engine: Any,
    tracks: list[dict[str, Any]],
    *,
    user_id: str,
    soundstat_api_key: str | None = None,
    budget_mode: bool = False,
) -> dict[str, int]:
    """Enrich tracks concurrently with bounded parallelism.

    Runs CONCURRENCY tracks simultaneously using asyncio.Semaphore.
    Each track: Deezer search + preview download + ReccoBeats upload
    overlap with other tracks' I/O waits.

    Speed: ~5x faster than sequential (8s/track → ~1.6s/track effective).
    Memory: bounded at CONCURRENCY × ~500KB previews.
    """
    stats = {
        "deezer": 0, "reccobeats": 0, "soundstat": 0,
        "skipped": 0, "failed": 0, "total": len(tracks),
    }
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _bounded_enrich(track: dict[str, Any]) -> str:
        async with semaphore:
            return await _enrich_single_track(
                engine, track, user_id=user_id,
                soundstat_api_key=soundstat_api_key,
            )

    # Process in batches of BATCH_SIZE with concurrent tracks within each batch
    for batch_start in range(0, len(tracks), BATCH_SIZE):
        batch = tracks[batch_start:batch_start + BATCH_SIZE]

        # Run all tracks in this batch concurrently (bounded by semaphore)
        results = await asyncio.gather(
            *[_bounded_enrich(track) for track in batch],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, str):
                stats[result] += 1
            else:
                stats["failed"] += 1

        gc.collect()

    logger.info(
        "Enriched %d/%d: %d Deezer, %d ReccoBeats, %d SoundStat, "
        "%d skipped, %d failed",
        stats["deezer"] + stats["reccobeats"] + stats["soundstat"],
        stats["total"], stats["deezer"], stats["reccobeats"],
        stats["soundstat"], stats["skipped"], stats["failed"],
    )
    return stats


async def enrich_tracks_global(
    engine: Any,
    tracks: list[dict[str, Any]],
    *,
    soundstat_api_key: str | None = None,
) -> dict[str, int]:
    """Enrich tracks and store ONLY in audio_features_global (by ISRC).

    Used by the worker for global enrichment — no per-user audio_features_cache.
    Skips tracks without ISRC (can't store globally without it).
    Reuses the same Deezer → ReccoBeats pipeline but only writes globally.
    """
    stats = {"enriched": 0, "skipped": 0, "failed": 0, "total": len(tracks)}

    for track in tracks:
        isrc = track.get("isrc", "")
        if not isrc:
            stats["skipped"] += 1
            continue

        # Check if already globally enriched
        existing = await _get_global_features(engine, isrc)
        if existing and existing.get("energy") is not None:
            stats["skipped"] += 1
            continue

        name = track.get("name", "")
        artist_name = track.get("artist_name", "")
        features: dict[str, Any] = {}
        feature_source: dict[str, str] = {}

        # Stage 1: Deezer (check cached preview_url first)
        preview_url = track.get("preview_url") or None
        if not preview_url and (name or isrc):
            try:
                deezer_result = await fetch_deezer_features(
                    name=name, artist_name=artist_name, isrc=isrc or None,
                )
                if deezer_result:
                    preview_url = deezer_result.pop("preview_url", None)
                    deezer_result.pop("deezer_id", None)
                    _merge_features(features, feature_source, deezer_result, "deezer")
            except Exception:
                pass

        # Stage 2: ReccoBeats
        if preview_url:
            try:
                audio_bytes = await _download_preview(preview_url)
                if audio_bytes:
                    recco_result = await _reccobeats_with_retry(audio_bytes)
                    del audio_bytes
                    if recco_result:
                        _merge_features(
                            features, feature_source, recco_result, "reccobeats",
                        )
            except Exception:
                pass

        if features and any(v is not None for v in features.values()):
            await _store_global_features(engine, isrc, features, feature_source)
            stats["enriched"] += 1
        else:
            stats["failed"] += 1

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
    Returns catalog_id → features dict for scoring.
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
        if result in ("deezer", "reccobeats", "soundstat"):
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
    soundstat_api_key: str | None = None,
) -> str:
    """Enrich one track. Returns result category string."""
    catalog_id = track.get("catalog_id", "")
    name = track.get("name", "")
    artist_name = track.get("artist_name", "")
    isrc = track.get("isrc") or ""
    service_source = track.get("service_source", "")

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

    # Stage 0: AcousticBrainz (free, no API calls — from bulk import)
    if isrc and _missing_fields(features):
        try:
            # We need the MBID — try to get it from the MusicBrainz ISRC lookup
            # (the query is already rate-limited and cached)
            from musicmind.engine.enrichment.acousticbrainz import (
                enrich_from_acousticbrainz,
            )

            # Quick MBID lookup from existing data (no API call if cached)
            async with engine.begin() as conn:
                from musicmind.db.schema import isrc_spotify_mapping
                result = await conn.execute(
                    sa.select(isrc_spotify_mapping.c.resolved_via).where(
                        isrc_spotify_mapping.c.isrc == isrc.upper()
                    )
                )
                result.first()
                # If we have an MBID-style resolved_via, try AcousticBrainz
                # Otherwise skip (don't make API calls here)

            # Try AcousticBrainz with ISRC as MBID proxy (some overlap)
            ab_features = await enrich_from_acousticbrainz(
                engine, isrc.upper(), existing_features=features,
            )
            if ab_features:
                for k, v in ab_features.items():
                    if k != "feature_source" and v is not None and features.get(k) is None:
                        features[k] = v
                        feature_source[k] = "acousticbrainz"
                if not _missing_fields(features):
                    enriched_by = "acousticbrainz"
        except Exception:
            pass  # AcousticBrainz is best-effort

    # Stage 1: Deezer (BPM + preview URL)
    # Check if we already have a cached preview URL from a previous enrichment
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

    if not preview_url and (name or isrc):
        try:
            deezer_result = await fetch_deezer_features(
                name=name, artist_name=artist_name, isrc=isrc or None,
            )
            if deezer_result:
                preview_url = deezer_result.pop("preview_url", None)
                deezer_result.pop("deezer_id", None)
                if _merge_features(features, feature_source, deezer_result, "deezer"):
                    enriched_by = "deezer"
                # Save the preview URL to song_metadata_cache for future use
                if preview_url:
                    try:
                        async with engine.begin() as conn:
                            await conn.execute(
                                sa.text(
                                    "UPDATE song_metadata_cache"
                                    " SET preview_url = :url"
                                    " WHERE catalog_id = :cid"
                                    "   AND (preview_url IS NULL OR preview_url = '')"
                                ),
                                {"url": preview_url, "cid": catalog_id},
                            )
                    except Exception:
                        pass
        except Exception:
            logger.debug("Deezer enrichment failed for %s - %s", artist_name, name)

    # Stage 2: ReccoBeats (upload preview)
    if preview_url:
        try:
            audio_bytes = await _download_preview(preview_url)
            if audio_bytes:
                recco_result = await _reccobeats_with_retry(audio_bytes)
                del audio_bytes
                if recco_result:
                    if _merge_features(features, feature_source, recco_result, "reccobeats"):
                        enriched_by = "reccobeats"
        except Exception:
            logger.debug("ReccoBeats enrichment failed for %s", catalog_id)

    # Stage 3: SoundStat (paid, gap-fill)
    if _missing_fields(features) and soundstat_api_key:
        try:
            spotify_id = await _get_spotify_id(engine, track, isrc, service_source)
            if spotify_id:
                ss_result = await fetch_soundstat_features(
                    spotify_id, api_key=soundstat_api_key
                )
                if ss_result:
                    if _merge_features(features, feature_source, ss_result, "soundstat"):
                        enriched_by = "soundstat"
        except Exception:
            logger.debug("SoundStat enrichment failed for %s", catalog_id)

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


async def _reccobeats_with_retry(audio_bytes: bytes, max_retries: int = 3) -> dict[str, Any] | None:
    """Upload to ReccoBeats with Retry-After header handling."""
    import httpx

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                files = {"audioFile": ("preview.mp3", audio_bytes, "audio/mpeg")}
                resp = await client.post(
                    "https://api.reccobeats.com/v1/analysis/audio-features",
                    files=files,
                )
                if resp.status_code == 200:
                    return _parse_reccobeats_response(resp.json())

                if resp.status_code == 429:
                    # Use Retry-After header if present, else short default
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else 2.0
                    logger.info("ReccoBeats 429, waiting %.1fs (Retry-After)", wait)
                    await asyncio.sleep(wait)
                    continue

                return None
        except httpx.ReadTimeout:
            logger.debug("ReccoBeats timeout, attempt %d/%d", attempt + 1, max_retries)
            continue
        except Exception:
            return None

    return None


def _parse_reccobeats_response(data: dict[str, Any]) -> dict[str, Any] | None:
    """Parse ReccoBeats response into our schema."""
    result: dict[str, Any] = {}
    if data.get("tempo") and data["tempo"] > 0:
        result["tempo"] = round(float(data["tempo"]), 2)
    if data.get("energy") is not None:
        result["energy"] = round(float(data["energy"]), 4)
    if data.get("danceability") is not None:
        result["danceability"] = round(float(data["danceability"]), 4)
    if data.get("acousticness") is not None:
        result["acousticness"] = round(float(data["acousticness"]), 4)
    if data.get("valence") is not None:
        result["valence_proxy"] = round(float(data["valence"]), 4)
    if data.get("instrumentalness") is not None:
        result["instrumentalness"] = round(float(data["instrumentalness"]), 4)
    if data.get("loudness") is not None:
        raw = float(data["loudness"])
        result["loudness"] = round(max(0.0, min(1.0, (raw + 60) / 60)), 4)
    return result if result else None


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
            features: dict[str, Any] = {}
            for field in ENRICHABLE_FIELDS:
                val = getattr(row, field, None)
                if val is not None:
                    features[field] = val
            if features:
                af_map[row.catalog_id] = features
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


async def _get_spotify_id(
    engine: Any, track: dict[str, Any], isrc: str, service_source: str,
) -> str | None:
    if service_source == "spotify":
        return track.get("catalog_id")
    if isrc:
        return await resolve_spotify_id(isrc, engine=engine)
    return None
