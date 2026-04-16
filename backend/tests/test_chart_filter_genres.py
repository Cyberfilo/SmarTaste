"""Tests that chart_filter uses expand_genres for near-match genre overlap."""
from __future__ import annotations

from musicmind.api.recommendations.fetch import _genre_overlap_with_expansion


def test_parent_matches_regional() -> None:
    """'Hip-Hop/Rap' chart track matches 'Italian Hip-Hop/Rap' profile."""
    assert _genre_overlap_with_expansion(
        ["Hip-Hop/Rap"], ["Italian Hip-Hop/Rap", "Drill"]
    ) is True


def test_regional_matches_parent() -> None:
    """'Italian Hip-Hop/Rap' chart track matches 'Hip-Hop/Rap' profile."""
    assert _genre_overlap_with_expansion(
        ["Italian Hip-Hop/Rap"], ["Hip-Hop/Rap"]
    ) is True


def test_no_overlap() -> None:
    """'Country' chart track does not match 'Italian Hip-Hop/Rap' profile."""
    assert _genre_overlap_with_expansion(
        ["Country"], ["Italian Hip-Hop/Rap", "Drill"]
    ) is False


def test_case_insensitive() -> None:
    assert _genre_overlap_with_expansion(["POP"], ["pop"]) is True


def test_empty_inputs() -> None:
    assert _genre_overlap_with_expansion([], ["Pop"]) is False
    assert _genre_overlap_with_expansion(["Pop"], []) is False
