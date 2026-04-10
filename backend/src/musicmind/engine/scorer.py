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


def _language_match(
    song_genres: list[str],
    genre_vector: dict[str, float],
) -> float:
    """Score how well a song matches the user's regional/language preferences.

    Detects regional prefixes in genre names (e.g. "Italian", "French", "Korean")
    and checks if the candidate's regional tags match the user's dominant regions.
    Returns 1.0 for perfect match, 0.0 for no regional overlap.
    """
    if not song_genres or not genre_vector:
        return 0.0

    # Extract regional prefixes from genre names
    # E.g. "Italian Hip-Hop/Rap" → "Italian", "K-Pop" → "K"
    def _extract_region(genre: str) -> str | None:
        parts = genre.split()
        if len(parts) >= 2:
            # "Italian Hip-Hop/Rap" → "Italian"
            candidate = parts[0]
            # Filter out non-regional prefixes
            if candidate[0].isupper() and len(candidate) > 1:
                return candidate.lower()
        # Handle "K-Pop", "J-Rock" style
        if "-" in genre and len(genre.split("-")[0]) <= 2:
            return genre.split("-")[0].lower()
        return None

    # Build user's regional profile from genre_vector
    user_regions: dict[str, float] = {}
    for genre, weight in genre_vector.items():
        region = _extract_region(genre)
        if region:
            user_regions[region] = user_regions.get(region, 0.0) + weight

    if not user_regions:
        return 0.5  # No regional signal — neutral

    # Check candidate's regional tags
    candidate_regions: set[str] = set()
    for genre in song_genres:
        region = _extract_region(genre)
        if region:
            candidate_regions.add(region)

    if not candidate_regions:
        # Candidate has no regional tag — neutral, don't penalize
        return 0.5

    # Score: weighted overlap
    total_user_weight = sum(user_regions.values())
    if total_user_weight == 0:
        return 0.5

    overlap_weight = sum(
        user_regions.get(r, 0.0) for r in candidate_regions
    )
    return min(1.0, overlap_weight / total_user_weight)


