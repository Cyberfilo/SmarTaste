"""Tests for shared cobweb ranking logic."""
from __future__ import annotations

from musicmind.engine.cobweb import rank_cobweb_candidates


def test_sum_accumulates_co_occurrence() -> None:
    """An artist featured 10 times should outrank one featured once."""
    library_rows = [
        {"artist_name": "Main feat. Prolific", "primary_affinity": 1.0}
    ] * 10 + [
        {"artist_name": "Main feat. Rare", "primary_affinity": 1.0}
    ]
    library_names = {"main"}
    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names=library_names,
        existing_cobweb_names=set(),
    )
    names_by_priority = [name for name, _ in ranked]
    assert names_by_priority[0].lower() == "prolific"
    by_name = {n.lower(): p for n, p in ranked}
    ratio = by_name["prolific"] / max(by_name["rare"], 0.001)
    assert 2.0 < ratio < 6.0, f"Expected log-dampened ratio 2-6x, got {ratio:.2f}"


def test_primary_affinity_weights_contributions() -> None:
    """A feat on the top artist's track should outrank one on a tail-artist track."""
    library_rows = [
        {"artist_name": "TopArtist feat. Alice", "primary_affinity": 1.0},
        {"artist_name": "TailArtist feat. Bob", "primary_affinity": 0.1},
    ]
    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names={"topartist", "tailartist"},
        existing_cobweb_names=set(),
    )
    scores = {n.lower(): p for n, p in ranked}
    assert scores["alice"] > scores["bob"], (
        f"Feat on top artist should outrank feat on tail artist. Got {scores}"
    )


def test_library_artists_excluded() -> None:
    """Don't add library artists back to the cobweb."""
    library_rows = [
        {"artist_name": "A feat. B", "primary_affinity": 1.0},
    ]
    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names={"a", "b"},
        existing_cobweb_names=set(),
    )
    assert ranked == []


def test_cap_based_on_feat_density_not_library_size() -> None:
    """Cap should reflect the number of unique featured names present."""
    library_rows = [
        {"artist_name": f"Main feat. Feat{i}", "primary_affinity": 1.0}
        for i in range(7)
    ]
    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names={"main"},
        existing_cobweb_names=set(),
        max_total=100,
    )
    assert len(ranked) == 7
