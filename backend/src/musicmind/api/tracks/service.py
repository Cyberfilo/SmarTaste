"""Track detail service -- audio feature lookups from cache."""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import audio_features_cache

logger = logging.getLogger(__name__)


class TrackService:
    """Service for per-track detail views.

    Stateless class -- all state passed as parameters.
    """

    async def get_audio_features(
        self,
        engine,
        *,
        user_id: str,
        catalog_id: str,
    ) -> dict[str, Any] | None:
        """Look up audio features for a track from the cache.

        Args:
            engine: SQLAlchemy async engine.
            user_id: MusicMind user ID (for access scoping).
            catalog_id: Catalog ID of the track.

        Returns:
            Dict with audio feature fields, or None if not cached.
        """
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(audio_features_cache).where(
                    audio_features_cache.c.catalog_id == catalog_id,
                )
            )
            row = result.first()

        if row is None:
            return None

        return {
            "catalog_id": row.catalog_id,
            "energy": row.energy,
            "danceability": row.danceability,
            "valence": row.valence_proxy,
            "acousticness": row.acousticness,
            "tempo": row.tempo,
            "instrumentalness": None,  # Not in DB -- placeholder for API contract
            "beat_strength": row.beat_strength,
            "brightness": row.brightness,
        }
