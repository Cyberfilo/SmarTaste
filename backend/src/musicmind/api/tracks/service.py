"""Track detail service -- audio feature lookups and on-demand analysis."""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import audio_features_cache, song_metadata_cache

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
            user_id: SmarTaste user ID (for access scoping).
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
            "instrumentalness": getattr(row, "instrumentalness", None),
            "beat_strength": row.beat_strength,
            "brightness": row.brightness,
        }

    async def analyze_seeds(
        self,
        engine,
        *,
        user_id: str,
        catalog_ids: list[str],
    ) -> dict[str, Any]:
        """Trigger on-demand Essentia analysis for seed tracks.

        Looks up track metadata from song_metadata_cache, then runs
        the Essentia analyzer on each track's preview clip.

        Args:
            engine: SQLAlchemy async engine.
            user_id: SmarTaste user ID.
            catalog_ids: List of catalog IDs to analyze (max 10).

        Returns:
            Dict with results list, analyzed/cached/total counts, essentia flag.
        """
        from musicmind.engine.audio.analyzer import ESSENTIA_AVAILABLE, analyze_seeds

        # Look up track metadata
        tracks: list[dict[str, Any]] = []
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(song_metadata_cache).where(
                    sa.and_(
                        song_metadata_cache.c.catalog_id.in_(catalog_ids),
                        song_metadata_cache.c.user_id == user_id,
                    )
                )
            )
            for row in result:
                genres = row.genre_names
                if isinstance(genres, str):
                    try:
                        genres = json.loads(genres)
                    except (json.JSONDecodeError, TypeError):
                        genres = []
                tracks.append({
                    "catalog_id": row.catalog_id,
                    "name": row.name,
                    "artist_name": row.artist_name,
                    "isrc": row.isrc,
                    "preview_url": row.preview_url,
                })

        # For IDs not in cache, create stubs
        cached_ids = {t["catalog_id"] for t in tracks}
        for cid in catalog_ids:
            if cid not in cached_ids:
                tracks.append({
                    "catalog_id": cid,
                    "name": "Unknown",
                    "artist_name": "",
                    "isrc": "",
                    "preview_url": "",
                })

        results = await analyze_seeds(engine, tracks, user_id=user_id)

        analyzed = sum(1 for r in results if r["status"] == "analyzed")
        cached = sum(1 for r in results if r["status"] == "cached")

        return {
            "results": results,
            "analyzed": analyzed,
            "cached": cached,
            "total": len(results),
            "essentia_available": ESSENTIA_AVAILABLE,
        }
