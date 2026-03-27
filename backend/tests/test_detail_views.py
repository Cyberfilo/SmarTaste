"""Integration tests for detail view endpoints (RECO-07 and RECO-08)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_SPOTIFY_CLIENT_ID", "test-spotify-client-id")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.app import app  # noqa: E402
from musicmind.config import Settings  # noqa: E402
from musicmind.db.schema import (  # noqa: E402
    audio_features_cache,
    metadata,
    song_metadata_cache,
    taste_profile_snapshots,
    users,
)
from musicmind.security.encryption import EncryptionService  # noqa: E402

JWT_SECRET = "test-jwt-secret-key-for-testing-only"
TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _insert_test_user(engine: AsyncEngine, user_id: str) -> None:
    """Insert a minimal user row so FK constraints don't block inserts."""
    async with engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id=user_id,
                email=f"{user_id}@test.example.com",
                password_hash="hashed",
                display_name="Test User",
            )
        )


async def _insert_song_metadata(
    engine: AsyncEngine, user_id: str, catalog_id: str,
) -> None:
    """Insert a row into song_metadata_cache for breakdown testing."""
    async with engine.begin() as conn:
        await conn.execute(
            song_metadata_cache.insert().values(
                catalog_id=catalog_id,
                user_id=user_id,
                name="Luna Piena",
                artist_name="Gazzelle",
                album_name="Tuttevogliamobene",
                genre_names=json.dumps(["Italian Pop", "Italian Indie"]),
                duration_ms=220000,
                release_date="2024-03-01",
            )
        )


async def _insert_taste_profile(engine: AsyncEngine, user_id: str) -> None:
    """Insert a taste profile snapshot for breakdown testing."""
    async with engine.begin() as conn:
        await conn.execute(
            taste_profile_snapshots.insert().values(
                user_id=user_id,
                genre_vector=json.dumps({
                    "Italian Hip-Hop/Rap": 0.6,
                    "Italian Pop": 0.3,
                    "Pop": 0.1,
                }),
                top_artists=json.dumps([
                    {"name": "Ultimo", "score": 0.9, "song_count": 15},
                    {"name": "Rose Villain", "score": 0.6, "song_count": 8},
                ]),
                release_year_distribution=json.dumps({
                    "2024": 0.5, "2023": 0.3, "2022": 0.2,
                }),
                familiarity_score=0.4,
                total_songs_analyzed=50,
                listening_hours_estimated=100.0,
            )
        )


async def _insert_audio_features(
    engine: AsyncEngine, catalog_id: str, user_id: str,
) -> None:
    """Insert a row into audio_features_cache for audio features testing."""
    async with engine.begin() as conn:
        await conn.execute(
            audio_features_cache.insert().values(
                catalog_id=catalog_id,
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


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for detail view tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def test_settings() -> Settings:
    """Settings with test config."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=TEST_FERNET_KEY,
        jwt_secret_key=JWT_SECRET,
        debug=True,
        spotify_client_id="test-spotify-client-id",
    )


@pytest.fixture
def encryption() -> EncryptionService:
    """EncryptionService using the test Fernet key."""
    return EncryptionService(TEST_FERNET_KEY)


@pytest.fixture
async def client(
    test_engine: AsyncEngine, test_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient with test DB and settings overrides."""
    app.state.engine = test_engine
    app.state.settings = test_settings
    app.state.encryption = EncryptionService(TEST_FERNET_KEY)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def test_user_id() -> str:
    """Deterministic test user ID for detail view tests."""
    return "test-user-id-detail-01"


@pytest.fixture
def auth_cookies(test_user_id: str) -> dict[str, str]:
    """Valid JWT access_token cookie for test_user_id."""
    import jwt

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": test_user_id,
            "email": "test@example.com",
            "iat": now,
            "exp": now + timedelta(minutes=30),
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"access_token": token}


# ── RECO-07: Scoring breakdown ───────────────────────────────────────────────


async def test_breakdown_returns_7_dimensions(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/recommendations/{catalog_id}/breakdown returns 200 with 7 dimensions (RECO-07)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_song_metadata(test_engine, test_user_id, "sp_breakdown_001")
    await _insert_taste_profile(test_engine, test_user_id)

    resp = await client.get(
        "/api/recommendations/sp_breakdown_001/breakdown",
        cookies=auth_cookies,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "overall_score" in data
    assert isinstance(data["overall_score"], float)
    assert 0.0 <= data["overall_score"] <= 1.0

    assert "dimensions" in data
    assert len(data["dimensions"]) == 7

    # Each dimension has required fields
    for dim in data["dimensions"]:
        assert "name" in dim
        assert "label" in dim
        assert "score" in dim
        assert "weight" in dim
        assert isinstance(dim["score"], (int, float))
        assert isinstance(dim["weight"], (int, float))

    # Check expected dimension names
    dim_names = {d["name"] for d in data["dimensions"]}
    expected = {
        "genre_match", "audio_similarity", "novelty", "freshness",
        "diversity", "artist_affinity", "anti_staleness",
    }
    assert dim_names == expected

    assert "explanation" in data
    assert "catalog_id" in data
    assert data["catalog_id"] == "sp_breakdown_001"


async def test_breakdown_not_found(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/recommendations/{catalog_id}/breakdown returns 404 for nonexistent track (RECO-07)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await client.get(
        "/api/recommendations/nonexistent-track/breakdown",
        cookies=auth_cookies,
    )

    assert resp.status_code == 404


async def test_breakdown_unauthenticated(
    client: AsyncClient,
) -> None:
    """GET /api/recommendations/{catalog_id}/breakdown returns 401 without auth (RECO-07)."""
    resp = await client.get("/api/recommendations/sp_breakdown_001/breakdown")
    assert resp.status_code == 401


async def test_breakdown_no_profile(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/recommendations/{catalog_id}/breakdown returns 404 when no profile exists (RECO-07)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_song_metadata(test_engine, test_user_id, "sp_breakdown_002")
    # No taste profile inserted

    resp = await client.get(
        "/api/recommendations/sp_breakdown_002/breakdown",
        cookies=auth_cookies,
    )

    assert resp.status_code == 404


# ── RECO-08: Audio features ─────────────────────────────────────────────────


async def test_audio_features_returns_data(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/tracks/{catalog_id}/audio-features returns 200 with feature data (RECO-08)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_audio_features(test_engine, "track-af-001", test_user_id)

    resp = await client.get(
        "/api/tracks/track-af-001/audio-features",
        cookies=auth_cookies,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["catalog_id"] == "track-af-001"
    assert data["energy"] == 0.82
    assert data["danceability"] == 0.75
    assert data["valence"] == 0.55
    assert data["acousticness"] == 0.1
    assert data["tempo"] == 128.0
    assert data["beat_strength"] == 0.9
    assert data["brightness"] == 0.6
    assert data["instrumentalness"] is None


async def test_audio_features_not_found(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/tracks/{catalog_id}/audio-features returns 404 when not cached (RECO-08)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await client.get(
        "/api/tracks/nonexistent-track/audio-features",
        cookies=auth_cookies,
    )

    assert resp.status_code == 404
    assert "Audio features not available" in resp.json()["detail"]


async def test_audio_features_unauthenticated(
    client: AsyncClient,
) -> None:
    """GET /api/tracks/{catalog_id}/audio-features returns 401 without auth (RECO-08)."""
    resp = await client.get("/api/tracks/track-af-001/audio-features")
    assert resp.status_code == 401
