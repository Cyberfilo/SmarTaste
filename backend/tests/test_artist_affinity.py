"""Test build_artist_affinity saturation and play-dominance invariants."""
from __future__ import annotations

from musicmind.engine.profile import build_artist_affinity


def test_library_presence_saturates_with_log1p() -> None:
    """50 library songs by an artist should NOT exceed contribution from ~3 plays."""
    songs = [{"artist_name": "Heavy Library", "catalog_id": f"s{i}"} for i in range(50)]
    history = [
        {"artist_name": "Actually Played", "song_id": "p1"},
        {"artist_name": "Actually Played", "song_id": "p2"},
        {"artist_name": "Actually Played", "song_id": "p3"},
    ]

    result = build_artist_affinity(songs, history)
    by_name = {r["name"]: r["score"] for r in result}

    assert by_name["Actually Played"] > by_name["Heavy Library"], (
        f"Plays should dominate presence. Got {by_name}"
    )


def test_single_library_song_still_counts() -> None:
    """log1p(1) ≈ 0.69, so a single library song still contributes non-trivially."""
    songs = [{"artist_name": "Solo", "catalog_id": "s1"}]
    result = build_artist_affinity(songs, [])
    assert len(result) == 1
    assert result[0]["name"] == "Solo"
    assert result[0]["score"] > 0.0


def test_library_presence_caps_at_3() -> None:
    """Saturation cap: even 1000 library songs by one artist shouldn't exceed 3.0 raw."""
    songs = [{"artist_name": "Overflow", "catalog_id": f"s{i}"} for i in range(1000)]
    history = [{"artist_name": "Active", "song_id": f"h{i}"} for i in range(20)]
    result = build_artist_affinity(songs, history)
    by_name = {r["name"]: r["score"] for r in result}
    assert by_name["Active"] / max(by_name["Overflow"], 0.001) > 20.0, (
        f"Cap not effective: {by_name}"
    )
