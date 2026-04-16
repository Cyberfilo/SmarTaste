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


def test_saturation_cap_asymptotes() -> None:
    """100 songs and 1000 songs by the same artist produce near-identical scores."""
    # Both should saturate near 3.0 raw; normalized scores should be within 5%.
    songs_100 = [{"artist_name": "Saturating", "catalog_id": f"s{i}"} for i in range(100)]
    songs_1000 = [{"artist_name": "Saturating", "catalog_id": f"s{i}"} for i in range(1000)]

    r100 = build_artist_affinity(songs_100, [])
    r1000 = build_artist_affinity(songs_1000, [])

    # Both normalize to 1.0 since only one artist; this validates log1p monotonic-but-dampened.
    # So instead test the raw contribution by comparing against a "known active" artist.
    active = [{"artist_name": "Active", "song_id": f"h{i}"} for i in range(20)]

    combined_100 = build_artist_affinity(songs_100, active)
    combined_1000 = build_artist_affinity(songs_1000, active)

    by_100 = {r["name"]: r["score"] for r in combined_100}
    by_1000 = {r["name"]: r["score"] for r in combined_1000}

    # Saturating artist score should barely grow from 100 → 1000 songs.
    assert abs(by_100["Saturating"] - by_1000["Saturating"]) < 0.05, (
        f"Saturation should flatten the curve: {by_100=} vs {by_1000=}"
    )


def test_feat_artist_counts_less_than_primary() -> None:
    """Featured artists contribute less library presence than primaries."""
    # Same number of appearances, but one is always feat, one is always primary.
    songs = (
        [{"artist_name": "Primary", "catalog_id": f"p{i}"} for i in range(10)]
        + [{"artist_name": "Host feat. Featured", "catalog_id": f"f{i}"} for i in range(10)]
    )
    result = build_artist_affinity(songs, [])
    by_name = {r["name"]: r["score"] for r in result}
    assert by_name["Primary"] > by_name["Featured"], (
        f"Primary should outrank featured-only artist: {by_name}"
    )
