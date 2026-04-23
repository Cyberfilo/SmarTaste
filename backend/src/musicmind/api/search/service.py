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
    """CLAP-powered semantic music search + text autocomplete (V 6.420)."""

    async def songs_autocomplete(
        self,
        engine,
        *,
        query: str,
        limit: int = 10,
        service: str | None = None,
    ) -> list[dict[str, Any]]:
        """Text autocomplete over global_song_cache with joined enrichment.

        Used by the playlist-chat song-mention dropdown. Returns trimmed
        payload optimized for inline rendering: artwork + enrichment tags.

        Args:
            engine: SQLAlchemy async engine.
            query: Raw user text (>=1 char).
            limit: Max rows (1-25).
            service: Optional 'apple_music' or 'spotify' filter.
        """
        q = query.strip()
        if not q:
            return []

        # ILIKE patterns for fuzzy match. We rank in SQL via a CASE
        # expression so the top-N are the relevant ones, not arbitrary.
        pattern = f"%{q}%"
        prefix = f"{q}%"

        service_clause = ""
        params: dict[str, Any] = {
            "q": q.lower(),
            "prefix": prefix.lower(),
            "pattern": pattern.lower(),
            "lim": limit,
        }
        if service:
            service_clause = "AND g.service_source = :service"
            params["service"] = service

        sql = sa.text(f"""
            SELECT
                g.catalog_id, g.isrc, g.name, g.artist_name,
                g.album_name, g.genre_names, g.artwork_url,
                g.preview_url, g.service_source, g.mood_tags,
                afg.tempo, afg.energy, afg.valence_proxy, afg.danceability
            FROM global_song_cache g
            LEFT JOIN audio_features_global afg ON afg.isrc = g.isrc
            WHERE (
                LOWER(g.name) ILIKE :pattern
                OR LOWER(g.artist_name) ILIKE :pattern
            )
            AND g.name IS NOT NULL AND g.name <> ''
            AND g.artist_name IS NOT NULL AND g.artist_name <> ''
            {service_clause}
            ORDER BY
                CASE
                    WHEN LOWER(g.name) = :q THEN 1
                    WHEN LOWER(g.name) ILIKE :prefix THEN 2
                    WHEN LOWER(g.artist_name) = :q THEN 3
                    WHEN LOWER(g.artist_name) ILIKE :prefix THEN 4
                    WHEN LOWER(g.name) ILIKE :pattern THEN 5
                    ELSE 6
                END,
                g.fetched_at DESC
            LIMIT :lim
        """)

        async with engine.begin() as conn:
            rows = (await conn.execute(sql, params)).fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            genres = r.genre_names
            if isinstance(genres, str):
                try:
                    genres = json.loads(genres)
                except (json.JSONDecodeError, TypeError):
                    genres = []
            moods = r.mood_tags
            if isinstance(moods, str):
                try:
                    moods = json.loads(moods)
                except (json.JSONDecodeError, TypeError):
                    moods = []
            out.append({
                "catalog_id": r.catalog_id,
                "isrc": r.isrc,
                "name": r.name,
                "artist_name": r.artist_name,
                "album_name": r.album_name or "",
                "genre_names": genres or [],
                "artwork_url": r.artwork_url or "",
                "preview_url": r.preview_url or "",
                "service_source": r.service_source or "",
                "enrichment": {
                    "mood_tags": moods or [],
                    "tempo": float(r.tempo) if r.tempo is not None else None,
                    "energy": float(r.energy) if r.energy is not None else None,
                    "valence": (
                        float(r.valence_proxy)
                        if r.valence_proxy is not None else None
                    ),
                    "danceability": (
                        float(r.danceability)
                        if r.danceability is not None else None
                    ),
                },
            })
        return out

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
