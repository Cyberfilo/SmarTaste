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

    # Library songs — two-pass approach to avoid linear accumulation dominating plays.
    # Pass 1: accumulate counts, track max decay, record ratings per primary artist.
    library_counts: Counter[str] = Counter()
    library_decay: dict[str, float] = {}
    library_ratings: dict[str, int] = {}

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
            library_counts[name] += weight  # feat artists add 0.3, primaries add 1.0
            artist_song_counts[name] += 1
            # Keep the highest decay seen (most recently added song dominates)
            if decay > library_decay.get(name, 0.0):
                library_decay[name] = decay

        # Love/dislike ratings — record for primary artist only
        # First non-None rating per primary artist wins; duplicate ratings on same artist are not aggregated.
        rating = song.get("user_rating")
        primary_name = parsed[0][0] if parsed else raw_artist
        if rating is not None and primary_name not in library_ratings:
            library_ratings[primary_name] = rating

    # Pass 2: apply log-saturated presence score once per artist
    for name, count in library_counts.items():
        d = library_decay.get(name, 1.0)
        artist_scores[name] += min(3.0, 0.3 * math.log1p(count)) * d

    # Apply ratings: strong explicit signals — one per primary artist
    for name, rating in library_ratings.items():
        d = library_decay.get(name, 1.0)
        if rating == 1:
            artist_scores[name] += 4.0 * d
        elif rating == -1:
            artist_scores[name] -= 3.0 * d

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
    *,
    feedback_weights: dict[str, float] | None = None,
) -> list[float] | None:
    """Compute L2-normalized mean embedding from a catalog_id → embedding map.

    When feedback_weights is provided, thumbs-up tracks contribute 2x and
    thumbs-down tracks contribute 0.2x to the centroid. This lets the profile
    evolve with each interaction.
    """
    import numpy as np

    ids_and_embs = [
        (s.get("catalog_id", ""), emb_map[s.get("catalog_id", "")])
        for s in songs
        if s.get("catalog_id", "") in emb_map
    ]
    if not ids_and_embs:
        return None

    weights = []
    vectors = []
    for cid, emb in ids_and_embs:
        vectors.append(emb)
        w = 1.0
        if feedback_weights and cid in feedback_weights:
            w = feedback_weights[cid]
        weights.append(w)

    arr = np.array(vectors)
    w_arr = np.array(weights).reshape(-1, 1)
    mean = (arr * w_arr).sum(axis=0) / w_arr.sum()
    norm = float(np.linalg.norm(mean))
    if norm > 0:
        mean = mean / norm
    return [round(float(v), 6) for v in mean]


