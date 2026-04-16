"""Test that _discovery_weight on a candidate boosts its score."""
from __future__ import annotations

from musicmind.engine.scorer import score_candidate


def test_discovery_weight_boosts_score() -> None:
    base = {
        "catalog_id": "x", "name": "X", "artist_name": "A",
        "genre_names": ["Pop"],
    }
    boosted = dict(base, _discovery_weight=1.0)

    profile = {
        "genre_vector": {"Pop": 1.0},
        "top_artists": [],
    }

    base_score = score_candidate(base, profile)["_score"]
    boosted_score = score_candidate(boosted, profile)["_score"]

    assert boosted_score > base_score, (
        f"discovery_weight should boost score: {base_score=} {boosted_score=}"
    )
    assert boosted_score - base_score <= 0.05, "Bonus should be capped"


def test_discovery_weight_zero_is_no_op() -> None:
    base = {
        "catalog_id": "x", "name": "X", "artist_name": "A",
        "genre_names": ["Pop"],
    }
    with_zero = dict(base, _discovery_weight=0.0)
    profile = {"genre_vector": {"Pop": 1.0}, "top_artists": []}
    assert score_candidate(base, profile)["_score"] == score_candidate(with_zero, profile)["_score"]
