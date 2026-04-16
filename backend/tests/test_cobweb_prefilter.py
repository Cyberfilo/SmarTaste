"""Tests for embedding-similarity pre-filter before cobweb enrichment."""
from __future__ import annotations

from musicmind.engine.cobweb import prefilter_by_centroid_similarity


def test_keeps_top_fraction_by_cosine() -> None:
    """Keep top 50% by cosine similarity to centroid; drop bottom 50%."""
    centroid = [1.0, 0.0, 0.0]
    tracks = [
        {"catalog_id": "aligned", "effnet_embedding": [1.0, 0.0, 0.0]},
        {"catalog_id": "perpendicular", "effnet_embedding": [0.0, 1.0, 0.0]},
        {"catalog_id": "slight", "effnet_embedding": [0.9, 0.1, 0.0]},
        {"catalog_id": "opposite", "effnet_embedding": [-1.0, 0.0, 0.0]},
    ]
    kept = prefilter_by_centroid_similarity(
        tracks=tracks,
        centroid=centroid,
        keep_fraction=0.5,
    )
    kept_ids = {t["catalog_id"] for t in kept}
    assert kept_ids == {"aligned", "slight"}


def test_tracks_without_embedding_pass_through() -> None:
    """Tracks with no embedding are kept (can't filter unknown, must enrich to learn)."""
    centroid = [1.0, 0.0]
    tracks = [
        {"catalog_id": "unknown_a"},
        {"catalog_id": "has_emb", "effnet_embedding": [0.5, 0.5]},
    ]
    kept = prefilter_by_centroid_similarity(
        tracks=tracks, centroid=centroid, keep_fraction=0.5,
    )
    kept_ids = {t["catalog_id"] for t in kept}
    assert "unknown_a" in kept_ids


def test_no_centroid_is_passthrough() -> None:
    """If no centroid (cold-start user), skip filtering entirely."""
    tracks = [{"catalog_id": f"t{i}"} for i in range(5)]
    kept = prefilter_by_centroid_similarity(
        tracks=tracks, centroid=None, keep_fraction=0.7,
    )
    assert len(kept) == 5


def test_keep_fraction_bounds() -> None:
    """Always keep at least 1 track; never more than input."""
    centroid = [1.0]
    tracks = [{"catalog_id": "t1", "effnet_embedding": [1.0]}]
    assert len(prefilter_by_centroid_similarity(
        tracks=tracks, centroid=centroid, keep_fraction=0.1
    )) >= 1
    assert len(prefilter_by_centroid_similarity(
        tracks=tracks, centroid=centroid, keep_fraction=2.0
    )) == 1
