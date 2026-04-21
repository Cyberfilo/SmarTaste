"""Semantic music search via CLAP text-to-audio similarity.

Encodes a text query to CLAP 512-dim embedding, then finds the most
similar songs in the user's catalog by cosine similarity.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import sqlalchemy as sa

logger = logging.getLogger(__name__)


class SearchService:
    """CLAP-powered semantic music search."""

    async def semantic_search(
        self,
        engine,
        settings,
        *,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search user's catalog using natural language.

        1. Encode query text to CLAP 512-dim embedding via Modal GPU
        2. Load all CLAP embeddings for user's songs
        3. Cosine similarity rank → return top N with scores
        """
        from musicmind.engine.enrichment.gpu_client import encode_text_via_gpu

        if not settings.modal_endpoint_url:
            return []

        # Encode query text
        query_embedding = await encode_text_via_gpu(
            query, settings.modal_endpoint_url,
        )
        if not query_embedding:
            return []

        # Load all user song CLAP embeddings
        from musicmind.db.schema import audio_embeddings, song_metadata_cache

        songs_with_clap: list[tuple[str, list[float], dict]] = []
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    audio_embeddings.c.catalog_id,
                    audio_embeddings.c.clap_embedding,
                ).where(
                    sa.and_(
                        audio_embeddings.c.user_id == user_id,
                        audio_embeddings.c.clap_embedding.isnot(None),
                    )
                )
            )
            for row in result:
                emb = row.clap_embedding
                if isinstance(emb, str):
                    emb = json.loads(emb)
                if emb and len(emb) > 0:
                    songs_with_clap.append((row.catalog_id, emb, {}))

        if not songs_with_clap:
            return []

        # Cosine similarity
        query_vec = np.array(query_embedding)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []
        query_vec = query_vec / query_norm

        scored: list[tuple[str, float]] = []
        for catalog_id, emb, _ in songs_with_clap:
            song_vec = np.array(emb)
            norm = np.linalg.norm(song_vec)
            if norm == 0:
                continue
            sim = float(np.dot(query_vec, song_vec / norm))
            scored.append((catalog_id, sim))

        scored.sort(key=lambda x: -x[1])
        top = scored[:limit]

        # Fetch song metadata for top results
        top_ids = [cid for cid, _ in top]

        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    song_metadata_cache.c.catalog_id,
                    song_metadata_cache.c.name,
                    song_metadata_cache.c.artist_name,
                    song_metadata_cache.c.album_name,
                    song_metadata_cache.c.genre_names,
                    song_metadata_cache.c.ai_caption,
                ).where(
                    sa.and_(
                        song_metadata_cache.c.catalog_id.in_(top_ids),
                        song_metadata_cache.c.user_id == user_id,
                    )
                )
            )
            meta_map = {}
            for row in result:
                genres = row.genre_names
                if isinstance(genres, str):
                    try:
                        genres = json.loads(genres)
                    except (json.JSONDecodeError, TypeError):
                        genres = []
                meta_map[row.catalog_id] = {
                    "catalog_id": row.catalog_id,
                    "name": row.name or "",
                    "artist_name": row.artist_name or "",
                    "album_name": row.album_name or "",
                    "genre_names": genres,
                    "ai_caption": row.ai_caption,
                }

        results = []
        for cid, score in top:
            meta = meta_map.get(cid, {"catalog_id": cid})
            meta["search_score"] = round(score, 3)
            results.append(meta)

        return results