def _compute_multi_centroids(
    songs: list[dict[str, Any]],
    emb_map: dict[str, list[float]],
    *,
    max_clusters: int = 4,
    min_songs_per_cluster: int = 5,
    feedback_weights: dict[str, float] | None = None,
) -> list[list[float]] | None:
    """Cluster library embeddings into k taste clusters via k-means.

    Returns up to max_clusters L2-normalized centroids. Falls back to a
    single centroid if the library is too small to cluster meaningfully.
    """
    import numpy as np

    ids_and_embs = [
        (s.get("catalog_id", ""), emb_map[s.get("catalog_id", "")])
        for s in songs
        if s.get("catalog_id", "") in emb_map
    ]
    if not ids_and_embs:
        return None

    vectors = []
    weights = []
    for cid, emb in ids_and_embs:
        vectors.append(emb)
        w = 1.0
        if feedback_weights and cid in feedback_weights:
            w = feedback_weights[cid]
        weights.append(w)

    arr = np.array(vectors)
    n = len(arr)
    k = min(max_clusters, max(1, n // min_songs_per_cluster))
    if k <= 1:
        mean = _compute_embedding_centroid(
            songs, emb_map, feedback_weights=feedback_weights,
        )
        return [mean] if mean else None

    # Simple k-means (avoid sklearn dependency)
    rng = np.random.default_rng(42)
    indices = rng.choice(n, size=k, replace=False)
    centers = arr[indices].copy()
    w_arr = np.array(weights)

    for _ in range(20):
        dists = np.linalg.norm(
            arr[:, np.newaxis, :] - centers[np.newaxis, :, :], axis=2,
        )
        labels = dists.argmin(axis=1)
        new_centers = np.zeros_like(centers)
        for j in range(k):
            mask = labels == j
            if mask.sum() == 0:
                new_centers[j] = centers[j]
                continue
            cluster_w = w_arr[mask].reshape(-1, 1)
            new_centers[j] = (
                (arr[mask] * cluster_w).sum(axis=0) / cluster_w.sum()
            )
        if np.allclose(centers, new_centers, atol=1e-6):
            break
        centers = new_centers

    result = []
    for c in centers:
        norm = float(np.linalg.norm(c))
        if norm > 0:
            c = c / norm
        result.append([round(float(v), 6) for v in c])
    return result


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
    mood_tags_map: dict[str, list[str]] | None = None,
    mood_scores_map: dict[str, dict[str, float]] | None = None,
    feedback_weights: dict[str, float] | None = None,
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
        feedback_weights: Optional catalog_id → weight (2.0 for thumbs-up,
            0.2 for thumbs-down) — shifts centroids toward liked music.
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

    total_duration_ms = sum(
        (s.get("duration_ms") or 0) for s in songs
    )
    listening_hours = round(total_duration_ms / 3_600_000, 1)

    audio_centroid: dict[str, float] = {}
    tempo_band_dist: dict[str, float] | None = None
    halftime_fraction = 0.0
    if audio_features_map:
        feature_list = [
            audio_features_map[s.get("catalog_id", "")]
            for s in songs
            if s.get("catalog_id", "") in audio_features_map
        ]
        audio_centroid = build_audio_centroid(feature_list)

        # V 6.393: tempo-band histogram + halftime-feel ratio. Both fall out
        # of the same scalar feature map we already pass in — no new queries.
        # Engagement weights reuse feedback_weights when present so heavily-
        # played tracks dominate the distribution (matches user intuition).
        from musicmind.engine.tempo import (
            halftime_ratio,
            tempo_band_distribution,
        )
        bpms = [f.get("tempo") for f in feature_list]
        bss = [f.get("beat_strength") for f in feature_list]
        dances = [f.get("danceability") for f in feature_list]
        eng_weights = None
        if feedback_weights:
            eng_weights = [
                feedback_weights.get(s.get("catalog_id", ""), 1.0)
                for s in songs
                if s.get("catalog_id", "") in audio_features_map
            ]
        tempo_band_dist = tempo_band_distribution(bpms, weights=eng_weights)
        halftime_fraction = halftime_ratio(
            bpms, bss, dances, weights=eng_weights,
        )

    fw = feedback_weights
    embedding_centroid = (
        _compute_embedding_centroid(songs, embedding_map, feedback_weights=fw)
        if embedding_map else None
    )
    clap_centroid = (
        _compute_embedding_centroid(songs, clap_map, feedback_weights=fw)
        if clap_map else None
    )
    mert_centroid = (
        _compute_embedding_centroid(songs, mert_map, feedback_weights=fw)
        if mert_map else None
    )

    clap_centroids = (
        _compute_multi_centroids(songs, clap_map, feedback_weights=fw)
        if clap_map else None
    )
    mert_centroids = (
        _compute_multi_centroids(songs, mert_map, feedback_weights=fw)
        if mert_map else None
    )
    effnet_centroids = (
        _compute_multi_centroids(songs, embedding_map, feedback_weights=fw)
        if embedding_map else None
    )

    # V 6.388/V 6.389: mood distribution aggregated from library's
    # mood_scores (preferred, carries intensity) with mood_tags as
    # fallback for rows classified before the hybrid upgrade.
    mood_distribution: dict[str, float] = {}
    if mood_scores_map or mood_tags_map:
        from musicmind.engine.mood_tagger import aggregate_mood_distribution
        scores_list = [
            (mood_scores_map or {}).get(s.get("catalog_id", ""))
            for s in songs
        ]
        tags_list = [
            (mood_tags_map or {}).get(s.get("catalog_id", ""), [])
            for s in songs
        ]
        mood_distribution = aggregate_mood_distribution(
            scores_list, library_tags_fallback=tags_list,
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
        "clap_centroids": clap_centroids,
        "mert_centroids": mert_centroids,
        "effnet_centroids": effnet_centroids,
        "mood_distribution": mood_distribution or None,
        # V 6.393: tempo-band psychology + halftime-feel signature.
        # Drives the tempo_band scoring dim and the halftime tie-breaker.
        "tempo_band_distribution": tempo_band_dist,
        "halftime_ratio": halftime_fraction,
    }
