"""Song similarity scoring from metadata.

Compares songs by genre overlap, artist match, release year proximity,
and other metadata signals. No API calls — works purely from cached data.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from musicmind.engine.profile import expand_genres


def genre_jaccard(genres_a: list[str], genres_b: list[str]) -> float:
    """Jaccard similarity between two genre lists (with expansion)."""
    set_a = set(expand_genres(genres_a))
    set_b = set(expand_genres(genres_b))
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def year_proximity(date_a: str | None, date_b: str | None) -> float:
    """Score based on release year proximity (0-1, 1 = same year)."""
    if not date_a or not date_b:
        return 0.5  # neutral when unknown
    try:
        year_a = int(date_a[:4])
        year_b = int(date_b[:4])
    except (ValueError, IndexError):
        return 0.5
    diff = abs(year_a - year_b)
    # Decay: same year = 1.0, 10+ years apart = ~0.1
    return max(0.1, 1.0 / (1.0 + diff * 0.15))


def song_similarity(song_a: dict[str, Any], song_b: dict[str, Any]) -> float:
    """Compute similarity between two songs (0-1).

    Weighted combination of:
    - Genre overlap (Jaccard): 40%
    - Same artist bonus: 20%
    - Release year proximity: 15%
    - Same album bonus: 15%
    - Content rating match: 10%
    """
    genre_score = genre_jaccard(
        song_a.get("genre_names", []),
        song_b.get("genre_names", []),
    )
    artist_score = 1.0 if (
        song_a.get("artist_name", "").lower() == song_b.get("artist_name", "").lower()
        and song_a.get("artist_name", "")
    ) else 0.0
    year_score = year_proximity(
        song_a.get("release_date"),
        song_b.get("release_date"),
    )
    album_score = 1.0 if (
        song_a.get("album_name", "").lower() == song_b.get("album_name", "").lower()
        and song_a.get("album_name", "")
    ) else 0.0
    rating_score = 1.0 if (
        song_a.get("content_rating") == song_b.get("content_rating")
    ) else 0.5

    return (
        0.40 * genre_score
        + 0.20 * artist_score
        + 0.15 * year_score
        + 0.15 * album_score
        + 0.10 * rating_score
    )


def audio_feature_similarity(
    features_a: dict[str, float] | None,
    features_b: dict[str, float] | None,
) -> float:
    """Cosine similarity between two audio feature vectors.

    Returns 0.5 (neutral) if either is None.
    Normalizes tempo to 0-1 range before comparison.
    """
    if not features_a or not features_b:
        return 0.5

    keys = ["energy", "brightness", "danceability", "acousticness",
            "valence_proxy", "beat_strength"]

    def _norm_tempo(t: float | None) -> float:
        if t is None:
            return 0.5
        return max(0.0, min(1.0, (t - 40.0) / 180.0))

    vec_a = [_norm_tempo(features_a.get("tempo"))] + [
        features_a.get(k, 0.5) for k in keys
    ]
    vec_b = [_norm_tempo(features_b.get("tempo"))] + [
        features_b.get(k, 0.5) for k in keys
    ]

    a = np.array(vec_a)
    b = np.array(vec_b)
    dot = float(np.dot(a, b))
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0 or norm_b == 0:
        return 0.5
    return float(dot / (norm_a * norm_b))


def classification_similarity(
    labels_a: dict[str, float] | None,
    labels_b: dict[str, float] | None,
) -> float:
    """Jaccard-like similarity on classification labels weighted by confidence.

    Returns 0.0 if either is None or empty.
    """
    if not labels_a or not labels_b:
        return 0.0

    shared = set(labels_a.keys()) & set(labels_b.keys())
    if not shared:
        return 0.0

    # Weighted overlap: sum of min confidences / sum of max confidences
    min_sum = sum(min(labels_a[k], labels_b[k]) for k in shared)
    all_keys = set(labels_a.keys()) | set(labels_b.keys())
    max_sum = sum(
        max(labels_a.get(k, 0.0), labels_b.get(k, 0.0)) for k in all_keys
    )
    if max_sum == 0:
        return 0.0
    return min_sum / max_sum
