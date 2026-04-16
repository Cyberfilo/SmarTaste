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

        # Step 2: Resolve service + credentials
        all_creds = await self._resolve_all_credentials(
            engine, encryption, settings, user_id=user_id,
        )

        # Step 3: Extract seed data from profile
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

        # Step 4: Run discovery strategies (against all connected services)
        candidates: list[dict[str, Any]] = []
        discovery_tasks = []
        for svc, access_token, developer_token, storefront in all_creds:
            discovery_tasks.append(
                self._run_discovery(
                    svc, access_token, seed_artist_names, top_genre_names,
                    strategy=strategy,
                    developer_token=developer_token,
                    storefront=storefront,
                    profile_genres=top_genre_names,
                    seed_scored=seed_scored,
                )
            )

        discovery_results = await asyncio.gather(
            *discovery_tasks, return_exceptions=True,
        )
        for result in discovery_results:
            if isinstance(result, list):
                candidates.extend(result)
            elif isinstance(result, Exception):
                logger.warning("Cross-service discovery failed: %s", result)

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

        # ── Two-pass scoring ─────────────────────────────────
        # Pass 1: Score on non-audio dimensions (instant)
        pre_ranked = rank_candidates(
            unique, profile, count=min(limit * 3, 30),
            weights=weights,
            calibration_artists=calibration_artists,
            user_tag_profile=user_tag_profile,
            collaborative_matches=collaborative_matches,
        )

        # Pass 2: Lazy-enrich top candidates, then re-score with audio
        from musicmind.engine.enrichment.orchestrator import enrich_candidates

        audio_features_map = await enrich_candidates(
            engine, pre_ranked, user_id=user_id,
            max_to_enrich=min(limit * 2, 30),
        )

        # Load CLAP/MERT/EffNet embedding maps for candidates
        clap_map, mert_map, embedding_map = await self._load_embeddings(
            engine, pre_ranked, user_id=user_id,
        )

        ranked = rank_candidates(
            pre_ranked, profile, count=limit, weights=weights,
            audio_features_map=audio_features_map,
            user_audio_centroid=user_audio_centroid,
            embedding_map=embedding_map or None,
            user_embedding_centroid=user_embedding_centroid,
            clap_map=clap_map or None,
            user_clap_centroid=user_clap_centroid,
            mert_map=mert_map or None,
            user_mert_centroid=user_mert_centroid,
            calibration_artists=calibration_artists,
            user_tag_profile=user_tag_profile,
            collaborative_matches=collaborative_matches,
        )

        # Step 9: Build explanations
        items = []
        for result in ranked:
            explanation = _build_explanation(result.get("_breakdown", {}))
            items.append({
                "catalog_id": result.get("catalog_id", ""),
                "name": result.get("name", ""),
                "artist_name": result.get("artist_name", ""),
                "album_name": result.get("album_name", ""),
                "artwork_url": result.get("artwork_url_template", ""),
                "preview_url": result.get("preview_url", ""),
                "score": result.get("_score", 0.0),
                "explanation": explanation,
                "strategy_source": result.get("_strategy_source", strategy),
                "genre_names": result.get("genre_names", []),
            })

        # Step 10: Return
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
        """Get the 7-dimension scoring breakdown for a track.

        Re-scores the track against the user's latest taste profile and
        returns overall score, per-dimension scores with weights, and
        a natural-language explanation.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for token decryption.
            settings: Application settings.
            user_id: SmarTaste user ID.
            catalog_id: Catalog ID of the track to score.

        Returns:
            Dict with catalog_id, overall_score, dimensions, explanation.

        Raises:
            ValueError: If track not in song_metadata_cache or no profile.
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
                raise ValueError("Track not found in catalog")

            # Get latest taste profile
            profile_result = await conn.execute(
                sa.select(taste_profile_snapshots)
                .where(taste_profile_snapshots.c.user_id == user_id)
                .order_by(taste_profile_snapshots.c.computed_at.desc())
                .limit(1)
            )
            profile_row = profile_result.first()

            if profile_row is None:
                raise ValueError("No taste profile available")

        # Build song dict
        genre_names = song_row.genre_names
        if isinstance(genre_names, str):
            try:
                genre_names = json.loads(genre_names)
            except (json.JSONDecodeError, TypeError):
                genre_names = []

        song_dict: dict[str, Any] = {
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

        profile_dict: dict[str, Any] = {
            "genre_vector": _parse_json(profile_row.genre_vector, {}),
            "top_artists": _parse_json(profile_row.top_artists, []),
            "release_year_distribution": _parse_json(
                profile_row.release_year_distribution, {},
            ),
            "familiarity_score": profile_row.familiarity_score or 0.0,
        }

        # Score the candidate
        result = score_candidate(song_dict, profile_dict)

        # Map breakdown keys to the 7 reportable dimensions
        breakdown = result.get("_breakdown", {})
        dimension_map: list[tuple[str, str, str]] = [
            ("genre_match", "Genre Match", "genre"),
            ("audio_similarity", "Audio Similarity", "audio"),
            ("artist_match", "Artist Affinity", "artist"),
            ("language_match", "Language/Region", "language"),
        ]

        dimensions: list[dict[str, Any]] = []
        for dim_name, dim_label, weight_key in dimension_map:
            # Map breakdown keys to dimension names
            if dim_name == "diversity":
                score = round(1.0 - breakdown.get("diversity_penalty", 0.0), 3)
            elif dim_name == "artist_affinity":
                score = breakdown.get("artist_match", 0.0)
            elif dim_name == "anti_staleness":
                score = round(1.0 - breakdown.get("staleness", 0.0), 3)
            else:
                score = breakdown.get(dim_name, 0.0)

            dimensions.append({
                "name": dim_name,
                "label": dim_label,
                "score": score,
                "weight": DEFAULT_WEIGHTS.get(weight_key, 0.0),
            })

        return {
            "catalog_id": catalog_id,
            "overall_score": result.get("_score", 0.0),
            "dimensions": dimensions,
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
            scored = seed_scored or [(n, 1.0) for n in seed_artist_names]
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
