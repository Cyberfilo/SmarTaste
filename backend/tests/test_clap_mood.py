"""Tests for Phase 9: CLAP mood embeddings."""

from __future__ import annotations

import pytest

from musicmind.engine.clap_mood import (
    MOOD_DESCRIPTIONS,
    filter_candidates_by_mood_clap,
    mood_cosine_similarity,
    score_mood_clap,
)


# ── Mood cosine similarity tests ─────────────────────────────────────────


def test_mood_cosine_identical() -> None:
    """Identical embeddings have high similarity."""
    vec = [0.1 * i for i in range(512)]
    sim = mood_cosine_similarity(vec, vec)
    assert sim == pytest.approx(1.0, abs=0.01)


def test_mood_cosine_none() -> None:
    """Returns 0.5 when either embedding is None."""
    assert mood_cosine_similarity(None, [0.1] * 512) == 0.5
    assert mood_cosine_similarity([0.1] * 512, None) == 0.5


def test_mood_cosine_opposite() -> None:
    """Opposite embeddings have low similarity."""
    a = [0.1] * 512
    b = [-0.1] * 512
    sim = mood_cosine_similarity(a, b)
    assert sim < 0.1


def test_mood_cosine_mismatched_length() -> None:
    """Returns 0.5 for mismatched lengths."""
    assert mood_cosine_similarity([0.1] * 256, [0.1] * 512) == 0.5


# ── Mood descriptions completeness ──────────────────────────────────────


def test_all_standard_moods_have_descriptions() -> None:
    """All 6 standard moods have text descriptions."""
    expected = {"workout", "chill", "focus", "party", "sad", "driving"}
    assert set(MOOD_DESCRIPTIONS.keys()) == expected


# ── score_mood_clap tests ────────────────────────────────────────────────


def test_score_mood_clap_none_embedding() -> None:
    """Returns 0.5 when candidate has no CLAP embedding."""
    assert score_mood_clap("workout", None) == 0.5


# ── filter_candidates_by_mood_clap tests ─────────────────────────────────


def test_filter_no_clap_returns_all() -> None:
    """Without CLAP, all candidates returned with neutral boost."""
    candidates = [
        {"catalog_id": "a", "genre_names": ["Pop"]},
        {"catalog_id": "b", "genre_names": ["Rock"]},
    ]
    result = filter_candidates_by_mood_clap(candidates, "chill")
    assert len(result) == 2
    assert all(c.get("_mood_boost") == 0.0 for c in result)


def test_filter_no_embeddings_map_returns_all() -> None:
    """Without embeddings map, all candidates returned with neutral boost."""
    candidates = [
        {"catalog_id": "a", "genre_names": ["Pop"]},
    ]
    result = filter_candidates_by_mood_clap(
        candidates, "workout", clap_embeddings_map=None,
    )
    assert len(result) == 1
