"""Typed data models for the recommendation engine.

Replaces dict[str, Any] with structured types for candidates, scored results,
taste profiles, and scoring weights. All models use dataclasses for zero
overhead and easy conversion to/from dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    """A song candidate for scoring, from any service (Spotify / Apple Music)."""

    catalog_id: str = ""
    name: str = ""
    artist_name: str = ""
    album_name: str = ""
    genre_names: list[str] = field(default_factory=list)
    release_date: str = ""
    isrc: str | None = None
    editorial_notes: str | None = None
    audio_traits: list[str] = field(default_factory=list)
    content_rating: str | None = None
    artwork_url_template: str | None = None
    preview_url: str | None = None
    duration_ms: int | None = None

    # Internal scoring metadata (set by pipeline)
    _strategy_source: str = ""
    _strategy_count: int = 1
    _mood_boost: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Candidate:
        """Create from a raw dict, ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        # Handle genre_names being a string
        genres = filtered.get("genre_names")
        if isinstance(genres, str):
            filtered["genre_names"] = [genres]
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict, preserving underscore-prefixed metadata."""
        return {
            "catalog_id": self.catalog_id,
            "name": self.name,
            "artist_name": self.artist_name,
            "album_name": self.album_name,
            "genre_names": self.genre_names,
            "release_date": self.release_date,
            "isrc": self.isrc,
            "editorial_notes": self.editorial_notes,
            "audio_traits": self.audio_traits,
            "content_rating": self.content_rating,
            "artwork_url_template": self.artwork_url_template,
            "preview_url": self.preview_url,
            "duration_ms": self.duration_ms,
            "_strategy_source": self._strategy_source,
            "_strategy_count": self._strategy_count,
            "_mood_boost": self._mood_boost,
        }


@dataclass
class ScoreBreakdown:
    """Per-dimension scoring breakdown for a candidate."""

    genre_match: float = 0.0
    artist_match: float = 0.0
    audio_similarity: float = 0.0
    novelty: float = 0.0
    freshness: float = 0.0
    diversity_penalty: float = 0.0
    staleness: float = 0.0
    cross_strategy_bonus: float = 0.0
    mood_boost: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """Convert to dict with rounded values."""
        return {
            "genre_match": round(self.genre_match, 3),
            "artist_match": round(self.artist_match, 3),
            "audio_similarity": round(self.audio_similarity, 3),
            "novelty": round(self.novelty, 3),
            "freshness": round(self.freshness, 3),
            "diversity_penalty": round(self.diversity_penalty, 3),
            "staleness": round(self.staleness, 3),
            "cross_strategy_bonus": round(self.cross_strategy_bonus, 3),
            "mood_boost": round(self.mood_boost, 3),
        }

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> ScoreBreakdown:
        """Create from a dict."""
        return cls(
            genre_match=d.get("genre_match", 0.0),
            artist_match=d.get("artist_match", 0.0),
            audio_similarity=d.get("audio_similarity", 0.0),
            novelty=d.get("novelty", 0.0),
            freshness=d.get("freshness", 0.0),
            diversity_penalty=d.get("diversity_penalty", 0.0),
            staleness=d.get("staleness", 0.0),
            cross_strategy_bonus=d.get("cross_strategy_bonus", 0.0),
            mood_boost=d.get("mood_boost", 0.0),
        )


@dataclass
class ScoredCandidate:
    """A candidate augmented with scoring results."""

    candidate: Candidate
    score: float = 0.0
    breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    explanation: str = "moderate match"

    def to_dict(self) -> dict[str, Any]:
        """Convert to the dict format expected by the API layer."""
        d = self.candidate.to_dict()
        d["_score"] = round(self.score, 3)
        d["_breakdown"] = self.breakdown.to_dict()
        d["_explanation"] = self.explanation
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScoredCandidate:
        """Create from a scored candidate dict."""
        candidate = Candidate.from_dict(d)
        breakdown = ScoreBreakdown.from_dict(d.get("_breakdown", {}))
        return cls(
            candidate=candidate,
            score=d.get("_score", 0.0),
            breakdown=breakdown,
            explanation=d.get("_explanation", "moderate match"),
        )


@dataclass
class ArtistAffinity:
    """An artist with their affinity score."""

    name: str
    score: float
    song_count: int = 0


@dataclass
class UserProfile:
    """Complete taste profile for scoring candidates."""

    genre_vector: dict[str, float] = field(default_factory=dict)
    top_artists: list[ArtistAffinity] = field(default_factory=list)
    audio_trait_preferences: dict[str, float] = field(default_factory=dict)
    release_year_distribution: dict[str, float] = field(default_factory=dict)
    familiarity_score: float = 0.0
    total_songs_analyzed: int = 0
    listening_hours_estimated: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UserProfile:
        """Create from the dict format used by build_taste_profile."""
        artists = []
        for a in d.get("top_artists", []):
            if isinstance(a, dict):
                artists.append(ArtistAffinity(
                    name=a.get("name", ""),
                    score=a.get("score", 0.0),
                    song_count=a.get("song_count", 0),
                ))
        return cls(
            genre_vector=d.get("genre_vector", {}),
            top_artists=artists,
            audio_trait_preferences=d.get("audio_trait_preferences", {}),
            release_year_distribution=d.get("release_year_distribution", {}),
            familiarity_score=d.get("familiarity_score", 0.0),
            total_songs_analyzed=d.get("total_songs_analyzed", 0),
            listening_hours_estimated=d.get("listening_hours_estimated", 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for storage/serialization."""
        return {
            "genre_vector": self.genre_vector,
            "top_artists": [
                {"name": a.name, "score": a.score, "song_count": a.song_count}
                for a in self.top_artists
            ],
            "audio_trait_preferences": self.audio_trait_preferences,
            "release_year_distribution": self.release_year_distribution,
            "familiarity_score": self.familiarity_score,
            "total_songs_analyzed": self.total_songs_analyzed,
            "listening_hours_estimated": self.listening_hours_estimated,
        }


