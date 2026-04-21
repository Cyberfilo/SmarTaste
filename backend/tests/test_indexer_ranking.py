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


def test_calibration_dominates_frequency_when_user_picked() -> None:
    """V 6.363 contract: wizard-ranked artists outrank any uncalibrated one.

    A weight-5 top_artist pick with only 3 library songs must outrank a
    200-song uncalibrated artist — because the user explicitly told the
    app "these are my top artists". Library frequency is a within-tier
    tiebreaker, not the primary signal.
    """
    freq_map = {"Baby Gang": 200, "Simba La Rue": 3}
    cal_weights = {"simba la rue": 5.0}
    ranked = _rank_artists_by_affinity(freq_map, cal_weights)
    names = [n for n, _ in ranked]
    assert names.index("Simba La Rue") < names.index("Baby Gang"), (
        f"Calibrated Simba La Rue should outrank uncalibrated Baby Gang. Got: {names}"
    )


def test_frequency_breaks_ties_within_calibration_tier() -> None:
    """Two calibrated artists with the same weight: higher freq wins."""
    ranked = _rank_artists_by_affinity(
        freq_map={"Artist A": 10, "Artist B": 3},
        cal_weights={"artist a": 5.0, "artist b": 5.0},
    )
    names = [n for n, _ in ranked]
    assert names[0] == "Artist A", (
        f"Higher-freq calibrated artist wins the tiebreaker. Got: {names}"
    )


def test_uncalibrated_high_freq_still_surfaces() -> None:
    """Uncalibrated library artists still appear below calibrated ones."""
    ranked = _rank_artists_by_affinity(
        freq_map={"Heavy Listen": 100},
        cal_weights={},
    )
    names = [n for n, _ in ranked]
    assert "Heavy Listen" in names
    assert ranked[0][1] == pytest.approx(1.0)


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


def test_calibration_only_artist_preserves_original_casing() -> None:
    """Non-standard artist casing (SZA, YUNGBLUD) survives the ranking."""
    ranked = _rank_artists_by_affinity(
        freq_map={},
        cal_weights={"SZA": 5.0, "YUNGBLUD": 4.0, "twenty one pilots": 3.0},
    )
    names = [n for n, _ in ranked]
    assert "SZA" in names
    assert "YUNGBLUD" in names
    assert "twenty one pilots" in names
    # Specifically NOT mangled by .title()
    assert "Sza" not in names
    assert "Yungblud" not in names
    assert "Twenty One Pilots" not in names


def test_calibration_overlap_with_freq_uses_freq_casing() -> None:
    """When an artist exists in both freq and cal, freq's casing wins (display in library)."""
    ranked = _rank_artists_by_affinity(
        freq_map={"Baby Gang": 50},
        cal_weights={"baby gang": 5.0},   # different casing
    )
    names = [n for n, _ in ranked]
    assert "Baby Gang" in names
    assert "baby gang" not in names


def test_compute_depth_fraction_smooth_at_rank_cliff() -> None:
    score_3 = compute_depth_fraction(0.7)
    score_4 = compute_depth_fraction(0.6)
    assert abs(score_3 - score_4) < 0.15, (
        f"Depth curve should be smooth: {score_3=} {score_4=}"
    )
