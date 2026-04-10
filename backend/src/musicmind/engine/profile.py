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

    "Italian Hip-Hop/Rap" -> ["Italian Hip-Hop/Rap", "Hip-Hop/Rap"]
    "UK Drill" -> ["UK Drill", "Drill"]
    "Pop" -> ["Pop"]
    """
    expanded = set()
    for genre in genre_names:
        expanded.add(genre)
        # Try to find a parent by removing common prefixes (space-separated first word)
        words = genre.split()
        if len(words) > 1:
            # "Italian Hip-Hop/Rap" -> "Hip-Hop/Rap"
            # "UK Drill" -> "Drill"
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


# ── Featuring Artist Parsing ──────────────────────────────────────────────

# Weight for featured artists relative to primary (0.3 = 30%)
FEATURING_WEIGHT = 0.3

import re  # noqa: E402

_FEAT_PATTERN = re.compile(
    r"\s*(?:\(|\[)?\s*(?:feat\.?|ft\.?|featuring)\s+",
    re.IGNORECASE,
)
_MULTI_ARTIST_SEP = re.compile(r"\s*(?:,\s*|\s+&\s+|\s+x\s+|\s+X\s+)")


def parse_artists(artist_name: str) -> list[tuple[str, float]]:
    """Parse an artist name string into (name, weight) tuples.

    Primary artist gets weight 1.0, featuring artists get FEATURING_WEIGHT.
    Handles patterns: "Artist A feat. Artist B", "A ft. B & C",
    "A (feat. B, C & D)", "A, B & C".

    Returns:
        List of (artist_name, weight) tuples. Never empty if input is non-empty.
    """
    if not artist_name or not artist_name.strip():
        return []

    # Split on feat./ft./featuring
    parts = _FEAT_PATTERN.split(artist_name, maxsplit=1)
    primary_part = parts[0].strip()
    featuring_part = parts[1].strip().rstrip(")").rstrip("]") if len(parts) > 1 else ""

    result: list[tuple[str, float]] = []

    # Primary artist(s) — may contain "A & B" or "A, B" (co-primaries)
    for name in _MULTI_ARTIST_SEP.split(primary_part):
        name = name.strip()
        if name:
            result.append((name, 1.0))

    # Featuring artist(s)
    if featuring_part:
        for name in _MULTI_ARTIST_SEP.split(featuring_part):
            name = name.strip()
            if name:
                result.append((name, FEATURING_WEIGHT))

    return result if result else [(artist_name.strip(), 1.0)]


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
    """Build artist affinity scores based on LISTENING frequency.

    Weights prioritize actual plays over library presence:
    - Library presence: 0.3 per song (baseline — having songs != listening)
    - Recent play: 3.0 per appearance (dominant signal)
    - Repeated play: 5.0 for songs played multiple times
    - Love rating: +4.0 (strong explicit signal)
    - Dislike rating: -3.0

    Returns sorted list of {name, score, song_count}.
    """
    artist_scores: dict[str, float] = Counter()
    artist_song_counts: dict[str, int] = Counter()
    now = datetime.now(tz=UTC)

    # Library songs: low weight (just having a song doesn't mean you listen)
    for song in songs:
        raw_artist = song.get("artist_name", "")
        if not raw_artist:
            continue
        decay = 1.0
        if use_temporal_decay:
            ts = song.get("date_added_to_library") or song.get("fetched_at")
            decay = temporal_decay_weight(ts, now, half_life_days)

        parsed = parse_artists(raw_artist)
        for name, weight in parsed:
            artist_scores[name] += 0.3 * decay * weight
            artist_song_counts[name] += 1

        # Love/dislike ratings are strong explicit signals
        rating = song.get("user_rating")
        primary_name = parsed[0][0] if parsed else raw_artist
        if rating == 1:
            artist_scores[primary_name] += 4.0 * decay
        elif rating == -1:
            artist_scores[primary_name] -= 3.0 * decay

    # Recent plays: the DOMINANT signal for artist affinity
    seen_song_ids: set[str] = set()
    for entry in history:
        raw_artist = entry.get("artist_name", "")
        if not raw_artist:
            continue
        decay = 1.0
        if use_temporal_decay:
            ts = entry.get("observed_at")
            decay = temporal_decay_weight(ts, now, half_life_days)

        song_id = entry.get("song_id", "")
        is_repeat = song_id in seen_song_ids
        seen_song_ids.add(song_id)

        # Repeated plays get higher weight than first plays
        play_weight = 5.0 if is_repeat else 3.0
        for name, weight in parse_artists(raw_artist):
            artist_scores[name] += play_weight * decay * weight

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

    import math

    for features, w in zip(audio_features_list, weights):
        has_valid = False
        for k in keys:
            val = features.get(k)
            if val is not None and not math.isnan(val):
                weighted_sums[k] += val * w
                has_valid = True
        if has_valid:
            total_weight += w

    if total_weight == 0:
        return {}

    return {k: round(v / total_weight, 3) for k, v in weighted_sums.items()}


def _compute_embedding_centroid(
    songs: list[dict[str, Any]],
    emb_map: dict[str, list[float]],
) -> list[float] | None:
    """Compute L2-normalized mean embedding from a catalog_id → embedding map.

    Returns None if no embeddings are available for the user's songs.
    """
    import numpy as np

    emb_list = [
        emb_map[s.get("catalog_id", "")]
        for s in songs
        if s.get("catalog_id", "") in emb_map
    ]
    if not emb_list:
        return None

    arr = np.array(emb_list)
    mean = arr.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm > 0:
        mean = mean / norm  # L2 normalize
    return [round(float(v), 6) for v in mean]


def build_taste_profile(
    songs: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    use_temporal_decay: bool = False,
    half_life_days: float = 90.0,
    audio_features_map: dict[str, dict[str, float]] | None = None,
    embedding_map: dict[str, list[float]] | None = None,
    clap_map: dict[str, list[float]] | None = None,
    mert_map: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """Build a complete taste profile from cached data.

    Args:
        songs: All songs from song_metadata_cache
        history: Listening history entries
        use_temporal_decay: Apply exponential decay to older songs
        half_life_days: Half-life in days for temporal decay
        audio_features_map: Optional catalog_id → scalar audio features dict
        embedding_map: Optional catalog_id → EffNet embedding (1280-dim)
        clap_map: Optional catalog_id → CLAP embedding (512-dim)
        mert_map: Optional catalog_id → MERT embedding (768-dim)

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

    # Build audio centroid from enriched scalar features
    audio_centroid: dict[str, float] = {}
    if audio_features_map:
        feature_list = [
            audio_features_map[s.get("catalog_id", "")]
            for s in songs
            if s.get("catalog_id", "") in audio_features_map
        ]
        audio_centroid = build_audio_centroid(feature_list)

    # Build embedding centroids (L2-normalized mean per model)
    embedding_centroid = (
        _compute_embedding_centroid(songs, embedding_map)
        if embedding_map else None
    )
    clap_centroid = (
        _compute_embedding_centroid(songs, clap_map)
        if clap_map else None
    )
    mert_centroid = (
        _compute_embedding_centroid(songs, mert_map)
        if mert_map else None
    )

    return {
        "genre_vector": genre_vector,
        "top_artists": top_artists,
        "audio_trait_preferences": audio_prefs,
        "release_year_distribution": release_dist,
        "familiarity_score": familiarity,
        "total_songs_analyzed": len(songs),
        "listening_hours_estimated": listening_hours,
        "audio_centroid": audio_centroid,
        "embedding_centroid": embedding_centroid,
        "clap_centroid": clap_centroid,
        "mert_centroid": mert_centroid,
    }
