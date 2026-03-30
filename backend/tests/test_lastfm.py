"""Tests for Phase 10: Last.fm tag enrichment."""

from __future__ import annotations

import pytest

from musicmind.engine.lastfm import (
    combined_genre_similarity,
    tag_jaccard_similarity,
)


# ── Tag Jaccard similarity tests ─────────────────────────────────────────


def test_tag_jaccard_identical() -> None:
    """Identical tag sets have similarity 1.0."""
    tags = {"rock": 0.8, "indie": 0.5, "alternative": 0.3}
    assert tag_jaccard_similarity(tags, tags) == pytest.approx(1.0)


def test_tag_jaccard_empty() -> None:
    """Empty tag sets have similarity 0.0."""
    assert tag_jaccard_similarity({}, {}) == 0.0
    assert tag_jaccard_similarity({"rock": 0.5}, {}) == 0.0
    assert tag_jaccard_similarity({}, {"rock": 0.5}) == 0.0


def test_tag_jaccard_partial_overlap() -> None:
    """Partially overlapping tags have intermediate similarity."""
    a = {"rock": 0.8, "indie": 0.5}
    b = {"rock": 0.6, "pop": 0.4}
    sim = tag_jaccard_similarity(a, b)
    assert 0.0 < sim < 1.0


def test_tag_jaccard_no_overlap() -> None:
    """Non-overlapping tags have similarity 0.0."""
    a = {"rock": 0.8, "indie": 0.5}
    b = {"classical": 0.9, "jazz": 0.4}
    sim = tag_jaccard_similarity(a, b)
    assert sim == 0.0


def test_tag_jaccard_weighted() -> None:
    """Higher weights on shared tags increase similarity."""
    a = {"rock": 1.0, "indie": 0.1}
    b = {"rock": 1.0, "pop": 0.1}
    sim = tag_jaccard_similarity(a, b)
    # rock:min(1,1)=1, indie:min(0.1,0)=0, pop:min(0,0.1)=0 → sum=1
    # rock:max(1,1)=1, indie:max(0.1,0)=0.1, pop:max(0,0.1)=0.1 → sum=1.2
    assert sim == pytest.approx(1.0 / 1.2, abs=0.01)


# ── Combined genre similarity tests ─────────────────────────────────────


def test_combined_genre_blend() -> None:
    """Combined = 70% embedding + 30% tag."""
    result = combined_genre_similarity(0.8, 0.6)
    expected = 0.7 * 0.8 + 0.3 * 0.6
    assert result == pytest.approx(expected)


def test_combined_genre_zero_tag() -> None:
    """When tag similarity is 0, still uses embedding."""
    result = combined_genre_similarity(0.9, 0.0)
    assert result == pytest.approx(0.63)


def test_combined_genre_zero_embedding() -> None:
    """When embedding similarity is 0, still uses tags."""
    result = combined_genre_similarity(0.0, 0.9)
    assert result == pytest.approx(0.27)
