"""Typed models for the audio analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedFeatures:
    """Full audio feature extraction result from a preview clip.

    Scalar features are always present (or None if extraction failed).
    The embedding field holds the 128-dim Discogs-EffNet vector when available.
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
    key: str | None = None
    scale: str | None = None

    # 128-dim embedding from Discogs-EffNet (or None)
    embedding: list[float] | None = None

    def to_scalar_dict(self) -> dict[str, float | None]:
        """Convert scalar features to legacy audio_features_cache format."""
        return {
            "tempo": self.tempo,
            "energy": self.energy,
            "brightness": self.brightness,
            "danceability": self.danceability,
            "acousticness": self.acousticness,
            "valence_proxy": self.valence,
            "beat_strength": self.beat_strength,
        }

    def to_full_dict(self) -> dict[str, Any]:
        """Full serialization including embedding."""
        d: dict[str, Any] = self.to_scalar_dict()
        d["arousal"] = self.arousal
        d["loudness_lufs"] = self.loudness_lufs
        d["key"] = self.key
        d["scale"] = self.scale
        d["embedding"] = self.embedding
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
    """128-dim audio embedding with metadata."""

    catalog_id: str
    isrc: str | None = None
    vector: list[float] = field(default_factory=list)
    model_version: str = "discogs-effnet-bs64"

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
