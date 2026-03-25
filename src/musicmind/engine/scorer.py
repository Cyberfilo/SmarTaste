"""Candidate scoring — rank catalog songs against a taste profile.

Uses genre cosine similarity, artist affinity, novelty bonuses,
freshness matching, and MMR-style diversity penalties.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from musicmind.engine.profile import expand_genres


def _genre_cosine(
    song_genres: list[str],
    genre_vector: dict[str, float],
) -> float:
    """Cosine similarity between a song's genres and the taste profile genre vector."""
    if not song_genres or not genre_vector:
        return 0.0

    expanded = expand_genres(song_genres)
    # Build aligned vectors
    all_genres = set(genre_vector.keys()) | set(expanded)
    profile_vec = np.array([genre_vector.get(g, 0.0) for g in all_genres])
    # Song vector: uniform weight across its genres
    song_weight = 1.0 / len(expanded) if expanded else 0.0
    song_vec = np.array([song_weight if g in expanded else 0.0 for g in all_genres])

    dot = np.dot(profile_vec, song_vec)
    norm_a = np.linalg.norm(profile_vec)
    norm_b = np.linalg.norm(song_vec)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def score_candidate(
    candidate: dict[str, Any],
    profile: dict[str, Any],
    already_selected: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score a single candidate song against a taste profile.

    Returns the candidate dict augmented with:
    - _score: overall score (0-1)
    - _breakdown: per-dimension scores
    - _explanation: human-readable explanation
    """
    genre_vector = profile.get("genre_vector", {})
    top_artists = profile.get("top_artists", [])
    release_dist = profile.get("release_year_distribution", {})

    # 1. Genre match (cosine similarity)
    genre_score = _genre_cosine(
        candidate.get("genre_names", []), genre_vector
    )

    # 2. Artist match
    artist_name = candidate.get("artist_name", "").lower()
    artist_scores = {a["name"].lower(): a["score"] for a in top_artists}
    artist_match = artist_scores.get(artist_name, 0.0)

    # 3. Novelty bonus: genre matches but artist is new
    known_artists = {a["name"].lower() for a in top_artists}
    novelty = 0.0
    if genre_score > 0.3 and artist_name not in known_artists:
        novelty = 0.3  # bonus for new artist in a familiar genre

    # 4. Freshness: match release year to user's distribution
    freshness = 0.5  # neutral default
    release_date = candidate.get("release_date", "")
    if release_date and len(release_date) >= 4:
        year = release_date[:4]
        freshness = release_dist.get(year, 0.0)
        # Boost very recent releases slightly
        try:
            if int(year) >= 2024:
                freshness = max(freshness, 0.3)
        except ValueError:
            pass

    # 5. Diversity penalty (MMR-style)
    diversity_penalty = 0.0
    if already_selected:
        from musicmind.engine.similarity import song_similarity

        max_sim = max(
            song_similarity(candidate, s) for s in already_selected
        )
        diversity_penalty = max_sim * 0.3  # penalize up to 30%

    # Weighted combination
    overall = (
        0.35 * genre_score
        + 0.20 * artist_match
        + 0.15 * novelty
        + 0.15 * freshness
        + 0.15 * (1.0 - diversity_penalty)
    )
    overall = max(0.0, min(1.0, overall))

    # Build explanation
    parts = []
    if genre_score > 0.5:
        top_genres = ", ".join(candidate.get("genre_names", [])[:3])
        parts.append(f"strong genre match ({top_genres})")
    if artist_match > 0.5:
        parts.append(f"you like {candidate.get('artist_name', 'this artist')}")
    if novelty > 0:
        parts.append("new artist in a genre you enjoy")
    if diversity_penalty > 0.2:
        parts.append("slight diversity penalty")

    return {
        **candidate,
        "_score": round(overall, 3),
        "_breakdown": {
            "genre_match": round(genre_score, 3),
            "artist_match": round(artist_match, 3),
            "novelty": round(novelty, 3),
            "freshness": round(freshness, 3),
            "diversity_penalty": round(diversity_penalty, 3),
        },
        "_explanation": "; ".join(parts) if parts else "moderate match",
    }


def rank_candidates(
    candidates: list[dict[str, Any]],
    profile: dict[str, Any],
    count: int = 20,
) -> list[dict[str, Any]]:
    """Rank candidates using MMR-style scoring with diversity.

    Selects top candidates one at a time, applying diversity penalty
    based on similarity to already-selected songs.
    """
    if not candidates:
        return []

    selected: list[dict[str, Any]] = []
    remaining = list(candidates)

    for _ in range(min(count, len(remaining))):
        scored = [
            score_candidate(c, profile, selected)
            for c in remaining
        ]
        scored.sort(key=lambda x: x["_score"], reverse=True)

        best = scored[0]
        selected.append(best)
        # Remove the selected candidate from remaining
        remaining = [
            c for c in remaining
            if c.get("catalog_id") != best.get("catalog_id")
        ]

    return selected
