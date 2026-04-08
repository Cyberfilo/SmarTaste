"""AcousticBrainz lookup — free audio features from CC0 bulk import.

No API calls needed. Data is pre-imported from the AcousticBrainz dump
into the acousticbrainz_cache table. This module looks up features
by MusicBrainz recording ID (MBID).

To get the MBID: ISRC → MusicBrainz recording search → MBID.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import acousticbrainz_cache

logger = logging.getLogger(__name__)


async def lookup_acousticbrainz(
    engine: Any,
    mbid: str,
) -> dict[str, Any] | None:
    """Look up AcousticBrainz features by MusicBrainz recording ID.

    Returns dict with mood probabilities, danceability, genre predictions,
    or None if not in the cache.
    """
    if not mbid:
        return None

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(acousticbrainz_cache).where(
                acousticbrainz_cache.c.mbid == mbid
            )
        )
        row = result.first()

    if row is None:
        return None

    return {
        "mood_aggressive": row.mood_aggressive,
        "mood_happy": row.mood_happy,
        "mood_relaxed": row.mood_relaxed,
        "mood_sad": row.mood_sad,
        "mood_party": row.mood_party,
        "mood_electronic": row.mood_electronic,
        "mood_acoustic": row.mood_acoustic,
        "danceability": row.danceability,
        "gender": row.gender,
        "voice_instrumental": row.voice_instrumental,
        "genre_probabilities": row.genre_probabilities or {},
        "average_loudness": row.average_loudness,
        "bpm": row.bpm,
        "key": row.key,
        "scale": row.scale,
    }


async def enrich_from_acousticbrainz(
    engine: Any,
    mbid: str,
    *,
    existing_features: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Fill gaps in existing audio features from AcousticBrainz data.

    Only fills fields that are None in the existing features.
    Returns merged features dict, or None if no AcousticBrainz data.
    """
    ab = await lookup_acousticbrainz(engine, mbid)
    if not ab:
        return None

    features = dict(existing_features) if existing_features else {}

    # Map AcousticBrainz fields to our audio features schema
    field_mapping = {
        "danceability": "danceability",
        "bpm": "tempo",
        "average_loudness": "loudness",
        "key": "key",
        "scale": "scale",
    }

    for ab_key, our_key in field_mapping.items():
        if features.get(our_key) is None and ab.get(ab_key) is not None:
            features[our_key] = ab[ab_key]

    # Derive acousticness from mood_acoustic probability
    if features.get("acousticness") is None and ab.get("mood_acoustic") is not None:
        features["acousticness"] = ab["mood_acoustic"]

    # Derive valence_proxy from mood_happy
    if features.get("valence_proxy") is None and ab.get("mood_happy") is not None:
        features["valence_proxy"] = ab["mood_happy"]

    # Derive energy from inverse of mood_relaxed
    if features.get("energy") is None and ab.get("mood_relaxed") is not None:
        features["energy"] = round(1.0 - ab["mood_relaxed"], 3)

    return features
