"""Candidate scoring — rank catalog songs against a taste profile.

Uses adaptive weights across 7+ dimensions: genre cosine, artist affinity,
audio similarity, graduated novelty, freshness, MMR diversity, anti-staleness,
cross-strategy bonus, mood boost, and optional classification bonus.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np

from musicmind.engine.profile import expand_genres
from musicmind.engine.weights import DEFAULT_WEIGHTS


def _genre_cosine(
    song_genres: list[str],
    genre_vector: dict[str, float],
) -> float:
    """Cosine similarity between a song's genres and the taste profile genre vector."""
    if not song_genres or not genre_vector:
        return 0.0

    expanded = expand_genres(song_genres)
    all_genres = set(genre_vector.keys()) | set(expanded)
    profile_vec = np.array([genre_vector.get(g, 0.0) for g in all_genres])
    song_weight = 1.0 / len(expanded) if expanded else 0.0
    song_vec = np.array([song_weight if g in expanded else 0.0 for g in all_genres])

    dot = np.dot(profile_vec, song_vec)
    norm_a = np.linalg.norm(profile_vec)
    norm_b = np.linalg.norm(song_vec)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def _compute_staleness(
    catalog_id: str,
    recent_recommendations: list[dict[str, Any]],
) -> float:
    """Compute staleness penalty based on recent recommendations.

    Returns 0.0 (no penalty) to 0.8 (recently recommended).
    """
    if not recent_recommendations:
        return 0.0

    now = datetime.now(tz=UTC)
    for rec in recent_recommendations:
        if rec.get("catalog_id") != catalog_id:
            continue
        created = rec.get("created_at")
        if created is None:
            continue
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created)
            except (ValueError, TypeError):
                continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)

        age_days = (now - created).total_seconds() / 86400.0
        if age_days < 7:
            return 0.8
        elif age_days < 30:
            return 0.4
    return 0.0


