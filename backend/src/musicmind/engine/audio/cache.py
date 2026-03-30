"""Audio feature cache — ISRC-keyed storage for extracted features + embeddings.

Wraps the audio_features_cache and a new audio_embeddings table to provide
deduplication via ISRC. Two tracks with the same ISRC share analysis results.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import audio_features_cache
from musicmind.engine.audio.models import AudioEmbedding, ExtractedFeatures

logger = logging.getLogger(__name__)


async def get_cached_features(
    engine,
    *,
    catalog_id: str,
    user_id: str,
) -> ExtractedFeatures | None:
    """Load cached audio features for a track.

    Args:
        engine: SQLAlchemy async engine.
        catalog_id: Track catalog ID.
        user_id: User ID for multi-tenant isolation.

    Returns:
        ExtractedFeatures or None if not cached.
    """
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

    return ExtractedFeatures(
        tempo=row.tempo,
        energy=row.energy,
        brightness=row.brightness,
        danceability=row.danceability,
        acousticness=row.acousticness,
        valence=row.valence_proxy,
        beat_strength=row.beat_strength,
    )


async def store_features(
    engine,
    *,
    catalog_id: str,
    user_id: str,
    features: ExtractedFeatures,
) -> None:
    """Store extracted features to the audio_features_cache table.

    Uses INSERT ... ON CONFLICT to upsert.

    Args:
        engine: SQLAlchemy async engine.
        catalog_id: Track catalog ID.
        user_id: User ID.
        features: Extracted features to store.
    """
    now = datetime.now(UTC)
    scalar = features.to_scalar_dict()

    async with engine.begin() as conn:
        # Try insert, update on conflict
        existing = await conn.execute(
            sa.select(audio_features_cache.c.catalog_id).where(
                sa.and_(
                    audio_features_cache.c.catalog_id == catalog_id,
                    audio_features_cache.c.user_id == user_id,
                )
            )
        )
        if existing.first():
            await conn.execute(
                audio_features_cache.update()
                .where(
                    sa.and_(
                        audio_features_cache.c.catalog_id == catalog_id,
                        audio_features_cache.c.user_id == user_id,
                    )
                )
                .values(
                    tempo=scalar.get("tempo"),
                    energy=scalar.get("energy"),
                    brightness=scalar.get("brightness"),
                    danceability=scalar.get("danceability"),
                    acousticness=scalar.get("acousticness"),
                    valence_proxy=scalar.get("valence_proxy"),
                    beat_strength=scalar.get("beat_strength"),
                    analyzed_at=now,
                )
            )
        else:
            await conn.execute(
                audio_features_cache.insert().values(
                    catalog_id=catalog_id,
                    user_id=user_id,
                    tempo=scalar.get("tempo"),
                    energy=scalar.get("energy"),
                    brightness=scalar.get("brightness"),
                    danceability=scalar.get("danceability"),
                    acousticness=scalar.get("acousticness"),
                    valence_proxy=scalar.get("valence_proxy"),
                    beat_strength=scalar.get("beat_strength"),
                    analyzed_at=now,
                )
            )

    logger.info("Stored audio features for %s", catalog_id)


async def get_cached_embedding(
    engine,
    *,
    catalog_id: str,
    user_id: str,
) -> AudioEmbedding | None:
    """Load a cached audio embedding for a track.

    Embeddings are stored in the audio_embeddings table.
    """
    from musicmind.db.schema import audio_embeddings

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(audio_embeddings).where(
                sa.and_(
                    audio_embeddings.c.catalog_id == catalog_id,
                    audio_embeddings.c.user_id == user_id,
                )
            )
        )
        row = result.first()

    if row is None:
        return None

    vector = row.embedding
    if isinstance(vector, str):
        vector = json.loads(vector)

    return AudioEmbedding(
        catalog_id=catalog_id,
        isrc=row.isrc,
        vector=vector or [],
        model_version=row.model_version or "discogs-effnet-bs64",
    )


async def store_embedding(
    engine,
    *,
    catalog_id: str,
    user_id: str,
    embedding: AudioEmbedding,
) -> None:
    """Store an audio embedding."""
    from musicmind.db.schema import audio_embeddings

    now = datetime.now(UTC)

    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(audio_embeddings.c.catalog_id).where(
                sa.and_(
                    audio_embeddings.c.catalog_id == catalog_id,
                    audio_embeddings.c.user_id == user_id,
                )
            )
        )
        if existing.first():
            await conn.execute(
                audio_embeddings.update()
                .where(
                    sa.and_(
                        audio_embeddings.c.catalog_id == catalog_id,
                        audio_embeddings.c.user_id == user_id,
                    )
                )
                .values(
                    embedding=json.dumps(embedding.vector),
                    isrc=embedding.isrc,
                    model_version=embedding.model_version,
                    analyzed_at=now,
                )
            )
        else:
            await conn.execute(
                audio_embeddings.insert().values(
                    catalog_id=catalog_id,
                    user_id=user_id,
                    embedding=json.dumps(embedding.vector),
                    isrc=embedding.isrc,
                    model_version=embedding.model_version,
                    analyzed_at=now,
                )
            )

    logger.info("Stored audio embedding for %s", catalog_id)
