"""Recommendation pipeline: profile -> discovery -> scoring -> feedback.

Orchestrates the full recommendation flow: builds taste profile, runs discovery
strategies, deduplicates and scores candidates, applies mood filtering, and
generates natural-language explanations.

Supports unified cross-service recommendations when both Spotify and Apple Music
are connected, running discovery strategies against both catalogs in parallel.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import sqlalchemy as sa

from musicmind.api.recommendations.fetch import (
    discover_chart_filter,
    discover_editorial,
    discover_genre_adjacent,
    discover_similar_artists,
)
from musicmind.api.services.service import (
    detect_apple_music_storefront,
    generate_apple_developer_token,
    get_user_connections,
    refresh_spotify_token,
    upsert_service_connection,
)
from musicmind.api.taste.service import TasteService
from musicmind.db.schema import (
    service_connections,
    song_metadata_cache,
    taste_profile_snapshots,
)
from musicmind.engine.dedup import deduplicate_tracks
from musicmind.engine.genres import normalize_genre_list
from musicmind.engine.mood import MOOD_PROFILES, filter_candidates_by_mood
from musicmind.engine.scorer import rank_candidates, score_candidate
from musicmind.engine.weights import (
    DEFAULT_WEIGHTS,
)
from musicmind.security.encryption import EncryptionService

logger = logging.getLogger(__name__)

# ── Mood alias mapping ────────────────────────────────────────────────────────

MOOD_ALIAS: dict[str, str] = {
    "energy": "workout",
    "melancholy": "sad",
}

# Valid mood keywords: MOOD_PROFILES keys + alias keys
VALID_MOODS: set[str] = set(MOOD_PROFILES.keys()) | set(MOOD_ALIAS.keys())

# ── Strategy constants ────────────────────────────────────────────────────────

VALID_STRATEGIES = {"all", "similar_artist", "genre_adjacent", "editorial", "chart"}

# ── Explanation labels ────────────────────────────────────────────────────────

DIMENSION_LABELS: dict[str, str] = {
    "language_match": "language/region match",
    "audio_similarity": "audio similarity",
    "genre_match": "genre match",
    "artist_match": "artist affinity",
}

_taste_service = TasteService()


def _build_explanation(breakdown: dict[str, float]) -> str:
    """Build a human-readable explanation from a score breakdown.

    Takes the top 2-3 positive dimensions (excluding penalties and bonuses)
    and formats them with qualifiers based on score magnitude.
    """
    # Dimensions to exclude from explanation
    exclude = {
        "diversity_penalty", "staleness", "cross_strategy_bonus", "mood_boost",
    }

    parts: list[str] = []
    for key, score in sorted(breakdown.items(), key=lambda x: x[1], reverse=True):
        if key in exclude:
            continue
        label = DIMENSION_LABELS.get(key, key.replace("_", " "))
        if score > 0.6:
            parts.append(f"Strong {label} ({score:.0%})")
        elif score > 0.3:
            parts.append(f"Good {label} ({score:.0%})")

    if not parts:
        return "Moderate match across multiple factors"

    return ". ".join(parts[:3])


class RecommendationService:
    """Orchestrates the full recommendation pipeline.

    Pipeline: taste profile -> discovery strategies -> dedup -> adaptive weights
    -> mood filter -> score/rank -> explain.

    Stateless class -- all state passed as parameters.
    """

    async def get_recommendations(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        strategy: str = "all",
        mood: str | None = None,
        limit: int = 10,
        mode: str = "all",
    ) -> dict[str, Any]:
        """Get personalized recommendations for a user.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for token decryption.
            settings: Application settings.
            user_id: SmarTaste user ID.
            strategy: Discovery strategy ("all", "similar_artist",
                "genre_adjacent", "editorial", "chart").
            mood: Optional mood filter (e.g. "chill", "workout", "energy").
            limit: Max recommendations to return (1-50).

        Returns:
            Dict with items, strategy, mood, total, weights_adapted keys.

        Raises:
            ValueError: If no connected service or invalid mood.
        """
        # Step 1: Get taste profile via TasteService
        profile = await _taste_service.get_profile(
            engine, encryption, settings, user_id=user_id,
        )

        # Step 2: Resolve service + credentials (only used for cold-start fallback)
        all_creds: list[tuple[str, str, str | None, str]] = []
        try:
            all_creds = await self._resolve_all_credentials(
                engine, encryption, settings, user_id=user_id,
            )
        except ValueError:
            # No connected service — DB candidates may still exist from a
            # previously connected service; let the read attempt below decide.
            pass

        # Step 3: Extract seed data from profile (still needed for weights/mood)
        top_artists_raw = profile.get("top_artists", [])
        seed_scored: list[tuple[str, float]] = [
            (a["name"], float(a.get("score", 0.0)))
            for a in top_artists_raw[:5]
            if isinstance(a, dict) and a.get("name")
        ]
        seed_artist_names = [n for n, _ in seed_scored]
        genre_vector = profile.get("genre_vector", {})
        top_genres = sorted(
            genre_vector.items(), key=lambda x: x[1], reverse=True,
        )[:5]
        top_genre_names = [g[0] for g in top_genres]

        # Step 4: Read candidates from DB (worker-populated)
        candidates = await self._load_candidates_from_db(
            engine, user_id=user_id, strategy=strategy,
        )

        # Step 4.5: Mode-specific filter + augment (V 6.378)
        # mode = "your_artists" → tracks where ANY artist (primary or feat)
        #        is a library artist. Augmented from global_song_cache with
        #        unowned discography tracks for library primaries.
        # mode = "discover"     → tracks where PRIMARY is NOT a library
        #        artist. Feat-library candidates remain (the 0.3× feat
        #        weight in V 6.377 scorer gives them their boost).
        # mode = "all" (default) → no filtering.
        if mode in ("your_artists", "discover"):
            from musicmind.db.schema import song_metadata_cache as _smc
            from musicmind.engine.profile import parse_artists as _parse

            async with engine.begin() as _conn:
                _res = await _conn.execute(
                    sa.select(sa.distinct(_smc.c.artist_name)).where(
                        sa.and_(
                            _smc.c.user_id == user_id,
                            sa.or_(
                                _smc.c.library_id.isnot(None),
                                _smc.c.date_added_to_library.isnot(None),
                            ),
                        )
                    )
                )
                lib_names: set[str] = set()
                for _row in _res:
                    if _row[0]:
                        for _n, _w in _parse(_row[0]):
                            if _w >= 1.0:
                                lib_names.add(_n.lower())

            def _primary_lower(c: dict[str, Any]) -> str:
                _p = _parse(c.get("artist_name", ""))
                return _p[0][0].lower() if _p else ""

            def _any_in_library(c: dict[str, Any]) -> bool:
                for _n, _w in _parse(c.get("artist_name", "")):
                    if _n.lower() in lib_names:
                        return True
                return False

            if mode == "your_artists":
                candidates = [c for c in candidates if _any_in_library(c)]
                # Augment with library-primary unowned discography tracks
                if lib_names:
                    async with engine.begin() as _conn:
                        _r = await _conn.execute(sa.text("""
                            SELECT g.catalog_id, g.name, g.artist_name,
                                   g.album_name, g.genre_names, g.isrc,
                                   g.preview_url, g.artwork_url,
                                   g.service_source, g.duration_ms,
                                   g.release_date
                            FROM global_song_cache g
                            WHERE LOWER(g.artist_name) = ANY(:names)
                              AND NOT EXISTS (
                                SELECT 1 FROM song_metadata_cache smc
                                WHERE smc.user_id = :uid
                                  AND (smc.library_id IS NOT NULL
                                       OR smc.date_added_to_library IS NOT NULL)
                                  AND (smc.catalog_id = g.catalog_id
                                       OR (smc.isrc IS NOT NULL
                                           AND smc.isrc <> ''
                                           AND smc.isrc = g.isrc))
                              )
                            LIMIT 300
                        """), {"names": list(lib_names), "uid": user_id})
                        _existing = {c.get("catalog_id") for c in candidates}
                        for _row in _r:
                            if _row.catalog_id in _existing:
                                continue
                            _gn = _row.genre_names
                            if isinstance(_gn, str):
                                try:
                                    _gn = json.loads(_gn)
                                except Exception:
                                    _gn = []
                            candidates.append({
                                "catalog_id": _row.catalog_id,
                                "name": _row.name or "",
                                "artist_name": _row.artist_name or "",
                                "album_name": _row.album_name or "",
                                "genre_names": _gn or [],
                                "isrc": _row.isrc,
                                "preview_url": _row.preview_url or "",
                                "artwork_url": _row.artwork_url or "",
                                "service_source": _row.service_source or "",
                                "duration_ms": _row.duration_ms,
                                "release_date": _row.release_date,
                                "_strategy_source": "your_artists",
                                "_discovery_weight": 0.8,
                            })
            elif mode == "discover":
                candidates = [
                    c for c in candidates
                    if _primary_lower(c) not in lib_names
                ]

        # Hard-fail when there's nothing to recommend AND no creds to fall back
        # on (test contract: connecting a service is a precondition).
        if not candidates and not all_creds:
            raise ValueError("No connected service found")

        # Cold-start: the cobweb is now the single source of recommendations
        # (per V 6.351 "everything-through-cobweb" directive). Running the
        # four live strategies here re-introduced editorial / chart / K-pop
        # pollution into recommendation_candidates every time a new user
        # opened the app before the worker cycled. Instead, kick off a
        # background populate and return whatever the worker has already
        # written — empty is fine, the UI handles a zero-candidate state.
        if not candidates and all_creds:
            logger.info(
                "Recommendations cold-start for user %s — no cobweb candidates "
                "yet, firing background populate and returning empty",
                user_id[:8],
            )
            asyncio.create_task(
                self._populate_candidates_background(
                    engine, settings, user_id=user_id,
                )
            )

        # Step 5: Deduplicate candidates across services and strategies
        unique = self._deduplicate_candidates(candidates)

        # Apply cross-service dedup via ISRC + fuzzy match if multiple services
        if len(all_creds) > 1:
            unique = deduplicate_tracks(unique)
            # Normalize genres on deduplicated candidates
            for c in unique:
                genres = c.get("genre_names") or []
                if isinstance(genres, str):
                    genres = [genres]
                c["genre_names"] = normalize_genre_list(genres)

        # Step 5b: Exclude songs already in user's library (unless added >2 years ago)
        unique = await self._filter_library_songs(
            engine, unique, user_id=user_id,
        )

        # Step 6: Load calibration artist weights
        calibration_artists = await self._load_calibration_artists(
            engine, user_id=user_id,
        )

        user_audio_centroid = profile.get("audio_centroid") or None
        user_clap_centroid = profile.get("clap_centroid") or None
        user_mert_centroid = profile.get("mert_centroid") or None
        user_embedding_centroid = profile.get("embedding_centroid") or None

        # Step 6b: Load Last.fm tag profile + collaborative matches
        user_tag_profile, collaborative_matches = await self._load_lastfm_data(
            engine, user_id=user_id,
        )

        # Step 7: Apply mood filter
        resolved_mood: str | None = None
        if mood:
            mapped = MOOD_ALIAS.get(mood, mood)
            if mapped not in MOOD_PROFILES:
                available = ", ".join(sorted(VALID_MOODS))
                msg = f"Unknown mood '{mood}'. Available: {available}"
                raise ValueError(msg)
            unique = filter_candidates_by_mood(unique, mapped)
            resolved_mood = mood

        # Step 8: Compute context-adaptive weights
        # Weights shift based on user's profile (regional concentration, etc.)
        from musicmind.engine.weights import compute_context_weights

        weights = compute_context_weights(
            profile,
            has_audio=bool(user_audio_centroid),
            has_calibration=calibration_artists is not None,
            mood_active=resolved_mood is not None,
        )

        # Layer feedback-learned weights on top if enough data
        feedback_weights, weights_adapted = await self._load_adaptive_weights(
            engine, user_id=user_id,
        )
        if weights_adapted:
            # Blend: 60% context-adaptive + 40% feedback-learned
            for k in weights:
                if k in feedback_weights:
                    weights[k] = 0.6 * weights[k] + 0.4 * feedback_weights[k]
            total = sum(weights.values())
            weights = {k: round(v / total, 4) for k, v in weights.items()}

        # ── Single-pass scoring (embeddings pre-loaded) ──────
        # Load audio features from cache only — NEVER run Essentia at
        # recommendation time. Essentia is ~1-2s CPU-bound per track and
        # blocks the event loop (V 6.384 regression: max_to_enrich=200 →
        # 5-minute stalls → Railway 502). Enrichment is the worker's job;
        # if a candidate is missing features, scoring degrades gracefully
        # via compute_context_weights.
        audio_features_map = await self._load_audio_features(
            engine, unique, user_id=user_id,
        )

        clap_map, mert_map, embedding_map = await self._load_embeddings(
            engine, unique, user_id=user_id,
        )

        # Load user library CLAP embeddings for contextual explanations
        user_library_claps = await self._load_user_library_claps(
            engine, user_id=user_id,
        )

        ranked = rank_candidates(
            unique, profile, count=limit, weights=weights,
            audio_features_map=audio_features_map,
            user_audio_centroid=user_audio_centroid,
            embedding_map=embedding_map or None,
            user_embedding_centroid=user_embedding_centroid,
            clap_map=clap_map or None,
            user_clap_centroid=user_clap_centroid,
            mert_map=mert_map or None,
            user_mert_centroid=user_mert_centroid,
            calibration_artists=calibration_artists,
            user_library_claps=user_library_claps or None,
        )

        # Step 9: Build response
        items = []
        for result in ranked:
            items.append({
                "catalog_id": result.get("catalog_id", ""),
                "name": result.get("name", ""),
                "artist_name": result.get("artist_name", ""),
                "album_name": result.get("album_name", ""),
                "artwork_url": result.get("artwork_url_template", ""),
                "preview_url": result.get("preview_url", ""),
                "score": result.get("_score", 0.0),
                "explanation": result.get("_explanation", ""),
                "strategy_source": result.get(
                    "_strategy_source", strategy,
                ),
                "genre_names": result.get("genre_names", []),
                "mood_tags": result.get("mood_tags", []),
            })

        return {
            "items": items,
            "strategy": strategy,
            "mood": resolved_mood,
            "total": len(items),
            "weights_adapted": weights_adapted,
        }

    async def get_scoring_breakdown(
        self,
        engine,
        encryption,
        settings,
        *,
        user_id: str,
        catalog_id: str,
    ) -> dict[str, Any]:
        """Score breakdown aligned with the 6-dimension embedding pipeline.

        Reports the six weighted dimensions (CLAP/MERT/EffNet/genre/scalar/
        artist) plus additive bonuses (discovery, cross-strategy, mood,
        calibration) and penalties (diversity, staleness). Looks the song up
        in song_metadata_cache first, falling back to global_song_cache so
        discovery candidates also get breakdowns.
        """
        from musicmind.db.schema import (
            audio_embeddings,
            audio_embeddings_global,
            audio_features_cache,
            audio_features_global,
            global_song_cache,
        )
        from musicmind.engine.weights import compute_context_weights

        # ── Load song metadata (per-user first, global fallback) ────────
        async with engine.begin() as conn:
            song_row = (await conn.execute(
                sa.select(
                    song_metadata_cache.c.catalog_id,
                    song_metadata_cache.c.name,
                    song_metadata_cache.c.artist_name,
                    song_metadata_cache.c.genre_names,
                    song_metadata_cache.c.release_date,
                    song_metadata_cache.c.isrc,
                ).where(
                    sa.and_(
                        song_metadata_cache.c.catalog_id == catalog_id,
                        song_metadata_cache.c.user_id == user_id,
                    )
                )
            )).first()
            source = "per_user"

            if song_row is None:
                song_row = (await conn.execute(
                    sa.select(
                        global_song_cache.c.catalog_id,
                        global_song_cache.c.name,
                        global_song_cache.c.artist_name,
                        global_song_cache.c.genre_names,
                        global_song_cache.c.release_date,
                        global_song_cache.c.isrc,
                    ).where(global_song_cache.c.catalog_id == catalog_id)
                )).first()
                source = "global"

            if song_row is None:
                raise ValueError("Track not found in catalog")

            # ── Load latest taste profile ─────────────────────────────
            profile_row = (await conn.execute(
                sa.select(taste_profile_snapshots)
                .where(taste_profile_snapshots.c.user_id == user_id)
                .order_by(taste_profile_snapshots.c.computed_at.desc())
                .limit(1)
            )).first()

            if profile_row is None:
                raise ValueError("No taste profile available")

            # ── Load embeddings + audio features for this track ───────
            af_row = None
            emb_row = None
            if source == "per_user":
                af_row = (await conn.execute(
                    sa.select(audio_features_cache).where(
                        sa.and_(
                            audio_features_cache.c.catalog_id == catalog_id,
                            audio_features_cache.c.user_id == user_id,
                        )
                    )
                )).first()
                emb_row = (await conn.execute(
                    sa.select(audio_embeddings).where(
                        sa.and_(
                            audio_embeddings.c.catalog_id == catalog_id,
                            audio_embeddings.c.user_id == user_id,
                        )
                    )
                )).first()

            # Fallback to global by ISRC
            if (af_row is None or emb_row is None) and song_row.isrc:
                if af_row is None:
                    af_row = (await conn.execute(
                        sa.select(audio_features_global).where(
                            audio_features_global.c.isrc == song_row.isrc
                        )
                    )).first()
                if emb_row is None:
                    emb_row = (await conn.execute(
                        sa.select(audio_embeddings_global).where(
                            audio_embeddings_global.c.isrc == song_row.isrc
                        )
                    )).first()

        # ── Parse JSON columns ──────────────────────────────────────
        def _parse_json(val: Any, default: Any) -> Any:
            if val is None:
                return default
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    return default
            return val

        genre_names = _parse_json(song_row.genre_names, [])
        if isinstance(genre_names, str):
            genre_names = [genre_names]

        song_dict: dict[str, Any] = {
            "catalog_id": song_row.catalog_id,
            "name": song_row.name,
            "artist_name": song_row.artist_name,
            "genre_names": genre_names,
            "release_date": song_row.release_date,
            "isrc": song_row.isrc,
        }

        profile_dict: dict[str, Any] = {
            "genre_vector": _parse_json(profile_row.genre_vector, {}),
            "top_artists": _parse_json(profile_row.top_artists, []),
            "release_year_distribution": _parse_json(
                profile_row.release_year_distribution, {},
            ),
            "familiarity_score": profile_row.familiarity_score or 0.0,
            "audio_centroid": _parse_json(profile_row.audio_centroid, {}),
            "embedding_centroid": _parse_json(profile_row.embedding_centroid, None),
            "clap_centroid": _parse_json(profile_row.clap_centroid, None),
            "mert_centroid": _parse_json(profile_row.mert_centroid, None),
        }

        # Candidate embeddings + features
        candidate_clap = None
        candidate_mert = None
        candidate_effnet = None
        audio_features = None
        if emb_row is not None:
            candidate_clap = _parse_json(
                getattr(emb_row, "clap_embedding", None), None,
            )
            candidate_mert = _parse_json(
                getattr(emb_row, "mert_embedding", None), None,
            )
            candidate_effnet = _parse_json(
                getattr(emb_row, "embedding", None), None,
            )
            if isinstance(candidate_effnet, list) and len(candidate_effnet) < 10:
                candidate_effnet = None
        if af_row is not None:
            audio_features = {
                "tempo": getattr(af_row, "tempo", None),
                "energy": getattr(af_row, "energy", None),
                "danceability": getattr(af_row, "danceability", None),
                "brightness": getattr(af_row, "brightness", None),
                "beat_strength": getattr(af_row, "beat_strength", None),
                "valence_proxy": getattr(af_row, "valence_proxy", None),
                "acousticness": getattr(af_row, "acousticness", None),
            }

        # ── Load mood signal for this track (global_song_cache) ──────
        candidate_mood_tags: list[str] = []
        candidate_mood_scores: dict[str, float] | None = None
        try:
            async with engine.begin() as conn:
                row = (await conn.execute(
                    sa.select(
                        global_song_cache.c.mood_tags,
                        global_song_cache.c.mood_scores,
                    ).where(
                        global_song_cache.c.catalog_id == catalog_id,
                    )
                )).first()
            if row is not None:
                tags = row.mood_tags
                if isinstance(tags, str):
                    try:
                        tags = json.loads(tags)
                    except (ValueError, TypeError):
                        tags = []
                if isinstance(tags, list):
                    candidate_mood_tags = [t for t in tags if isinstance(t, str)]
                scores = row.mood_scores
                if isinstance(scores, str):
                    try:
                        scores = json.loads(scores)
                    except (ValueError, TypeError):
                        scores = None
                if isinstance(scores, dict):
                    candidate_mood_scores = {
                        k: float(v) for k, v in scores.items()
                        if isinstance(k, str) and isinstance(v, (int, float))
                    }
        except Exception:
            logger.debug(
                "Could not load mood signal for breakdown", exc_info=True,
            )

        # Effective (context-adaptive) weights
        weights = compute_context_weights(
            profile_dict,
            has_audio=bool(profile_dict.get("audio_centroid")),
            has_calibration=False,
            mood_active=False,
        )

        # Calibration for this user (may boost artist_match)
        calibration_artists = await self._load_calibration_artists(
            engine, user_id=user_id,
        )

        # ── Score ──────────────────────────────────────────────────
        result = score_candidate(
            song_dict, profile_dict,
            weights=weights,
            audio_features=audio_features,
            user_audio_centroid=profile_dict.get("audio_centroid") or None,
            candidate_embedding=candidate_effnet,
            user_embedding_centroid=profile_dict.get("embedding_centroid"),
            candidate_clap=candidate_clap,
            user_clap_centroid=profile_dict.get("clap_centroid"),
            candidate_mert=candidate_mert,
            user_mert_centroid=profile_dict.get("mert_centroid"),
            calibration_artists=calibration_artists,
            candidate_mood_tags=candidate_mood_tags or None,
            candidate_mood_scores=candidate_mood_scores,
        )

        breakdown = result.get("_breakdown", {})

        # ── Weighted dimensions (sum to ~1.0 if all present) ────────
        weighted: list[dict[str, Any]] = [
            {"name": "clap", "label": "CLAP (audio-semantic)",
             "score": breakdown.get("clap_similarity", 0.0),
             "weight": weights.get("clap", 0.0)},
            {"name": "mert", "label": "MERT (musical structure)",
             "score": breakdown.get("mert_similarity", 0.0),
             "weight": weights.get("mert", 0.0)},
            {"name": "effnet", "label": "EffNet (timbral fingerprint)",
             "score": breakdown.get("effnet_similarity", 0.0),
             "weight": weights.get("effnet", 0.0)},
            {"name": "genre", "label": "Genre match",
             "score": breakdown.get("genre_match", 0.0),
             "weight": weights.get("genre", 0.0)},
            {"name": "scalar", "label": "Scalar audio (tempo/energy)",
             "score": breakdown.get("scalar_similarity", 0.0),
             "weight": weights.get("scalar", 0.0)},
            {"name": "mood_match", "label": "Mood distribution match",
             "score": breakdown.get("mood_match", 0.0),
             "weight": weights.get("mood_match", 0.0)},
            {"name": "artist", "label": "Artist affinity",
             "score": breakdown.get("artist_match", 0.0),
             "weight": weights.get("artist", 0.0)},
        ]

        # ── Modifiers (additive bonuses + subtractive penalties) ────
        # weight reflects the ABSOLUTE cap of each modifier; score is the
        # realized value (positive = bonus, negative = penalty).
        modifiers: list[dict[str, Any]] = [
            {"name": "discovery_bonus", "label": "Discovery weight",
             "score": breakdown.get("discovery_bonus", 0.0), "weight": 0.04},
            {"name": "cross_strategy_bonus", "label": "Cross-strategy overlap",
             "score": breakdown.get("cross_strategy_bonus", 0.0), "weight": 0.10},
            {"name": "calibration_boost", "label": "Calibration boost",
             "score": breakdown.get("calibration_boost", 0.0), "weight": 0.20},
            {"name": "mood_boost", "label": "Mood match",
             "score": breakdown.get("mood_boost", 0.0) * 0.1, "weight": 0.10},
            {"name": "diversity_penalty", "label": "Diversity penalty",
             "score": -breakdown.get("diversity_penalty", 0.0) * 0.05, "weight": 0.05},
            {"name": "staleness", "label": "Staleness penalty",
             "score": -breakdown.get("staleness", 0.0) * 0.03, "weight": 0.03},
            {"name": "recency_boost", "label": "Recency boost",
             "score": breakdown.get("recency_boost", 0.0), "weight": 0.02},
        ]

        return {
            "catalog_id": catalog_id,
            "overall_score": result.get("_score", 0.0),
            "dimensions": weighted + modifiers,
            "explanation": result.get("_explanation", ""),
        }

    async def submit_feedback(
        self,
        engine,
        *,
        user_id: str,
        catalog_id: str,
        feedback_type: str,
    ) -> None:
        """Record user feedback on a recommendation (stub).

        recommendation_feedback table was removed. Logs feedback for now;
        persistence can be re-added when a new feedback table is introduced.

        Args:
            engine: SQLAlchemy async engine.
            user_id: SmarTaste user ID.
            catalog_id: Catalog ID of the track.
            feedback_type: One of thumbs_up, thumbs_down, skip.
        """
        logger.info(
            "Received %s feedback for catalog_id=%s user=%s (not persisted — table removed)",
            feedback_type, catalog_id, user_id,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _resolve_all_credentials(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
    ) -> list[tuple[str, str, str | None, str]]:
        """Resolve credentials for all connected services.

        Returns a list of (service, access_token, developer_token, storefront)
        tuples, one per connected service. For unified recommendations, this
        returns credentials for both Spotify and Apple Music.

        Returns:
            List of (service, access_token, developer_token, storefront) tuples.
        """
        connections = await get_user_connections(engine, user_id=user_id)
        if not connections:
            raise ValueError("No connected service found")

        results: list[tuple[str, str, str | None, str]] = []

        for conn_data in connections:
            service = conn_data["service"]

            try:
                creds = await self._resolve_single_credentials(
                    engine, encryption, settings,
                    user_id=user_id, service=service,
                )
                results.append(creds)
            except Exception:
                logger.warning(
                    "Failed to resolve credentials for %s, skipping", service,
                )

        if not results:
            raise ValueError("No valid service credentials found")

        return results

    async def _resolve_single_credentials(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
    ) -> tuple[str, str, str | None, str]:
        """Resolve credentials for a single service.

        Returns:
            Tuple of (service, access_token, developer_token, storefront).
        """
        async with engine.begin() as db_conn:
            result = await db_conn.execute(
                sa.select(service_connections).where(
                    sa.and_(
                        service_connections.c.user_id == user_id,
                        service_connections.c.service == service,
                    )
                )
            )
            row = result.first()

        if row is None:
            raise ValueError(f"No {service} connection found for user")

        access_token = encryption.decrypt(row.access_token_encrypted)

        # Handle Spotify token refresh if needed
        if service == "spotify":
            from datetime import UTC, datetime, timedelta

            token_expires_at = row.token_expires_at
            now = datetime.now(UTC)
            if token_expires_at is not None:
                if token_expires_at.tzinfo is None:
                    token_expires_at = token_expires_at.replace(tzinfo=UTC)
                if token_expires_at < now + timedelta(seconds=60):
                    refresh_token_encrypted = row.refresh_token_encrypted
                    if refresh_token_encrypted:
                        refresh_token_value = encryption.decrypt(refresh_token_encrypted)
                        token_data = await refresh_spotify_token(
                            refresh_token_value, settings.spotify_client_id,
                        )
                        if token_data:
                            access_token = token_data["access_token"]
                            await upsert_service_connection(
                                engine, encryption,
                                user_id=user_id,
                                service="spotify",
                                access_token=access_token,
                                refresh_token=token_data.get(
                                    "refresh_token", refresh_token_value,
                                ),
                                expires_in=token_data.get("expires_in"),
                                service_user_id=row.service_user_id,
                            )

        # Detect storefront / market for the user's region
        developer_token = None
        storefront = "us"
        if service == "apple_music":
            developer_token = generate_apple_developer_token(
                settings.apple_team_id,
                settings.apple_key_id,
                settings.apple_private_key_path,
                private_key_b64=settings.apple_private_key_b64,
            )
            storefront = await detect_apple_music_storefront(
                access_token, developer_token,
            )
            logger.info("Detected Apple Music storefront: %s", storefront)
        elif service == "spotify":
            # Detect Spotify market from user profile country
            try:
                from musicmind.api.services.service import (
                    fetch_spotify_user_profile,
                )
                profile = await fetch_spotify_user_profile(access_token)
                country = profile.get("country", "US")
                if country and len(country) == 2:
                    storefront = country.lower()
                    logger.info(
                        "Detected Spotify market: %s", storefront,
                    )
            except Exception:
                logger.debug("Could not detect Spotify market")

        return service, access_token, developer_token, storefront

    async def _run_discovery(
        self,
        service: str,
        access_token: str,
        seed_artist_names: list[str],
        top_genre_names: list[str],
        *,
        strategy: str,
        developer_token: str | None,
        storefront: str = "us",
        profile_genres: list[str] | None = None,
        seed_scored: list[tuple[str, float]] | None = None,
    ) -> list[dict[str, Any]]:
        """Run discovery strategies and tag candidates with source strategy."""
        candidates: list[dict[str, Any]] = []

        # Build genre sets for post-filtering
        # Full genre names (regional) get priority over parent genres
        full_genre_filter = set(g.lower() for g in (profile_genres or top_genre_names))
        # Also build parent-only set for looser fallback
        from musicmind.engine.profile import expand_genres as _expand

        parent_genres = set()
        for g in (profile_genres or top_genre_names):
            for expanded in _expand([g]):
                parent_genres.add(expanded.lower())

        def _filter_by_genre_overlap(
            results: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            """Keep candidates with regional genre overlap. Strict: prefer exact
            regional match, fall back to parent match only if needed."""
            if not full_genre_filter:
                return results

            exact_matches: list[dict[str, Any]] = []
            parent_matches: list[dict[str, Any]] = []

            for c in results:
                track_genres = set(g.lower() for g in c.get("genre_names", []))
                # Exact match on full regional genre names
                if track_genres & full_genre_filter:
                    exact_matches.append(c)
                # Looser: parent genre overlap (e.g. "Hip-Hop/Rap")
                elif track_genres & parent_genres:
                    parent_matches.append(c)

            # Prefer exact regional matches; add parent matches only to fill
            if exact_matches:
                return exact_matches
            if parent_matches:
                return parent_matches[:5]  # Limit loose matches
            return results[:3]  # Absolute fallback

        async def _run_similar_artists() -> list[dict[str, Any]]:
            scored = seed_scored or []
            results = await discover_similar_artists(
                service, access_token, scored,
                developer_token=developer_token,
                storefront=storefront,
                total_budget=40,
            )
            results = _filter_by_genre_overlap(results)
            for c in results:
                c["_strategy_source"] = "similar_artist"
            return results

        async def _run_genre_adjacent() -> list[dict[str, Any]]:
            results = await discover_genre_adjacent(
                service, access_token, top_genre_names,
                developer_token=developer_token,
                storefront=storefront,
            )
            results = _filter_by_genre_overlap(results)
            for c in results:
                c["_strategy_source"] = "genre_adjacent"
            return results

        async def _run_editorial() -> list[dict[str, Any]]:
            results = await discover_editorial(
                service, access_token, top_genre_names,
                developer_token=developer_token,
                storefront=storefront,
            )
            results = _filter_by_genre_overlap(results)
            for c in results:
                c["_strategy_source"] = "editorial"
            return results

        async def _run_chart_filter() -> list[dict[str, Any]]:
            results = await discover_chart_filter(
                service, access_token, top_genre_names,
                developer_token=developer_token,
                storefront=storefront,
            )
            for c in results:
                c["_strategy_source"] = "chart"
            return results

        strategy_map = {
            "similar_artist": _run_similar_artists,
            "genre_adjacent": _run_genre_adjacent,
            "editorial": _run_editorial,
            "chart": _run_chart_filter,
        }

        if strategy == "all":
            # Run all 4 in parallel, tolerate individual failures
            results = await asyncio.gather(
                _run_similar_artists(),
                _run_genre_adjacent(),
                _run_editorial(),
                _run_chart_filter(),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, list):
                    candidates.extend(result)
                elif isinstance(result, Exception):
                    logger.warning("Discovery strategy failed: %s", result)
        else:
            func = strategy_map.get(strategy)
            if func:
                try:
                    candidates.extend(await func())
                except Exception:
                    logger.exception("Discovery strategy '%s' failed", strategy)

        return candidates

    @staticmethod
    async def _load_candidates_from_db(
        engine,
        *,
        user_id: str,
        strategy: str = "all",
    ) -> list[dict[str, Any]]:
        """Read worker-populated discovery candidates from recommendation_candidates.

        Joins with global_song_cache for metadata. Aggregates per catalog_id:
        - _strategy_count: how many distinct strategies surfaced this track
        - _discovery_weight: max weight across strategies
        - _strategy_source: name of the highest-weight strategy
        """
        from musicmind.db.schema import (
            global_song_cache,
            recommendation_candidates as rc,
        )

        async with engine.begin() as conn:
            stmt = sa.select(
                rc.c.catalog_id,
                rc.c.strategy_source,
                rc.c.discovery_weight,
                rc.c.service_source,
                global_song_cache.c.name,
                global_song_cache.c.artist_name,
                global_song_cache.c.album_name,
                global_song_cache.c.genre_names,
                global_song_cache.c.isrc,
                global_song_cache.c.duration_ms,
                global_song_cache.c.release_date,
                global_song_cache.c.preview_url,
                global_song_cache.c.artwork_url,
                global_song_cache.c.mood_tags,
                global_song_cache.c.mood_scores,
            ).select_from(
                rc.join(
                    global_song_cache,
                    rc.c.catalog_id == global_song_cache.c.catalog_id,
                )
            ).where(rc.c.user_id == user_id)

            if strategy != "all":
                stmt = stmt.where(rc.c.strategy_source == strategy)

            result = await conn.execute(stmt)
            rows = result.fetchall()

        # Aggregate by catalog_id
        by_cid: dict[str, dict[str, Any]] = {}
        strategy_counts: dict[str, set[str]] = {}
        max_weights: dict[str, tuple[float, str]] = {}

        for row in rows:
            cid = row.catalog_id
            if not cid:
                continue
            genres = row.genre_names
            if isinstance(genres, str):
                try:
                    genres = json.loads(genres)
                except (ValueError, TypeError):
                    genres = []
            if cid not in by_cid:
                mood_tags = row.mood_tags
                if isinstance(mood_tags, str):
                    try:
                        mood_tags = json.loads(mood_tags)
                    except (ValueError, TypeError):
                        mood_tags = []
                if not isinstance(mood_tags, list):
                    mood_tags = []
                mood_scores = row.mood_scores
                if isinstance(mood_scores, str):
                    try:
                        mood_scores = json.loads(mood_scores)
                    except (ValueError, TypeError):
                        mood_scores = None
                if not isinstance(mood_scores, dict):
                    mood_scores = None
                by_cid[cid] = {
                    "catalog_id": cid,
                    "name": row.name or "",
                    "artist_name": row.artist_name or "",
                    "album_name": row.album_name or "",
                    "genre_names": genres or [],
                    "isrc": row.isrc,
                    "duration_ms": row.duration_ms,
                    "release_date": row.release_date,
                    "preview_url": row.preview_url or "",
                    "service_source": row.service_source or "",
                    "artwork_url_template": row.artwork_url or "",
                    "mood_tags": mood_tags,
                    "mood_scores": mood_scores,
                }
                strategy_counts[cid] = set()
                max_weights[cid] = (0.0, "")
            strategy_counts[cid].add(row.strategy_source)
            dw = float(row.discovery_weight or 0.0)
            if dw >= max_weights[cid][0]:
                max_weights[cid] = (dw, row.strategy_source)

        for cid, c in by_cid.items():
            c["_strategy_count"] = len(strategy_counts[cid])
            best_dw, best_strat = max_weights[cid]
            c["_discovery_weight"] = best_dw
            c["_strategy_source"] = best_strat or "db"

        return list(by_cid.values())

    @staticmethod
    async def _populate_candidates_background(
        engine,
        settings,
        *,
        user_id: str,
    ) -> None:
        """Fire-and-forget: trigger worker-side discovery for this user.

        Runs the same _run_discovery_for_user that the worker uses on each
        cycle; the 6h cadence guard inside it prevents redundant runs if the
        worker already touched this user recently.
        """
        try:
            from musicmind.worker import _run_discovery_for_user
            await _run_discovery_for_user(engine, settings, user_id=user_id)
        except Exception:
            logger.warning(
                "Background candidate populate failed for user %s",
                user_id[:8], exc_info=True,
            )

    @staticmethod
    def _deduplicate_candidates(
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Deduplicate by catalog_id, tracking cross-strategy occurrences."""
        by_id: dict[str, dict[str, Any]] = {}
        strategy_counts: dict[str, int] = {}

        for c in candidates:
            cid = c.get("catalog_id", "")
            if not cid:
                continue
            if cid not in by_id:
                by_id[cid] = c
                strategy_counts[cid] = 1
            else:
                strategy_counts[cid] += 1

        for cid, c in by_id.items():
            c["_strategy_count"] = strategy_counts.get(cid, 1)

        return list(by_id.values())

    @staticmethod
    async def _filter_library_songs(
        engine,
        candidates: list[dict[str, Any]],
        *,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """Remove candidates already in user's library (unless added >2 years ago).

        Songs older than 2 years in the library are allowed through as
        potential rediscovery suggestions.
        """
        if not candidates:
            return candidates

        from datetime import UTC, datetime, timedelta

        catalog_ids = [c.get("catalog_id", "") for c in candidates if c.get("catalog_id")]
        if not catalog_ids:
            return candidates

        cutoff = datetime.now(UTC) - timedelta(days=730)  # 2 years

        # Get library song IDs and their dates (only actual library songs)
        library_ids: set[str] = set()
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    song_metadata_cache.c.catalog_id,
                    song_metadata_cache.c.library_id,
                    song_metadata_cache.c.date_added_to_library,
                ).where(
                    sa.and_(
                        song_metadata_cache.c.catalog_id.in_(catalog_ids),
                        song_metadata_cache.c.user_id == user_id,
                        sa.or_(
                            song_metadata_cache.c.library_id.isnot(None),
                            song_metadata_cache.c.date_added_to_library.isnot(None),
                        ),
                    )
                )
            )
            for row in result:
                date_added = row.date_added_to_library
                if date_added is not None:
                    if hasattr(date_added, "tzinfo") and date_added.tzinfo is None:
                        date_added = date_added.replace(tzinfo=UTC)
                    # Keep songs added >2 years ago (rediscovery)
                    if date_added > cutoff:
                        library_ids.add(row.catalog_id)
                else:
                    # Has library_id but no date — treat as old, allow rediscovery
                    pass

        if not library_ids:
            return candidates

        filtered = [c for c in candidates if c.get("catalog_id", "") not in library_ids]

        logger.info(
            "Filtered %d library songs from %d candidates",
            len(candidates) - len(filtered), len(candidates),
        )
        return filtered if filtered else candidates[:5]  # Fallback if all filtered

    @staticmethod
    async def _load_lastfm_data(
        engine,
        *,
        user_id: str,
    ) -> tuple[dict[str, float] | None, set[str] | None]:
        """Return empty Last.fm data (tables removed).

        lastfm_tags_cache and lastfm_similar_tracks tables were removed.
        Returns (None, None) so the scorer gracefully degrades without
        tag similarity or collaborative match dimensions.
        """
        return None, None

    @staticmethod
    async def _load_calibration_artists(
        engine,
        *,
        user_id: str,
    ) -> dict[str, float] | None:
        """Load calibrated artist weights for scoring boost.

        Returns dict of lowercased artist name -> calibration weight,
        or None if no calibration exists.
        """
        from musicmind.db.schema import user_calibration

        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(user_calibration).where(
                    sa.and_(
                        user_calibration.c.user_id == user_id,
                        user_calibration.c.calibration_type.in_(
                            ["top_artist", "artist_rank"]
                        ),
                    )
                )
            )
            rows = result.fetchall()

        if not rows:
            return None

        return {row.item_id: row.weight for row in rows}

    @staticmethod
    async def _load_embeddings(
        engine,
        candidates: list[dict[str, Any]],
        *,
        user_id: str,
    ) -> tuple[
        dict[str, list[float]],
        dict[str, list[float]],
        dict[str, list[float]],
    ]:
        """Load CLAP/MERT/EffNet embeddings for candidate tracks.

        Checks user-scoped audio_embeddings first, then falls back to
        global audio_embeddings_global via ISRC for cross-user sharing.

        Returns (clap_map, mert_map, effnet_map) — catalog_id keyed.
        """
        from musicmind.db.schema import audio_embeddings, audio_embeddings_global

        clap_map: dict[str, list[float]] = {}
        mert_map: dict[str, list[float]] = {}
        effnet_map: dict[str, list[float]] = {}

        catalog_ids = [
            c.get("catalog_id", "") for c in candidates
            if c.get("catalog_id")
        ]
        if not catalog_ids:
            return clap_map, mert_map, effnet_map

        # User-scoped embeddings
        for i in range(0, len(catalog_ids), 500):
            chunk = catalog_ids[i:i + 500]
            try:
                async with engine.begin() as conn:
                    result = await conn.execute(
                        sa.select(
                            audio_embeddings.c.catalog_id,
                            audio_embeddings.c.embedding,
                            audio_embeddings.c.clap_embedding,
                            audio_embeddings.c.mert_embedding,
                        ).where(
                            sa.and_(
                                audio_embeddings.c.user_id == user_id,
                                audio_embeddings.c.catalog_id.in_(chunk),
                            )
                        )
                    )
                    for row in result:
                        cid = row.catalog_id
                        emb = row.embedding
                        if emb and isinstance(emb, list) and len(emb) > 10:
                            effnet_map[cid] = emb
                        clap = row.clap_embedding
                        if clap and isinstance(clap, list) and len(clap) > 10:
                            clap_map[cid] = clap
                        mert = row.mert_embedding
                        if mert and isinstance(mert, list) and len(mert) > 10:
                            mert_map[cid] = mert
            except Exception:
                logger.debug("Failed to load user embeddings", exc_info=True)

        # Global ISRC fallback for candidates not found in user table
        missing = [
            c for c in candidates
            if c.get("catalog_id", "") not in clap_map
            and c.get("isrc")
        ]
        if missing:
            isrcs = [c["isrc"] for c in missing]
            isrc_to_cid = {c["isrc"]: c["catalog_id"] for c in missing}
            try:
                async with engine.begin() as conn:
                    result = await conn.execute(
                        sa.select(
                            audio_embeddings_global.c.isrc,
                            audio_embeddings_global.c.embedding,
                            audio_embeddings_global.c.clap_embedding,
                            audio_embeddings_global.c.mert_embedding,
                        ).where(
                            audio_embeddings_global.c.isrc.in_(isrcs)
                        )
                    )
                    for row in result:
                        cid = isrc_to_cid.get(row.isrc, "")
                        if not cid:
                            continue
                        emb = row.embedding
                        if (
                            emb and isinstance(emb, list)
                            and len(emb) > 10 and cid not in effnet_map
                        ):
                            effnet_map[cid] = emb
                        clap = row.clap_embedding
                        if (
                            clap and isinstance(clap, list)
                            and len(clap) > 10 and cid not in clap_map
                        ):
                            clap_map[cid] = clap
                        mert = row.mert_embedding
                        if (
                            mert and isinstance(mert, list)
                            and len(mert) > 10 and cid not in mert_map
                        ):
                            mert_map[cid] = mert
            except Exception:
                logger.debug(
                    "Failed to load global embeddings", exc_info=True,
                )

        return clap_map, mert_map, effnet_map

    @staticmethod
    async def _load_audio_features(
        engine,
        candidates: list[dict[str, Any]],
        *,
        user_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Load cached audio features (scalar tempo/energy/etc) — read-only.

        Per-user ``audio_features_cache`` first, then ``audio_features_global``
        via ISRC for candidates not found in the user table. Does NOT run
        Essentia extraction; missing candidates simply don't get features
        and the scorer degrades gracefully.
        """
        from musicmind.db.schema import (
            audio_features_cache,
            audio_features_global,
        )

        af_map: dict[str, dict[str, Any]] = {}
        catalog_ids = [
            c.get("catalog_id", "") for c in candidates if c.get("catalog_id")
        ]
        if not catalog_ids:
            return af_map

        fields = (
            "tempo", "energy", "brightness", "danceability",
            "acousticness", "valence_proxy", "beat_strength",
            "key", "scale", "instrumentalness", "loudness",
        )

        for i in range(0, len(catalog_ids), 500):
            chunk = catalog_ids[i:i + 500]
            try:
                async with engine.begin() as conn:
                    result = await conn.execute(
                        sa.select(audio_features_cache).where(
                            sa.and_(
                                audio_features_cache.c.user_id == user_id,
                                audio_features_cache.c.catalog_id.in_(chunk),
                            )
                        )
                    )
                    for row in result:
                        row_features = {
                            f: getattr(row, f, None) for f in fields
                            if getattr(row, f, None) is not None
                        }
                        if row_features:
                            af_map[row.catalog_id] = row_features
            except Exception:
                logger.debug(
                    "Failed to load per-user audio features", exc_info=True,
                )

        missing = [
            c for c in candidates
            if c.get("catalog_id", "") not in af_map and c.get("isrc")
        ]
        if missing:
            isrcs = [c["isrc"] for c in missing]
            isrc_to_cid = {c["isrc"]: c["catalog_id"] for c in missing}
            try:
                async with engine.begin() as conn:
                    result = await conn.execute(
                        sa.select(audio_features_global).where(
                            audio_features_global.c.isrc.in_(isrcs)
                        )
                    )
                    for row in result:
                        cid = isrc_to_cid.get(row.isrc, "")
                        if not cid or cid in af_map:
                            continue
                        row_features = {
                            f: getattr(row, f, None) for f in fields
                            if getattr(row, f, None) is not None
                        }
                        if row_features:
                            af_map[cid] = row_features
            except Exception:
                logger.debug(
                    "Failed to load global audio features", exc_info=True,
                )

        return af_map

    @staticmethod
    async def _load_user_library_claps(
        engine,
        *,
        user_id: str,
    ) -> dict[str, dict[str, Any]] | None:
        """Load CLAP embeddings for user's library songs (contextual explanations).

        Returns {catalog_id: {"name": str, "artist_name": str, "clap": list[float]}}
        for songs that have CLAP embeddings, or None if empty.
        """
        from musicmind.db.schema import audio_embeddings

        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    song_metadata_cache.c.catalog_id,
                    song_metadata_cache.c.name,
                    song_metadata_cache.c.artist_name,
                    audio_embeddings.c.clap_embedding,
                ).select_from(
                    song_metadata_cache.join(
                        audio_embeddings,
                        sa.and_(
                            song_metadata_cache.c.catalog_id == audio_embeddings.c.catalog_id,
                            song_metadata_cache.c.user_id == audio_embeddings.c.user_id,
                        ),
                    )
                ).where(
                    sa.and_(
                        song_metadata_cache.c.user_id == user_id,
                        audio_embeddings.c.clap_embedding.isnot(None),
                        sa.or_(
                            song_metadata_cache.c.library_id.isnot(None),
                            song_metadata_cache.c.date_added_to_library.isnot(None),
                        ),
                    )
                )
            )
            out: dict[str, dict[str, Any]] = {}
            for row in result:
                clap = row.clap_embedding
                if isinstance(clap, str):
                    try:
                        clap = json.loads(clap)
                    except (ValueError, TypeError):
                        continue
                if clap and isinstance(clap, list) and len(clap) > 10:
                    out[row.catalog_id] = {
                        "name": row.name or "",
                        "artist_name": row.artist_name or "",
                        "clap": clap,
                    }

        return out if out else None

    @staticmethod
    async def _load_adaptive_weights(
        engine,
        *,
        user_id: str,
    ) -> tuple[dict[str, float], bool]:
        """Return default weights (feedback table removed).

        recommendation_feedback table was removed. Always returns
        default weights with adapted=False until a new feedback
        persistence mechanism is introduced.
        """
        return dict(DEFAULT_WEIGHTS), False

    @staticmethod
    async def _compute_predicted_score(
        engine,
        *,
        user_id: str,
        catalog_id: str,
    ) -> float | None:
        """Compute predicted score for a song if data is available.

        Looks up the song in song_metadata_cache and the user's latest
        taste profile snapshot. If both exist, scores the candidate.
        """
        async with engine.begin() as conn:
            # Get song metadata
            song_result = await conn.execute(
                sa.select(song_metadata_cache).where(
                    sa.and_(
                        song_metadata_cache.c.catalog_id == catalog_id,
                        song_metadata_cache.c.user_id == user_id,
                    )
                )
            )
            song_row = song_result.first()

            if song_row is None:
                return None

            # Get latest taste profile
            profile_result = await conn.execute(
                sa.select(taste_profile_snapshots)
                .where(taste_profile_snapshots.c.user_id == user_id)
                .order_by(taste_profile_snapshots.c.computed_at.desc())
                .limit(1)
            )
            profile_row = profile_result.first()

            if profile_row is None:
                return None

        # Build song dict
        genre_names = song_row.genre_names
        if isinstance(genre_names, str):
            try:
                genre_names = json.loads(genre_names)
            except (json.JSONDecodeError, TypeError):
                genre_names = []

        song_dict = {
            "catalog_id": song_row.catalog_id,
            "name": song_row.name,
            "artist_name": song_row.artist_name,
            "genre_names": genre_names,
            "release_date": song_row.release_date,
        }

        # Build profile dict
        def _parse_json(val: Any, default: Any) -> Any:
            if val is None:
                return default
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    return default
            return val

        profile_dict = {
            "genre_vector": _parse_json(profile_row.genre_vector, {}),
            "top_artists": _parse_json(profile_row.top_artists, []),
            "release_year_distribution": _parse_json(
                profile_row.release_year_distribution, {},
            ),
            "familiarity_score": profile_row.familiarity_score or 0.0,
        }

        result = score_candidate(song_dict, profile_dict)
        return result.get("_score")
