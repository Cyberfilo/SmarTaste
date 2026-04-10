"""Typed models for the audio analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedFeatures:
    """Full audio feature extraction result from a preview clip.

    Scalar features are always present (or None if extraction failed).
    The embedding field holds the Discogs-EffNet vector when available
    (1,280-dim via ONNX, or 128-dim via legacy TensorFlow).
    """

    # Scalar features
    tempo: float | None = None
    energy: float | None = None
    danceability: float | None = None
    acousticness: float | None = None
    valence: float | None = None
    arousal: float | None = None
    loudness_lufs: float | None = None
    brightness: float | None = None
    beat_strength: float | None = None
    instrumentalness: float | None = None
    loudness: float | None = None
    key: str | None = None
    scale: str | None = None

    # Discogs-EffNet embedding (1,280-dim via ONNX, 128-dim via TF, or None)
    embedding: list[float] | None = None

    # Classifier head outputs (populated from embedding when models available)
    mood_aggressive: float | None = None
    mood_happy: float | None = None
    mood_party: float | None = None
    mood_relaxed: float | None = None
    mood_sad: float | None = None
    voice_instrumental: float | None = None  # probability track is instrumental
    mood_acoustic: float | None = None

    # Genre tags (Discogs400 multi-label, tag -> probability)
    genre_tags: dict[str, float] | None = None

    def to_scalar_dict(self) -> dict[str, float | None]:
        """Convert scalar features to audio_features_cache format."""
        return {
            "tempo": self.tempo,
            "energy": self.energy,
            "brightness": self.brightness,
            "danceability": self.danceability,
            "acousticness": self.acousticness,
            "valence_proxy": self.valence,
            "beat_strength": self.beat_strength,
            "instrumentalness": self.instrumentalness,
            "loudness": self.loudness,
            "key": self.key,
            "scale": self.scale,
            # Classifier head outputs
            "mood_aggressive": self.mood_aggressive,
            "mood_happy": self.mood_happy,
            "mood_party": self.mood_party,
            "mood_relaxed": self.mood_relaxed,
            "mood_sad": self.mood_sad,
            "voice_instrumental": self.voice_instrumental,
            "mood_acoustic": self.mood_acoustic,
        }

    def to_full_dict(self) -> dict[str, Any]:
        """Full serialization including embedding and genre tags."""
        d: dict[str, Any] = self.to_scalar_dict()
        d["arousal"] = self.arousal
        d["loudness_lufs"] = self.loudness_lufs
        d["embedding"] = self.embedding
        d["genre_tags"] = self.genre_tags
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExtractedFeatures:
        """Create from a stored dict."""
        return cls(
            tempo=d.get("tempo"),
            energy=d.get("energy"),
            danceability=d.get("danceability"),
            acousticness=d.get("acousticness"),
            valence=d.get("valence_proxy") or d.get("valence"),
            arousal=d.get("arousal"),
            loudness_lufs=d.get("loudness_lufs"),
            brightness=d.get("brightness"),
            beat_strength=d.get("beat_strength"),
            key=d.get("key"),
            scale=d.get("scale"),
            embedding=d.get("embedding"),
        )


@dataclass
class AudioEmbedding:
    """Audio embedding with metadata (1,280-dim ONNX or 128-dim TF legacy)."""

    catalog_id: str
    isrc: str | None = None
    vector: list[float] = field(default_factory=list)
    model_version: str = "discogs-effnet-1280"

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "catalog_id": self.catalog_id,
            "isrc": self.isrc,
            "vector": self.vector,
            "model_version": self.model_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AudioEmbedding:
        """Create from stored dict."""
        return cls(
            catalog_id=d.get("catalog_id", ""),
            isrc=d.get("isrc"),
            vector=d.get("vector", []),
            model_version=d.get("model_version", "discogs-effnet-bs64"),
        )
