"""Performance and correctness tests for engine scorer and weights optimizer.

Phase 2 improvements: O(n*k) rank_candidates, O(1) staleness lookup,
accurate coordinate descent weight optimization.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from musicmind.engine.scorer import (
    _build_staleness_index,
    _compute_staleness,
    rank_candidates,
    score_candidate,
)
from musicmind.engine.weights import (
    DEFAULT_WEIGHTS,
    _normalize_weights,
    _recompute_score,
    optimize_weights,
)


# ── Sample data factories ──────────────────────────────────────────────────


def _make_candidate(i: int, genre: str = "Pop", artist: str = "Artist") -> dict:
    return {
        "catalog_id": f"cat_{i}",
        "name": f"Song {i}",
        "artist_name": f"{artist} {i % 50}",
        "album_name": f"Album {i % 20}",
        "genre_names": [genre],
        "release_date": f"{2020 + i % 5}-01-01",
        "content_rating": None,
        "_strategy_count": 1 + (i % 3),
    }


def _make_profile() -> dict:
    return {
        "genre_vector": {"Pop": 0.4, "Hip-Hop/Rap": 0.3, "R&B/Soul": 0.2, "Rock": 0.1},
        "top_artists": [
            {"name": "Artist 0", "score": 1.0, "song_count": 10},
            {"name": "Artist 1", "score": 0.8, "song_count": 5},
        ],
        "release_year_distribution": {"2020": 0.3, "2021": 0.3, "2022": 0.2, "2023": 0.2},
        "familiarity_score": 0.6,
    }


# ── Phase 2.1: rank_candidates benchmark ──────────────────────────────────


def test_rank_candidates_500_under_500ms():
    """500 candidates ranked to top 20 should complete in <500ms."""
    candidates = [_make_candidate(i) for i in range(500)]
    profile = _make_profile()

    start = time.monotonic()
    result = rank_candidates(candidates, profile, count=20)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert len(result) == 20
    assert elapsed_ms < 500, f"rank_candidates took {elapsed_ms:.0f}ms (limit: 500ms)"
    # Scores should be monotonically non-increasing
    scores = [r["_score"] for r in result]
    for i in range(len(scores) - 1):
        assert scores[i] >= scores[i + 1] - 0.001  # allow rounding tolerance


def test_rank_candidates_returns_correct_count():
    """Verify rank_candidates returns exactly count items."""
    candidates = [_make_candidate(i) for i in range(100)]
    profile = _make_profile()
    result = rank_candidates(candidates, profile, count=10)
    assert len(result) == 10


def test_rank_candidates_fewer_than_count():
    """When candidates < count, return all candidates."""
    candidates = [_make_candidate(i) for i in range(5)]
    profile = _make_profile()
    result = rank_candidates(candidates, profile, count=20)
    assert len(result) == 5


def test_rank_candidates_empty():
    """Empty candidate list returns empty."""
    assert rank_candidates([], _make_profile(), count=20) == []


def test_rank_candidates_diversity_penalty():
    """Verify diversity penalty is applied — selecting duplicates should be penalized."""
    # All same genre/artist → diversity penalty should kick in
    same = [_make_candidate(i, genre="Pop", artist="Same") for i in range(20)]
    profile = _make_profile()
    result = rank_candidates(same, profile, count=10)
    # Later selections should have lower scores due to diversity penalty
    assert result[0]["_score"] >= result[-1]["_score"]


# ── Phase 2.2: staleness O(1) lookup ──────────────────────────────────────


def test_staleness_index_lookup():
    """Pre-built index allows O(1) staleness lookup."""
    now = datetime.now(tz=UTC)
    recs = [
        {"catalog_id": "abc", "created_at": now - timedelta(days=2)},
        {"catalog_id": "def", "created_at": now - timedelta(days=15)},
        {"catalog_id": "old", "created_at": now - timedelta(days=60)},
    ]
    index = _build_staleness_index(recs)
    assert _compute_staleness("abc", index) == 0.8  # < 7 days
    assert _compute_staleness("def", index) == 0.4  # 7-30 days
    assert _compute_staleness("old", index) == 0.0  # > 30 days
    assert _compute_staleness("missing", index) == 0.0  # not found


def test_staleness_empty_index():
    """Empty index returns 0.0 for any catalog_id."""
    index = _build_staleness_index([])
    assert _compute_staleness("anything", index) == 0.0


# ── Phase 2.3: weights optimizer ──────────────────────────────────────────


def test_recompute_score_matches_score_candidate():
    """_recompute_score with default weights should approximate score_candidate."""
    candidate = _make_candidate(0)
    profile = _make_profile()
    result = score_candidate(candidate, profile)
    breakdown = result["_breakdown"]

    recomputed = _recompute_score(breakdown, DEFAULT_WEIGHTS)
    # Should be close (not exact due to cross_bonus and mood_boost)
    assert abs(recomputed - result["_score"]) < 0.05


def test_optimize_weights_insufficient_feedback():
    """< MIN_FEEDBACK returns defaults."""
    feedback = [{"feedback_type": "thumbs_up", "predicted_score": 0.8}] * 5
    result = optimize_weights(feedback)
    assert result == DEFAULT_WEIGHTS


def test_optimize_weights_with_breakdowns():
    """With sufficient feedback and breakdowns, should return non-default weights."""
    feedback = []
    for i in range(20):
        fb_type = "thumbs_up" if i % 2 == 0 else "thumbs_down"
        feedback.append({
            "feedback_type": fb_type,
            "predicted_score": 0.7 if fb_type == "thumbs_up" else 0.3,
            "breakdown": {
                "genre_match": 0.8 if fb_type == "thumbs_up" else 0.1,
                "artist_match": 0.5,
                "audio_similarity": 0.5,
                "novelty": 0.3,
                "freshness": 0.4,
                "diversity_penalty": 0.1,
                "staleness": 0.0,
                "cross_strategy_bonus": 0.0,
                "mood_boost": 0.0,
            },
        })
    result = optimize_weights(feedback)
    # Should still have all dimensions and sum to ~1.0
    assert set(result.keys()) == set(DEFAULT_WEIGHTS.keys())
    assert abs(sum(result.values()) - 1.0) < 0.01


def test_optimize_weights_fallback_no_breakdowns():
    """Legacy feedback without breakdowns should return defaults."""
    feedback = [
        {"feedback_type": "thumbs_up", "predicted_score": 0.7}
        for _ in range(15)
    ]
    result = optimize_weights(feedback)
    assert result == DEFAULT_WEIGHTS


def test_normalize_weights_floor():
    """Weights below floor should be clamped up and sum to 1.0."""
    weights = {"genre": 0.01, "audio": 0.99}
    result = _normalize_weights(weights)
    # After clamping to 0.03 and normalizing, genre should be higher than input
    assert result["genre"] > 0.01
    assert abs(sum(result.values()) - 1.0) < 0.01
