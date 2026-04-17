"""Candidate scoring — rank catalog songs against a taste profile.

Uses 6 weighted dimensions: CLAP embedding cosine, MERT embedding cosine,
EffNet embedding cosine, genre cosine, scalar audio similarity, and artist
affinity. Plus additive bonuses (calibration, cross-strategy, mood) and
penalties (diversity, staleness).
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
    candidate_clap: list[float] | None = None,
    user_clap_centroid: list[float] | None = None,
    candidate_mert: list[float] | None = None,
    user_mert_centroid: list[float] | None = None,
    recent_recommendations: list[dict[str, Any]] | None = None,
    staleness_index: dict[str, dict[str, Any]] | None = None,
    calibration_artists: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Score a single candidate song against a taste profile.

    Six weighted dimensions:
    - CLAP cosine (0.30) — holistic audio-text similarity
    - MERT cosine (0.25) — musical structure (pitch, rhythm)
    - EffNet cosine (0.15) — genre/style granularity
    - Genre match (0.15) — Apple Music / Spotify genre cosine
    - Scalar audio (0.10) — normalized euclidean on tempo/energy/danceability
    - Artist affinity (0.05) — library presence + calibration

    Missing dimensions redistribute weight proportionally to available ones.

    Returns the candidate dict augmented with:
    - _score: overall score (0-1)
    - _breakdown: per-dimension scores
    - _explanation: human-readable explanation
    """
    from musicmind.engine.similarity import (
        clap_similarity,
        effnet_similarity,
        mert_similarity,
        scalar_audio_similarity,
    )

    w = weights or DEFAULT_WEIGHTS
    genre_vector = profile.get("genre_vector", {})
    top_artists = profile.get("top_artists", [])

    # ── 1. Embedding similarities (primary signals) ───────
    clap_score = 0.0
    has_clap = (
        candidate_clap is not None
        and user_clap_centroid is not None
    )
    if has_clap:
        clap_score = clap_similarity(user_clap_centroid, candidate_clap)

    mert_score = 0.0
    has_mert = (
        candidate_mert is not None
        and user_mert_centroid is not None
    )
    if has_mert:
        mert_score = mert_similarity(user_mert_centroid, candidate_mert)

    effnet_score = 0.0
    has_effnet = (
        candidate_embedding is not None
        and user_embedding_centroid is not None
    )
    if has_effnet:
        effnet_score = effnet_similarity(user_embedding_centroid, candidate_embedding)

    # ── 2. Genre match (cosine similarity) ────────────────
    genre_score = _genre_cosine(
        candidate.get("genre_names", []), genre_vector
    )

    # ── 3. Scalar audio match ─────────────────────────────
    scalar_score = 0.0
    has_scalar = (
        audio_features is not None
        and user_audio_centroid is not None
    )
    if has_scalar:
        scalar_score = scalar_audio_similarity(user_audio_centroid, audio_features)

    # ── 4. Artist affinity — three-tier match, wrong-genre penalised ─────
    # Tier 1 (primary match): candidate's primary artist is in user's top —
    #         full score, e.g. "Capo Plaza" on a Capo Plaza track.
    # Tier 2 (feat match): candidate's primary isn't in top, but one of its
    #         featured artists is — score discounted by FEATURING_WEIGHT=0.3.
    #         e.g. a "Lacrim feat. Simba La Rue" track where the user has
    #         Simba La Rue in their library but not Lacrim.
    # Tier 3 (no match): neither → 0.
    # The "candidate's primary was feat'd on library tracks" case is already
    # handled by build_artist_affinity, which injects feat artists into
    # top_artists with a 0.3 weight during profile computation.
    from musicmind.engine.profile import parse_artists
    raw_artist_str = candidate.get("artist_name", "")
    artist_scores = {a["name"].lower(): a["score"] for a in top_artists}

    artist_match = 0.0
    matched_as = ""
    for name, component_weight in parse_artists(raw_artist_str):
        score = artist_scores.get(name.lower(), 0.0)
        if score <= 0:
            continue
        weighted = score * component_weight  # primary 1.0, feat 0.3
        if weighted > artist_match:
            artist_match = weighted
            matched_as = "primary" if component_weight >= 1.0 else "feat"

    if artist_match > 0 and genre_score < 0.2:
        artist_match *= 0.3

    # ── 5. Diversity penalty (MMR-style) ──────────────────
    diversity_penalty = 0.0
    if already_selected:
        from musicmind.engine.similarity import song_similarity

        max_sim = max(
            song_similarity(candidate, s) for s in already_selected
        )
        diversity_penalty = max_sim * 0.3

    # ── 6. Staleness penalty ──────────────────────────────
    if staleness_index is not None:
        staleness = _compute_staleness(
            candidate.get("catalog_id", ""), staleness_index
        )
    else:
        recs = recent_recommendations or []
        idx = _build_staleness_index(recs) if recs else {}
        staleness = _compute_staleness(candidate.get("catalog_id", ""), idx)

    # ── 7. Cross-strategy bonus ───────────────────────────
    strategy_count = candidate.get("_strategy_count", 1)
    cross_bonus = min(0.10, max(0, (strategy_count - 1)) * 0.05)

    # ── 7b. Discovery-weight bonus ────────────────────────
    # Cross-strategy bonus already counts duplicate sources; discovery_weight
    # captures positional + seed affinity from the similar_artist crawl. Cap
    # the bonus so it stays additive (max +0.04 at perfect alignment).
    discovery_weight = float(candidate.get("_discovery_weight", 0.0))
    discovery_bonus = max(0.0, min(0.04, discovery_weight * 0.04))

    # ── 8. Mood boost (set by filter_candidates_by_mood) ──
    mood_boost = candidate.get("_mood_boost", 0.0)

    # ── 9. Calibration boost ──────────────────────────────
    cal_boost = 0.0
    if calibration_artists:
        cal_weight = calibration_artists.get(artist_name, 0.0)
        if cal_weight > 0:
            cal_boost = min(0.20, cal_weight * 0.03)
            if genre_score < 0.15:
                cal_boost = 0.0

    # ── Weighted combination with graceful degradation ────
    # Collect available dimensions and their weights
    dim_scores: dict[str, float] = {}
    dim_weights: dict[str, float] = {}

    if has_clap:
        dim_scores["clap"] = clap_score
        dim_weights["clap"] = w.get("clap", 0.30)
    if has_mert:
        dim_scores["mert"] = mert_score
        dim_weights["mert"] = w.get("mert", 0.25)
    if has_effnet:
        dim_scores["effnet"] = effnet_score
        dim_weights["effnet"] = w.get("effnet", 0.15)

    # Genre and artist are always available
    dim_scores["genre"] = genre_score
    dim_weights["genre"] = w.get("genre", 0.15)
    dim_scores["artist"] = artist_match
    dim_weights["artist"] = w.get("artist", 0.05)

    if has_scalar:
        dim_scores["scalar"] = scalar_score
        dim_weights["scalar"] = w.get("scalar", 0.10)

    # Normalize weights to sum to 1.0 (redistributes missing dimensions)
    w_total = sum(dim_weights.values())
    if w_total > 0:
        dim_weights = {k: v / w_total for k, v in dim_weights.items()}

    overall = sum(
        dim_weights[k] * dim_scores[k] for k in dim_scores
    )

    # Additive bonuses and penalties
    overall += cross_bonus
    overall += discovery_bonus
    overall += mood_boost * 0.1
    overall += cal_boost
    overall -= 0.05 * diversity_penalty
    overall -= 0.03 * staleness

    overall = max(0.0, min(1.0, overall))

    # ── Build explanation ─────────────────────────────────
    parts = []
    if has_clap and clap_score > 0.7:
        parts.append("sounds like your taste")
    if has_mert and mert_score > 0.7:
        parts.append("similar musical structure")
    if genre_score > 0.5:
        top_genres = ", ".join(candidate.get("genre_names", [])[:2])
        parts.append(f"genre match ({top_genres})")
    if has_scalar and scalar_score > 0.7:
        parts.append("similar energy/tempo")
    if artist_match > 0.5:
        if matched_as == "feat":
            parts.append(
                f"features an artist you like ({candidate.get('artist_name', '')})"
            )
        else:
            parts.append(f"you like {candidate.get('artist_name', 'this artist')}")
    if cal_boost > 0.1:
        parts.append("top calibrated artist")
    if cross_bonus > 0:
        parts.append(f"found by {strategy_count} strategies")
    if mood_boost > 0.2:
        parts.append("strong mood match")

    return {
        **candidate,
        "_score": round(overall, 3),
        "_breakdown": {
            "clap_similarity": round(clap_score, 3),
            "mert_similarity": round(mert_score, 3),
            "effnet_similarity": round(effnet_score, 3),
            "genre_match": round(genre_score, 3),
            "scalar_similarity": round(scalar_score, 3),
            "artist_match": round(artist_match, 3),
            "calibration_boost": round(cal_boost, 3),
            "diversity_penalty": round(diversity_penalty, 3),
            "staleness": round(staleness, 3),
            "cross_strategy_bonus": round(cross_bonus, 3),
            "discovery_bonus": round(discovery_bonus, 3),
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
    clap_map: dict[str, list[float]] | None = None,
    user_clap_centroid: list[float] | None = None,
    mert_map: dict[str, list[float]] | None = None,
    user_mert_centroid: list[float] | None = None,
    recent_recommendations: list[dict[str, Any]] | None = None,
    calibration_artists: dict[str, float] | None = None,
    # Legacy params — accepted but ignored for backward compat
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
        cid = c.get("catalog_id", "")

        scored = score_candidate(
            c, profile, already_selected=None,
            weights=weights,
            audio_features=af_map.get(cid),
            user_audio_centroid=user_audio_centroid,
            candidate_embedding=emb_map.get(cid),
            user_embedding_centroid=user_embedding_centroid,
            candidate_clap=(clap_map or {}).get(cid),
            user_clap_centroid=user_clap_centroid,
            candidate_mert=(mert_map or {}).get(cid),
            user_mert_centroid=user_mert_centroid,
            staleness_index=staleness_idx,
            calibration_artists=calibration_artists,
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
