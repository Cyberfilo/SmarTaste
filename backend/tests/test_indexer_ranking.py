"""Tests for indexer pure ranking math (no DB)."""
from __future__ import annotations

import pytest

from musicmind.indexer import (
    _rank_artists_by_affinity,
    compute_depth_fraction,
)


def test_ranked_artists_returns_tuples_with_normalized_scores() -> None:
    freq_map = {"Baby Gang": 200, "Simba La Rue": 3}
    cal_weights = {"simba la rue": 5.0}
    ranked = _rank_artists_by_affinity(freq_map, cal_weights)
    assert isinstance(ranked, list)
    assert ranked, "expected at least one artist"
    for entry in ranked:
        assert isinstance(entry, tuple)
        name, score = entry
        assert isinstance(name, str) and name
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
    assert ranked[0][1] == pytest.approx(1.0)


def test_frequency_dominates_calibration_when_mismatched() -> None:
    freq_map = {"Baby Gang": 200, "Simba La Rue": 3}
    cal_weights = {"simba la rue": 5.0}
    ranked = _rank_artists_by_affinity(freq_map, cal_weights)
    names = [n for n, _ in ranked]
    assert names.index("Baby Gang") < names.index("Simba La Rue"), (
        f"Baby Gang should rank above Simba La Rue. Got: {names}"
    )


def test_calibration_boosts_without_replacing() -> None:
    ranked = _rank_artists_by_affinity(
        freq_map={"Baby Gang": 200, "Simba La Rue": 3},
        cal_weights={"simba la rue": 5.0},
    )
    scores = dict(ranked)
    assert scores["Simba La Rue"] > 0.0
    assert scores["Baby Gang"] > scores["Simba La Rue"]


def test_calibration_only_artist_gets_seeded_frequency() -> None:
    ranked = _rank_artists_by_affinity(
        freq_map={"Only Library": 10},
        cal_weights={"calibrated only": 5.0},
    )
    names = {n.lower() for n, _ in ranked}
    assert "calibrated only" in names


def test_empty_inputs_return_empty() -> None:
    assert _rank_artists_by_affinity({}, {}) == []


def test_compute_depth_fraction_continuous() -> None:
    assert compute_depth_fraction(1.0) == pytest.approx(1.0)
    assert compute_depth_fraction(0.5) == pytest.approx(0.6)
    assert compute_depth_fraction(0.1) == pytest.approx(0.15)
    assert compute_depth_fraction(0.0) == pytest.approx(0.15)


def test_compute_depth_fraction_smooth_at_rank_cliff() -> None:
    score_3 = compute_depth_fraction(0.7)
    score_4 = compute_depth_fraction(0.6)
    assert abs(score_3 - score_4) < 0.15, (
        f"Depth curve should be smooth: {score_3=} {score_4=}"
    )
