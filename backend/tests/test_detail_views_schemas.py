"""Unit tests for detail view schemas and service methods (Task 1 TDD)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_SPOTIFY_CLIENT_ID", "test-spotify-client-id")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.api.recommendations.schemas import (  # noqa: E402
    BreakdownDimension,
    BreakdownResponse,
)
from musicmind.api.tracks.schemas import AudioFeaturesResponse  # noqa: E402
from musicmind.api.tracks.service import TrackService  # noqa: E402
from musicmind.db.schema import (  # noqa: E402
    audio_features_cache,
    metadata as db_metadata,
    song_metadata_cache,
    taste_profile_snapshots,
    users,
)


# ── Canned data ──────────────────────────────────────────────────────────────

CANNED_PROFILE = {
    "genre_vector": {
        "Italian Hip-Hop/Rap": 0.6,
        "Italian Pop": 0.3,
        "Pop": 0.1,
    },
    "top_artists": [
        {"name": "Ultimo", "score": 0.9, "song_count": 15},
        {"name": "Rose Villain", "score": 0.6, "song_count": 8},
    ],
    "release_year_distribution": {"2024": 0.5, "2023": 0.3, "2022": 0.2},
    "familiarity_score": 0.4,
}


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(db_metadata.create_all)
    yield engine
    await engine.dispose()


# ── Schema tests ─────────────────────────────────────────────────────────────


def test_breakdown_dimension_fields() -> None:
    """BreakdownDimension has name, label, score, weight fields."""
    dim = BreakdownDimension(
        name="genre_match", label="Genre Match", score=0.85, weight=0.35,
    )
    assert dim.name == "genre_match"
    assert dim.label == "Genre Match"
    assert dim.score == 0.85
    assert dim.weight == 0.35


def test_breakdown_response_fields() -> None:
    """BreakdownResponse has catalog_id, overall_score, dimensions list, explanation."""
    dims = [
        BreakdownDimension(name="genre_match", label="Genre Match", score=0.8, weight=0.35),
    ]
    resp = BreakdownResponse(
        catalog_id="test-001",
        overall_score=0.75,
        dimensions=dims,
        explanation="strong genre match",
    )
    assert resp.catalog_id == "test-001"
    assert resp.overall_score == 0.75
    assert len(resp.dimensions) == 1
    assert resp.explanation == "strong genre match"


def test_audio_features_response_fields() -> None:
    """AudioFeaturesResponse has all expected audio feature fields."""
    resp = AudioFeaturesResponse(
        catalog_id="track-001",
        energy=0.8,
        danceability=0.6,
        valence=0.5,
        acousticness=0.3,
        tempo=120.0,
        instrumentalness=None,
        beat_strength=0.7,
        brightness=0.65,
    )
    assert resp.catalog_id == "track-001"
    assert resp.energy == 0.8
    assert resp.danceability == 0.6
    assert resp.valence == 0.5
    assert resp.acousticness == 0.3
    assert resp.tempo == 120.0
    assert resp.instrumentalness is None
    assert resp.beat_strength == 0.7
    assert resp.brightness == 0.65


# ── TrackService tests ───────────────────────────────────────────────────────


async def test_track_service_get_audio_features_found(
    test_engine: AsyncEngine,
) -> None:
    """get_audio_features returns dict when track exists in audio_features_cache."""
    user_id = "test-user-audio-01"

    # Insert user
    async with test_engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id=user_id,
                email="audio@test.com",
                password_hash="hashed",
                display_name="Test",
            )
        )
        # Insert audio features
        await conn.execute(
            audio_features_cache.insert().values(
                catalog_id="track-af-001",
                user_id=user_id,
                tempo=128.0,
                energy=0.82,
                brightness=0.6,
                danceability=0.75,
                acousticness=0.1,
                valence_proxy=0.55,
                beat_strength=0.9,
            )
        )

    svc = TrackService()
    result = await svc.get_audio_features(test_engine, user_id=user_id, catalog_id="track-af-001")

    assert result is not None
    assert result["energy"] == 0.82
    assert result["danceability"] == 0.75
    assert result["valence"] == 0.55  # valence_proxy -> valence
    assert result["tempo"] == 128.0
    assert result["instrumentalness"] is None  # not in DB
    assert result["beat_strength"] == 0.9
    assert result["brightness"] == 0.6


async def test_track_service_get_audio_features_not_found(
    test_engine: AsyncEngine,
) -> None:
    """get_audio_features returns None when track not in audio_features_cache."""
    user_id = "test-user-audio-02"

    async with test_engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id=user_id,
                email="audio2@test.com",
                password_hash="hashed",
                display_name="Test",
            )
        )

    svc = TrackService()
    result = await svc.get_audio_features(test_engine, user_id=user_id, catalog_id="nonexistent")
    assert result is None
