"""Tests for Phase 5: Essentia audio pipeline."""

from __future__ import annotations

import pytest

from musicmind.engine.audio.models import AudioEmbedding, ExtractedFeatures
from musicmind.engine.similarity import (
    combined_audio_similarity,
    embedding_cosine_similarity,
)


# ── ExtractedFeatures model tests ──────────────────────────────────────────


def test_extracted_features_roundtrip() -> None:
    """ExtractedFeatures to_full_dict/from_dict roundtrip."""
    f = ExtractedFeatures(
        tempo=120.5,
        energy=0.7,
        danceability=0.8,
        acousticness=0.2,
        valence=0.6,
        arousal=0.65,
        loudness_lufs=-14.0,
        brightness=0.5,
        beat_strength=0.75,
        key="C",
        scale="major",
        embedding=[0.1] * 128,
    )
    d = f.to_full_dict()
    assert d["tempo"] == 120.5
    assert d["key"] == "C"
    assert len(d["embedding"]) == 128

    f2 = ExtractedFeatures.from_dict(d)
    assert f2.tempo == f.tempo
    assert f2.key == f.key
    assert f2.embedding == f.embedding


def test_extracted_features_scalar_dict() -> None:
    """to_scalar_dict returns legacy-compatible format."""
    f = ExtractedFeatures(tempo=130.0, energy=0.8, valence=0.6)
    scalar = f.to_scalar_dict()
    assert "tempo" in scalar
    assert "valence_proxy" in scalar
    assert scalar["valence_proxy"] == 0.6


def test_extracted_features_defaults() -> None:
    """Default ExtractedFeatures has all None values."""
    f = ExtractedFeatures()
    assert f.tempo is None
    assert f.embedding is None
    scalar = f.to_scalar_dict()
    assert all(v is None for v in scalar.values())


# ── AudioEmbedding model tests ────────────────────────────────────────────


def test_audio_embedding_roundtrip() -> None:
    """AudioEmbedding to_dict/from_dict roundtrip."""
    e = AudioEmbedding(
        catalog_id="abc123",
        isrc="US1234567890",
        vector=[0.5] * 128,
        model_version="discogs-effnet-bs64",
    )
    d = e.to_dict()
    e2 = AudioEmbedding.from_dict(d)
    assert e2.catalog_id == e.catalog_id
    assert e2.isrc == e.isrc
    assert len(e2.vector) == 128


# ── Embedding similarity tests ────────────────────────────────────────────


def test_embedding_cosine_identical() -> None:
    """Identical embeddings have similarity ~1.0."""
    vec = [0.1 * i for i in range(128)]
    sim = embedding_cosine_similarity(vec, vec)
    assert sim == pytest.approx(1.0, abs=0.001)


def test_embedding_cosine_orthogonal() -> None:
    """Orthogonal embeddings have low similarity."""
    a = [1.0 if i < 64 else 0.0 for i in range(128)]
    b = [0.0 if i < 64 else 1.0 for i in range(128)]
    sim = embedding_cosine_similarity(a, b)
    assert sim < 0.1


def test_embedding_cosine_none() -> None:
    """Returns 0.5 when either embedding is None."""
    assert embedding_cosine_similarity(None, [0.1] * 128) == 0.5
    assert embedding_cosine_similarity([0.1] * 128, None) == 0.5
    assert embedding_cosine_similarity(None, None) == 0.5


def test_embedding_cosine_mismatched_length() -> None:
    """Returns 0.5 for mismatched lengths."""
    assert embedding_cosine_similarity([0.1] * 64, [0.1] * 128) == 0.5


# ── Combined audio similarity tests ──────────────────────────────────────


def test_combined_with_embeddings() -> None:
    """Combined similarity uses 70% embedding + 30% scalar."""
    vec = [0.1 * i for i in range(128)]
    scalar = {"tempo": 120.0, "energy": 0.7}
    sim = combined_audio_similarity(scalar, scalar, vec, vec)
    # Embedding is identical (1.0), scalar is identical (~1.0)
    assert sim > 0.9


def test_combined_without_embeddings() -> None:
    """Falls back to 100% scalar when embeddings absent."""
    scalar = {"tempo": 120.0, "energy": 0.7}
    sim = combined_audio_similarity(scalar, scalar, None, None)
    # Just scalar similarity of identical features
    assert sim > 0.9


def test_combined_none_scalars_with_embeddings() -> None:
    """When scalars are None but embeddings exist, still works."""
    vec = [0.1 * i for i in range(128)]
    sim = combined_audio_similarity(None, None, vec, vec)
    # 70% embedding (1.0) + 30% scalar (0.5 neutral)
    assert sim == pytest.approx(0.85, abs=0.05)