def score_candidate(
    candidate: dict[str, Any],
    profile: dict[str, Any],
    already_selected: list[dict[str, Any]] | None = None,
    *,
    weights: dict[str, float] | None = None,
    audio_features: dict[str, Any] | None = None,
    user_audio_centroid: dict[str, float] | None = None,
    recent_recommendations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score a single candidate song against a taste profile.

    Returns the candidate dict augmented with:
    - _score: overall score (0-1)
    - _breakdown: per-dimension scores
    - _explanation: human-readable explanation
    """
    w = weights or DEFAULT_WEIGHTS
    genre_vector = profile.get("genre_vector", {})
    top_artists = profile.get("top_artists", [])
    release_dist = profile.get("release_year_distribution", {})
    familiarity = profile.get("familiarity_score", 0.5)

    # 1. Genre match (cosine similarity)
    genre_score = _genre_cosine(
        candidate.get("genre_names", []), genre_vector
    )

    # 2. Artist match
    artist_name = candidate.get("artist_name", "").lower()
    artist_scores = {a["name"].lower(): a["score"] for a in top_artists}
    artist_match = artist_scores.get(artist_name, 0.0)

    # 3. Audio similarity (Tier 2)
    audio_sim = 0.5  # neutral default
    if audio_features and user_audio_centroid:
        from musicmind.engine.similarity import audio_feature_similarity
        audio_sim = audio_feature_similarity(audio_features, user_audio_centroid)

    # 4. Novelty: graduated via cosine distance from profile
    known_artists = {a["name"].lower() for a in top_artists}
    novelty = 0.0
    if artist_name not in known_artists and genre_score > 0.1:
        # Gaussian bell curve peaking at distance 0.3-0.5
        distance = 1.0 - genre_score
        peak = 0.4
        width = 0.2 + familiarity * 0.2  # adventurous = wider peak
        novelty = float(np.exp(-((distance - peak) ** 2) / (2 * width ** 2)))

    # 5. Freshness: match release year to user's distribution
    freshness = 0.5
    release_date = candidate.get("release_date", "")
    if release_date and len(release_date) >= 4:
        year = release_date[:4]
        freshness = release_dist.get(year, 0.0)
        try:
            if int(year) >= 2024:
                freshness = max(freshness, 0.3)
        except ValueError:
            pass

    # 6. Diversity penalty (MMR-style)
    diversity_penalty = 0.0
    if already_selected:
        from musicmind.engine.similarity import song_similarity
        max_sim = max(
            song_similarity(candidate, s) for s in already_selected
        )
        diversity_penalty = max_sim * 0.3

    # 7. Staleness penalty
    staleness = _compute_staleness(
        candidate.get("catalog_id", ""),
        recent_recommendations or [],
    )

    # 8. Cross-strategy bonus
    strategy_count = candidate.get("_strategy_count", 1)
    cross_bonus = min(0.15, max(0, (strategy_count - 1)) * 0.05)

    # 9. Mood boost (set by filter_candidates_by_mood)
    mood_boost = candidate.get("_mood_boost", 0.0)

    # Weighted combination
    overall = (
        w.get("genre", 0.25) * genre_score
        + w.get("artist", 0.15) * artist_match
        + w.get("audio", 0.15) * audio_sim
        + w.get("novelty", 0.13) * novelty
        + w.get("freshness", 0.12) * freshness
        + w.get("diversity", 0.10) * (1.0 - diversity_penalty)
        + w.get("staleness", 0.10) * (1.0 - staleness)
        + cross_bonus
        + mood_boost * 0.1
    )
    overall = max(0.0, min(1.0, overall))

    # Build explanation
    parts = []
    if genre_score > 0.5:
        top_genres = ", ".join(candidate.get("genre_names", [])[:3])
        parts.append(f"strong genre match ({top_genres})")
    if artist_match > 0.5:
        parts.append(f"you like {candidate.get('artist_name', 'this artist')}")
    if novelty > 0.3:
        parts.append("new artist in a genre you enjoy")
    if audio_sim > 0.7 and audio_features:
        parts.append("sounds like your audio preferences")
    if cross_bonus > 0:
        parts.append(f"found by {strategy_count} strategies")
    if staleness > 0:
        parts.append("recently recommended")
    if diversity_penalty > 0.2:
        parts.append("slight diversity penalty")
    if mood_boost > 0.2:
        parts.append("strong mood match")

    return {
        **candidate,
        "_score": round(overall, 3),
        "_breakdown": {
            "genre_match": round(genre_score, 3),
            "artist_match": round(artist_match, 3),
            "audio_similarity": round(audio_sim, 3),
            "novelty": round(novelty, 3),
            "freshness": round(freshness, 3),
            "diversity_penalty": round(diversity_penalty, 3),
            "staleness": round(staleness, 3),
            "cross_strategy_bonus": round(cross_bonus, 3),
            "mood_boost": round(mood_boost, 3),
        },
        "_explanation": "; ".join(parts) if parts else "moderate match",
    }


def rank_candidates(
    candidates: list[dict[str, Any]],
    profile: dict[str, Any],
    count: int = 20,
    *,
    weights: dict[str, float] | None = None,
    audio_features_map: dict[str, dict[str, Any]] | None = None,
    user_audio_centroid: dict[str, float] | None = None,
    recent_recommendations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates using MMR-style scoring with diversity.

    Selects top candidates one at a time, applying diversity penalty
    based on similarity to already-selected songs.
    """
    if not candidates:
        return []

    af_map = audio_features_map or {}
    selected: list[dict[str, Any]] = []
    remaining = list(candidates)

    for _ in range(min(count, len(remaining))):
        scored = [
            score_candidate(
                c, profile, selected,
                weights=weights,
                audio_features=af_map.get(c.get("catalog_id", "")),
                user_audio_centroid=user_audio_centroid,
                recent_recommendations=recent_recommendations,
            )
            for c in remaining
        ]
        scored.sort(key=lambda x: x["_score"], reverse=True)

        best = scored[0]
        selected.append(best)
        remaining = [
            c for c in remaining
            if c.get("catalog_id") != best.get("catalog_id")
        ]

    return selected
