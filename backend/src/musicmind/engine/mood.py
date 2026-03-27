"""Context-aware mood filtering — genre heuristics + audio feature ranges.

Maps mood keywords to genre preferences and audio feature ranges,
then filters/boosts candidates accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MoodProfile:
    """Defines a mood/context with genre and audio feature constraints."""

    name: str
    preferred_genres: list[str] = field(default_factory=list)
    avoided_genres: list[str] = field(default_factory=list)
    energy_range: tuple[float, float] = (0.0, 1.0)
    tempo_range: tuple[float, float] = (0.0, 300.0)
    beat_strength_range: tuple[float, float] = (0.0, 1.0)
    acousticness_range: tuple[float, float] = (0.0, 1.0)
    danceability_range: tuple[float, float] = (0.0, 1.0)
    valence_range: tuple[float, float] = (0.0, 1.0)
    description: str = ""


MOOD_PROFILES: dict[str, MoodProfile] = {
    "workout": MoodProfile(
        name="workout",
        preferred_genres=["Hip-Hop/Rap", "Dance", "Electronic", "Pop", "Drill"],
        avoided_genres=["Classical", "Ambient", "Jazz"],
        energy_range=(0.7, 1.0),
        tempo_range=(120.0, 180.0),
        beat_strength_range=(0.6, 1.0),
        description="High-energy music for exercise",
    ),
    "chill": MoodProfile(
        name="chill",
        preferred_genres=["R&B/Soul", "Electronic", "Ambient", "Jazz", "Lo-Fi"],
        avoided_genres=["Metal", "Punk", "Drill"],
        energy_range=(0.0, 0.4),
        tempo_range=(60.0, 110.0),
        acousticness_range=(0.3, 1.0),
        description="Relaxed, low-energy vibes",
    ),
    "focus": MoodProfile(
        name="focus",
        preferred_genres=["Electronic", "Ambient", "Classical", "Lo-Fi"],
        avoided_genres=["Hip-Hop/Rap", "Pop", "Drill"],
        energy_range=(0.2, 0.6),
        tempo_range=(70.0, 120.0),
        beat_strength_range=(0.0, 0.4),
        description="Background music for concentration",
    ),
    "party": MoodProfile(
        name="party",
        preferred_genres=["Pop", "Dance", "Hip-Hop/Rap", "Reggaeton", "Electronic"],
        avoided_genres=["Classical", "Ambient", "Folk"],
        energy_range=(0.6, 1.0),
        tempo_range=(100.0, 140.0),
        danceability_range=(0.6, 1.0),
        description="Upbeat music for socializing",
    ),
    "sad": MoodProfile(
        name="sad",
        preferred_genres=["R&B/Soul", "Singer/Songwriter", "Indie", "Folk"],
        avoided_genres=["Dance", "Electronic", "Drill"],
        energy_range=(0.0, 0.4),
        tempo_range=(50.0, 100.0),
        valence_range=(0.0, 0.4),
        description="Melancholic, emotional music",
    ),
    "driving": MoodProfile(
        name="driving",
        preferred_genres=["Rock", "Hip-Hop/Rap", "Electronic", "Pop", "Drill"],
        avoided_genres=["Classical", "Ambient"],
        energy_range=(0.4, 0.9),
        tempo_range=(90.0, 140.0),
        description="Road trip and driving music",
    ),
}


def get_mood_profile(mood: str) -> MoodProfile | None:
    """Get a mood profile by name (case-insensitive)."""
    return MOOD_PROFILES.get(mood.lower().strip())


def _in_range(value: float | None, rng: tuple[float, float]) -> float:
    """Score how well a value fits within a range. Returns 0-1."""
    if value is None:
        return 0.5  # neutral when unknown
    low, high = rng
    if low <= value <= high:
        return 1.0
    # Distance-based falloff
    if value < low:
        dist = low - value
    else:
        dist = value - high
    return max(0.0, 1.0 - dist * 2.0)


def _score_mood_match(
    candidate: dict[str, Any],
    mood: MoodProfile,
    audio_features: dict[str, Any] | None = None,
) -> float:
    """Score how well a candidate matches a mood profile (0-1)."""
    scores = []

    # Genre matching
    genres = candidate.get("genre_names") or []
    if isinstance(genres, str):
        genres = [genres]
    genres_lower = [g.lower() for g in genres]

    genre_score = 0.0
    for pg in mood.preferred_genres:
        if any(pg.lower() in g for g in genres_lower):
            genre_score = 1.0
            break
    for ag in mood.avoided_genres:
        if any(ag.lower() in g for g in genres_lower):
            genre_score = max(0.0, genre_score - 0.5)
    scores.append(genre_score)

    # Audio feature matching (when available)
    if audio_features:
        audio_scores = [
            _in_range(audio_features.get("energy"), mood.energy_range),
            _in_range(audio_features.get("tempo"), mood.tempo_range),
            _in_range(audio_features.get("beat_strength"), mood.beat_strength_range),
            _in_range(audio_features.get("acousticness"), mood.acousticness_range),
            _in_range(audio_features.get("danceability"), mood.danceability_range),
            _in_range(audio_features.get("valence_proxy"), mood.valence_range),
        ]
        scores.extend(audio_scores)

    return sum(scores) / len(scores) if scores else 0.5


def filter_candidates_by_mood(
    candidates: list[dict[str, Any]],
    mood: str,
    audio_features_map: dict[str, dict[str, Any]] | None = None,
    min_keep_ratio: float = 0.3,
) -> list[dict[str, Any]]:
    """Filter and boost candidates by mood match.

    Scores each candidate, sorts by mood match, keeps at least min_keep_ratio.
    Adds _mood_boost field to each candidate.
    """
    profile = get_mood_profile(mood)
    if not profile:
        # Unknown mood — return as-is with neutral boost
        for c in candidates:
            c["_mood_boost"] = 0.0
        return candidates

    audio_map = audio_features_map or {}

    scored = []
    for c in candidates:
        cid = c.get("catalog_id", "")
        audio = audio_map.get(cid)
        match_score = _score_mood_match(c, profile, audio)
        c["_mood_boost"] = round(match_score - 0.5, 3)  # -0.5 to +0.5
        scored.append((match_score, c))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Keep at least min_keep_ratio of candidates
    min_keep = max(1, int(len(scored) * min_keep_ratio))
    # Keep all above 0.3 match, or at least min_keep
    threshold = 0.3
    kept = [c for score, c in scored if score >= threshold]
    if len(kept) < min_keep:
        kept = [c for _, c in scored[:min_keep]]

    return kept
