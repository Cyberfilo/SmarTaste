"""TasteProfile builder — genre vectors, artist affinity, temporal patterns.

Builds a comprehensive taste profile from locally cached data (never calls the API).
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, datetime
from typing import Any


def expand_genres(genre_names: list[str]) -> list[str]:
    """Expand hierarchical genres by extracting parent genres.

    Apple Music uses patterns like "Italian Hip-Hop/Rap" where "Hip-Hop/Rap"
    is the parent genre. We detect regional/style prefixes and extract the parent.

    "Italian Hip-Hop/Rap" → ["Italian Hip-Hop/Rap", "Hip-Hop/Rap"]
    "UK Drill" → ["UK Drill", "Drill"]
    "Pop" → ["Pop"]
    """
    expanded = set()
    for genre in genre_names:
        expanded.add(genre)
        # Try to find a parent by removing common prefixes (space-separated first word)
        words = genre.split()
        if len(words) > 1:
            # "Italian Hip-Hop/Rap" → "Hip-Hop/Rap"
            # "UK Drill" → "Drill"
            parent = " ".join(words[1:])
            expanded.add(parent)
    return list(expanded)


def temporal_decay_weight(
    timestamp: datetime | str | None,
    now: datetime,
    half_life_days: float = 90.0,
) -> float:
    """Compute exponential decay weight based on age.

    Returns 1.0 for now, 0.5 at half_life, 0.25 at 2x half_life.
    Returns 0.5 (neutral) for None timestamps.
    """
    if timestamp is None:
        return 0.5
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except (ValueError, TypeError):
            return 0.5
    # Make timezone-aware if needed
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    age_days = max(0.0, (now - timestamp).total_seconds() / 86400.0)
    return 2.0 ** (-age_days / half_life_days)


def build_genre_vector(
    songs: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    use_temporal_decay: bool = False,
    half_life_days: float = 90.0,
) -> dict[str, float]:
    """Build a normalized genre affinity vector.

    Songs from the library count 1x, songs from recent history count 2x.
    Genres are expanded hierarchically.
    When use_temporal_decay is True, each song's contribution is multiplied
    by an exponential decay weight based on its timestamp.
    """
    counter: Counter[str] = Counter()
    now = datetime.now(tz=UTC)

    # Library songs: 1x weight (optionally decayed)
    # Regional genre prioritization: original genre gets full weight,
    # expanded parent gets 0.3x to preserve regional preference signal
    for song in songs:
        genres = song.get("genre_names") or []
        if isinstance(genres, str):
            genres = [genres]

        base_weight = 1.0
        if use_temporal_decay:
            ts = song.get("date_added_to_library") or song.get("fetched_at")
            base_weight *= temporal_decay_weight(ts, now, half_life_days)

        originals = set(genres)
        for g in expand_genres(genres):
            if g in originals:
                counter[g] += base_weight
            else:
                counter[g] += base_weight * 0.3

    # Recent plays: 2x weight (recency bias)
    seen_song_ids = set()
    for entry in history:
        song_id = entry.get("song_id", "")
        if song_id in seen_song_ids:
            weight = 3.0
        else:
            weight = 2.0
            seen_song_ids.add(song_id)

        if use_temporal_decay:
            ts = entry.get("observed_at")
            weight *= temporal_decay_weight(ts, now, half_life_days)

        genres = entry.get("genre_names") or []
        if isinstance(genres, str):
            genres = [genres]
        originals = set(genres)
        for g in expand_genres(genres):
            if g in originals:
                counter[g] += weight
            else:
                counter[g] += weight * 0.3

    # Normalize to sum=1.0
    total = sum(counter.values())
    if total == 0:
        return {}
    return {genre: count / total for genre, count in counter.most_common()}


def build_artist_affinity(
    songs: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    use_temporal_decay: bool = False,
    half_life_days: float = 90.0,
) -> list[dict[str, Any]]:
    """Build artist affinity scores.

    Score = (library_song_count * 1) + (recent_play_appearances * 2) + (love_rating * 3)
    Returns sorted list of {name, score, song_count}.
    """
    artist_scores: dict[str, float] = Counter()
    artist_song_counts: dict[str, int] = Counter()
    now = datetime.now(tz=UTC)

    for song in songs:
        artist = song.get("artist_name", "")
        if not artist:
            continue
        decay = 1.0
        if use_temporal_decay:
            ts = song.get("date_added_to_library") or song.get("fetched_at")
            decay = temporal_decay_weight(ts, now, half_life_days)

        artist_scores[artist] += 1.0 * decay
        artist_song_counts[artist] += 1
        rating = song.get("user_rating")
        if rating == 1:
            artist_scores[artist] += 3.0 * decay
        elif rating == -1:
            artist_scores[artist] -= 2.0 * decay

    for entry in history:
        artist = entry.get("artist_name", "")
        if artist:
            decay = 1.0
            if use_temporal_decay:
                ts = entry.get("observed_at")
                decay = temporal_decay_weight(ts, now, half_life_days)
            artist_scores[artist] += 2.0 * decay

    # Normalize by max score
    if not artist_scores:
        return []
    max_score = max(artist_scores.values())
    if max_score <= 0:
        return []

    result = []
    for artist, score in artist_scores.most_common():
        if score <= 0:
            continue
        result.append({
            "name": artist,
            "score": round(score / max_score, 3),
            "song_count": artist_song_counts.get(artist, 0),
        })

    return result


def build_release_year_distribution(songs: list[dict[str, Any]]) -> dict[str, float]:
    """Build a histogram of release years."""
    year_counts: Counter[str] = Counter()
    for song in songs:
        release_date = song.get("release_date", "")
        if release_date and len(release_date) >= 4:
            year = release_date[:4]
            year_counts[year] += 1

    total = sum(year_counts.values())
    if total == 0:
        return {}
    return {year: round(count / total, 3) for year, count in year_counts.most_common()}


def build_audio_trait_preferences(songs: list[dict[str, Any]]) -> dict[str, float]:
    """Calculate fraction of library with each audio trait."""
    trait_counts: Counter[str] = Counter()
    total = 0
    for song in songs:
        traits = song.get("audio_traits") or []
        if isinstance(traits, str):
            traits = [traits]
        if traits:
            for t in traits:
                trait_counts[t] += 1
        total += 1

    if total == 0:
        return {}
    return {trait: round(count / total, 3) for trait, count in trait_counts.most_common()}


def compute_familiarity_score(genre_vector: dict[str, float]) -> float:
    """Compute familiarity score based on genre vector entropy.

    0 = all listening concentrated in 1 genre (not adventurous)
    1 = evenly distributed across many genres (very adventurous)

    Uses normalized Shannon entropy.
    """
    if not genre_vector:
        return 0.0

    values = [v for v in genre_vector.values() if v > 0]
    if len(values) <= 1:
        return 0.0

    # Shannon entropy
    entropy = -sum(p * math.log2(p) for p in values)
    # Normalize by maximum possible entropy (uniform distribution)
    max_entropy = math.log2(len(values))
    if max_entropy == 0:
        return 0.0

    return round(entropy / max_entropy, 3)


def build_audio_centroid(
    audio_features_list: list[dict[str, float]],
    engagement_weights: list[float] | None = None,
) -> dict[str, float]:
    """Compute weighted average audio feature centroid.

    Args:
        audio_features_list: List of audio feature dicts (tempo, energy, etc.)
        engagement_weights: Optional weights per song (loved=3, recent=2, library=1)

    Returns:
        Dict with average audio features, or empty dict if no data.
    """
    if not audio_features_list:
        return {}

    keys = ["tempo", "energy", "brightness", "danceability",
            "acousticness", "valence_proxy", "beat_strength"]
    weights = engagement_weights or [1.0] * len(audio_features_list)

    weighted_sums: dict[str, float] = {k: 0.0 for k in keys}
    total_weight = 0.0

    for features, w in zip(audio_features_list, weights):
        for k in keys:
            val = features.get(k)
            if val is not None:
                weighted_sums[k] += val * w
        total_weight += w

    if total_weight == 0:
        return {}

    return {k: round(v / total_weight, 3) for k, v in weighted_sums.items()}


def build_taste_profile(
    songs: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    use_temporal_decay: bool = False,
    half_life_days: float = 90.0,
) -> dict[str, Any]:
    """Build a complete taste profile from cached data.

    Args:
        songs: All songs from song_metadata_cache
        history: Listening history entries
        use_temporal_decay: Apply exponential decay to older songs
        half_life_days: Half-life in days for temporal decay

    Returns:
        Dict ready for saving as a taste_profile_snapshot
    """
    genre_vector = build_genre_vector(
        songs, history,
        use_temporal_decay=use_temporal_decay,
        half_life_days=half_life_days,
    )
    top_artists = build_artist_affinity(
        songs, history,
        use_temporal_decay=use_temporal_decay,
        half_life_days=half_life_days,
    )
    release_dist = build_release_year_distribution(songs)
    audio_prefs = build_audio_trait_preferences(songs)
    familiarity = compute_familiarity_score(genre_vector)

    # Estimate listening hours
    total_duration_ms = sum(
        (s.get("duration_ms") or 0) for s in songs
    )
    listening_hours = round(total_duration_ms / 3_600_000, 1)

    return {
        "genre_vector": genre_vector,
        "top_artists": top_artists,
        "audio_trait_preferences": audio_prefs,
        "release_year_distribution": release_dist,
        "familiarity_score": familiarity,
        "total_songs_analyzed": len(songs),
        "listening_hours_estimated": listening_hours,
    }