@dataclass
class ScoringWeights:
    """Adaptive scoring weights across all dimensions."""

    genre: float = 0.35
    audio: float = 0.20
    novelty: float = 0.12
    freshness: float = 0.10
    diversity: float = 0.08
    artist: float = 0.08
    staleness: float = 0.07

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> ScoringWeights:
        """Create from a weights dict."""
        return cls(
            genre=d.get("genre", 0.35),
            audio=d.get("audio", 0.20),
            novelty=d.get("novelty", 0.12),
            freshness=d.get("freshness", 0.10),
            diversity=d.get("diversity", 0.08),
            artist=d.get("artist", 0.08),
            staleness=d.get("staleness", 0.07),
        )

    def to_dict(self) -> dict[str, float]:
        """Convert to dict."""
        return {
            "genre": self.genre,
            "audio": self.audio,
            "novelty": self.novelty,
            "freshness": self.freshness,
            "diversity": self.diversity,
            "artist": self.artist,
            "staleness": self.staleness,
        }


@dataclass
class AudioFeatures:
    """Extracted audio features for a track (Tier 2)."""

    tempo: float | None = None
    energy: float | None = None
    brightness: float | None = None
    danceability: float | None = None
    acousticness: float | None = None
    valence_proxy: float | None = None
    beat_strength: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, float | None]) -> AudioFeatures:
        """Create from a features dict."""
        return cls(
            tempo=d.get("tempo"),
            energy=d.get("energy"),
            brightness=d.get("brightness"),
            danceability=d.get("danceability"),
            acousticness=d.get("acousticness"),
            valence_proxy=d.get("valence_proxy"),
            beat_strength=d.get("beat_strength"),
        )

    def to_dict(self) -> dict[str, float | None]:
        """Convert to dict."""
        return {
            "tempo": self.tempo,
            "energy": self.energy,
            "brightness": self.brightness,
            "danceability": self.danceability,
            "acousticness": self.acousticness,
            "valence_proxy": self.valence_proxy,
            "beat_strength": self.beat_strength,
        }