def _genre_cosine(
    song_genres: list[str],
    genre_vector: dict[str, float],
) -> float:
    """Cosine similarity between a song's genres and the taste profile genre vector.

    Regional genre prioritization: original genre names get full weight (1.0),
    expanded parent genres get reduced weight (0.3). This ensures a song tagged
    "Italian Hip-Hop/Rap" scores much higher against a profile dominated by
    "Italian Hip-Hop/Rap" than a song tagged just "Hip-Hop/Rap".
    """
    if not song_genres or not genre_vector:
        return 0.0

    originals = set(song_genres)
    expanded = expand_genres(song_genres)
    all_genres = set(genre_vector.keys()) | set(expanded)
    profile_vec = np.array([genre_vector.get(g, 0.0) for g in all_genres])

    # Build song vector: original genres get 1.0, expanded parents get 0.3
    raw_weights = {}
    for g in expanded:
        if g in originals:
            raw_weights[g] = 1.0
        else:
            raw_weights[g] = 0.3
    total_w = sum(raw_weights.values())
    song_vec = np.array([
        raw_weights.get(g, 0.0) / total_w if total_w > 0 else 0.0
        for g in all_genres
    ])

    dot = np.dot(profile_vec, song_vec)
    norm_a = np.linalg.norm(profile_vec)
    norm_b = np.linalg.norm(song_vec)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def _build_staleness_index(
    recent_recommendations: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Pre-index recent recommendations by catalog_id for O(1) lookup."""
    index: dict[str, dict[str, Any]] = {}
    for rec in recent_recommendations:
        cid = rec.get("catalog_id")
        if cid and cid not in index:
            index[cid] = rec
    return index


def _compute_staleness(
    catalog_id: str,
    staleness_index: dict[str, dict[str, Any]],
) -> float:
    """Compute staleness penalty based on recent recommendations.

    Returns 0.0 (no penalty) to 0.8 (recently recommended).
    Uses pre-built index for O(1) lookup instead of linear scan.
    """
    rec = staleness_index.get(catalog_id)
    if rec is None:
        return 0.0

    created = rec.get("created_at")
    if created is None:
        return 0.0
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except (ValueError, TypeError):
            return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)

    now = datetime.now(tz=UTC)
    age_days = (now - created).total_seconds() / 86400.0
    if age_days < 7:
        return 0.8
    elif age_days < 30:
        return 0.4
    return 0.0


def _tag_cosine(
    candidate_tags: dict[str, float] | None,
    user_tag_profile: dict[str, float] | None,
) -> float:
    """Cosine similarity between candidate's Last.fm tags and user tag profile.

    Tags encode mood/vibe signals ("dark", "aggressive", "Italian drill")
    that structured genres miss. Returns 0.5 (neutral) if either is None.
    """
    if not candidate_tags or not user_tag_profile:
        return 0.5

    all_tags = set(candidate_tags.keys()) | set(user_tag_profile.keys())
    if not all_tags:
        return 0.5

    vec_a = np.array([candidate_tags.get(t, 0.0) for t in all_tags])
    vec_b = np.array([user_tag_profile.get(t, 0.0) for t in all_tags])

    dot = np.dot(vec_a, vec_b)
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 0.5
    return float(dot / (norm_a * norm_b))


def score_candidate(
    candidate: dict[str, Any],
    profile: dict[str, Any],
    already_selected: list[dict[str, Any]] | None = None,
    *,
    weights: dict[str, float] | None = None,
    audio_features: dict[str, Any] | None = None,
    user_audio_centroid: dict[str, float] | None = None,
    candidate_embedding: list[float] | None = None,
    user_embedding_centroid: list[float] | None = None,
    recent_recommendations: list[dict[str, Any]] | None = None,
    staleness_index: dict[str, dict[str, Any]] | None = None,
    calibration_artists: dict[str, float] | None = None,
    candidate_tags: dict[str, float] | None = None,
    user_tag_profile: dict[str, float] | None = None,
    collaborative_matches: set[str] | None = None,
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

    # 1. Language/region match (most important for regional listeners)
    language_score = _language_match(
        candidate.get("genre_names", []), genre_vector
    )

    # 2. Genre match (cosine similarity)
    genre_score = _genre_cosine(
        candidate.get("genre_names", []), genre_vector
    )

    # 3. Audio similarity (hybrid: 70% embedding + 30% scalar when available)
    audio_sim = 0.5  # neutral default when no features available
    if audio_features or candidate_embedding:
        from musicmind.engine.similarity import combined_audio_similarity
        audio_sim = combined_audio_similarity(
            audio_features, user_audio_centroid,
            candidate_embedding, user_embedding_centroid,
        )

    # 4. Artist match — penalized if known artist but wrong genre
    artist_name = candidate.get("artist_name", "").lower()
    artist_scores = {a["name"].lower(): a["score"] for a in top_artists}
    artist_match = artist_scores.get(artist_name, 0.0)
    if artist_match > 0 and genre_score < 0.2:
        artist_match *= 0.3

    # 5. Diversity penalty (MMR-style)
    diversity_penalty = 0.0
    if already_selected:
        from musicmind.engine.similarity import song_similarity
        max_sim = max(
            song_similarity(candidate, s) for s in already_selected
        )
        diversity_penalty = max_sim * 0.3

    # 6. Staleness penalty
    if staleness_index is not None:
        staleness = _compute_staleness(
            candidate.get("catalog_id", ""), staleness_index
        )
    else:
        recs = recent_recommendations or []
        idx = _build_staleness_index(recs) if recs else {}
        staleness = _compute_staleness(candidate.get("catalog_id", ""), idx)

    # 7. Cross-strategy bonus
    strategy_count = candidate.get("_strategy_count", 1)
    cross_bonus = min(0.10, max(0, (strategy_count - 1)) * 0.05)

    # 8. Mood boost (set by filter_candidates_by_mood)
    mood_boost = candidate.get("_mood_boost", 0.0)

    # 9. Tag similarity (Last.fm crowd-sourced mood/vibe tags)
    tag_sim = _tag_cosine(candidate_tags, user_tag_profile)

    # 10. Collaborative match (Last.fm similar tracks)
    collab_boost = 0.0
    if collaborative_matches:
        candidate_key = f"{artist_name}:{candidate.get('name', '').lower()}"
        if candidate_key in collaborative_matches:
            collab_boost = 0.20  # Direct collaborative signal

    # 11. Calibration boost — continuous function of calibration weight
    # Scales smoothly: weight 5.0 → +0.15, 3.0 → +0.09, 1.0 → +0.03
    # Zeroed out if the song's genre doesn't match user profile (wrong-genre penalty)
    cal_boost = 0.0
    if calibration_artists:
        cal_weight = calibration_artists.get(artist_name, 0.0)
        if cal_weight > 0:
            cal_boost = min(0.20, cal_weight * 0.03)
            # Kill boost if this is a known artist in the wrong genre
            if genre_score < 0.15:
                cal_boost = 0.0

    # Weighted combination — 6 dimensions + bonuses
    # When audio/tags unavailable, redistribute weight proportionally
    has_audio = audio_features is not None and user_audio_centroid is not None
    has_tags = candidate_tags is not None and user_tag_profile is not None

    w_genre = w.get("genre", 0.25)
    w_audio = w.get("audio", 0.20)
    w_artist = w.get("artist", 0.15)
    w_lang = w.get("language", 0.15)
    w_tags = w.get("tags", 0.15)
    w_collab = w.get("collab", 0.10)

    # Redistribute unavailable dimensions
    if not has_audio:
        redistribute = w_audio
        w_audio = 0.0
        w_genre += redistribute * 0.4
        w_tags += redistribute * 0.3
        w_artist += redistribute * 0.3

    if not has_tags:
        redistribute = w_tags
        w_tags = 0.0
        w_genre += redistribute * 0.5
        w_lang += redistribute * 0.5

    # Renormalize redistributed weights so core dimensions sum to 1.0
    w_total = w_genre + w_audio + w_artist + w_lang + w_tags + w_collab
    if w_total > 0 and abs(w_total - 1.0) > 0.001:
        w_genre /= w_total
        w_audio /= w_total
        w_artist /= w_total
        w_lang /= w_total
        w_tags /= w_total
        w_collab /= w_total

    overall = (
        w_genre * genre_score
        + w_audio * audio_sim
        + w_artist * artist_match
        + w_lang * language_score
        + w_tags * tag_sim
        + w_collab * collab_boost  # 0.0 or 0.20 — continuous, not binary
        - 0.05 * diversity_penalty
        - 0.03 * staleness
        + cross_bonus
        + mood_boost * 0.1
        + cal_boost
    )
    overall = max(0.0, min(1.0, overall))

    # Build explanation
    parts = []
    if collab_boost > 0:
        parts.append("fans also listen to this")
    if tag_sim > 0.6 and has_tags:
        parts.append("similar vibe")
    if genre_score > 0.5:
        top_genres = ", ".join(candidate.get("genre_names", [])[:2])
        parts.append(f"genre match ({top_genres})")
    if audio_sim > 0.6 and audio_features:
        parts.append("sounds like your taste")
    if artist_match > 0.5:
        parts.append(f"you like {candidate.get('artist_name', 'this artist')}")
    if cal_boost > 0.1:
        parts.append("top calibrated artist")
    if language_score > 0.7:
        parts.append("matches your language/region")
    if cross_bonus > 0:
        parts.append(f"found by {strategy_count} strategies")
    if mood_boost > 0.2:
        parts.append("strong mood match")

    return {
        **candidate,
        "_score": round(overall, 3),
        "_breakdown": {
            "genre_match": round(genre_score, 3),
            "tag_similarity": round(tag_sim, 3),
            "collaborative_match": round(collab_boost, 3),
            "audio_similarity": round(audio_sim, 3),
            "artist_match": round(artist_match, 3),
            "language_match": round(language_score, 3),
            "calibration_boost": round(cal_boost, 3),
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
    embedding_map: dict[str, list[float]] | None = None,
    user_embedding_centroid: list[float] | None = None,
    recent_recommendations: list[dict[str, Any]] | None = None,
    calibration_artists: dict[str, float] | None = None,
    user_tag_profile: dict[str, float] | None = None,
    collaborative_matches: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates using MMR-style scoring with diversity.

    Optimized: precomputes base scores (without diversity penalty), then
    applies diversity penalty incrementally during greedy selection.
    """
    if not candidates:
        return []

    af_map = audio_features_map or {}
    emb_map = embedding_map or {}
    staleness_idx = _build_staleness_index(recent_recommendations or [])

    # Step 1: Precompute base scores for all candidates (no diversity penalty)
    base_scored: list[dict[str, Any]] = []
    for c in candidates:
        # Get candidate's Last.fm tags if available
        c_tags = c.get("_lastfm_tags")
        cid = c.get("catalog_id", "")

        scored = score_candidate(
            c, profile, already_selected=None,
            weights=weights,
            audio_features=af_map.get(cid),
            user_audio_centroid=user_audio_centroid,
            candidate_embedding=emb_map.get(cid),
            user_embedding_centroid=user_embedding_centroid,
            staleness_index=staleness_idx,
            calibration_artists=calibration_artists,
            candidate_tags=c_tags,
            user_tag_profile=user_tag_profile,
            collaborative_matches=collaborative_matches,
        )
        base_scored.append(scored)

    # Step 2: Greedy MMR selection — only recompute diversity penalty per iteration
    from musicmind.engine.similarity import song_similarity

    w = weights or DEFAULT_WEIGHTS
    diversity_weight = w.get("diversity", 0.10)
    selected: list[dict[str, Any]] = []
    remaining = list(base_scored)

    for _ in range(min(count, len(base_scored))):
        best_idx = -1
        best_score = -1.0

        for i, c in enumerate(remaining):
            base_score = c["_score"]
            # Recompute only the diversity component
            if selected:
                max_sim = max(song_similarity(c, s) for s in selected)
                diversity_penalty = max_sim * 0.3
            else:
                diversity_penalty = 0.0

            # Base score was computed with already_selected=None (diversity=0).
            # Simply subtract the diversity penalty from the base score.
            adjusted = base_score - diversity_weight * diversity_penalty
            adjusted = max(0.0, min(1.0, adjusted))

            if adjusted > best_score:
                best_score = adjusted
                best_idx = i

        best = remaining.pop(best_idx)
        # Update the score and breakdown with final diversity penalty
        if selected:
            max_sim = max(song_similarity(best, s) for s in selected)
            final_penalty = max_sim * 0.3
        else:
            final_penalty = 0.0
        best["_score"] = round(best_score, 3)
        best["_breakdown"]["diversity_penalty"] = round(final_penalty, 3)
        selected.append(best)

    return selected
