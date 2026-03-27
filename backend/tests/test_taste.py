"""Integration tests for taste profile endpoints (TAST-01 through TAST-04)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_SPOTIFY_CLIENT_ID", "test-spotify-client-id")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

import sqlalchemy as sa  # noqa: E402

from musicmind.app import app  # noqa: E402
from musicmind.config import Settings  # noqa: E402
from musicmind.db.schema import (  # noqa: E402
    metadata,
    service_connections,
    taste_profile_snapshots,
    users,
)
from musicmind.security.encryption import EncryptionService  # noqa: E402

JWT_SECRET = "test-jwt-secret-key-for-testing-only"
TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="

# ── Sample Data ─────────────────────────────────────────────────────────────

SAMPLE_SONGS = [
    {
        "catalog_id": "track_001",
        "library_id": None,
        "name": "Notti in Bianco",
        "artist_name": "Ultimo",
        "album_name": "Alba",
        "genre_names": [],  # Spotify tracks never have genres; populated via enrich
        "duration_ms": 240000,
        "release_date": "2023-04-15",
        "isrc": "ITXYZ0001",
        "editorial_notes": "",
        "audio_traits": ["lossless", "spatial"],
        "has_lyrics": True,
        "content_rating": None,
        "artwork_bg_color": "",
        "artwork_url_template": "",
        "preview_url": "",
        "user_rating": None,
        "date_added_to_library": None,
        "service_source": "spotify",
    },
    {
        "catalog_id": "track_002",
        "library_id": None,
        "name": "Come Un Tuono",
        "artist_name": "Rose Villain",
        "album_name": "Radio Gotham",
        "genre_names": [],
        "duration_ms": 200000,
        "release_date": "2024-01-20",
        "isrc": "ITXYZ0002",
        "editorial_notes": "",
        "audio_traits": [],
        "has_lyrics": True,
        "content_rating": None,
        "artwork_bg_color": "",
        "artwork_url_template": "",
        "preview_url": "",
        "user_rating": None,
        "date_added_to_library": None,
        "service_source": "spotify",
    },
    {
        "catalog_id": "track_003",
        "library_id": None,
        "name": "Tango",
        "artist_name": "Tananai",
        "album_name": "CALMOCOBRA",
        "genre_names": [],
        "duration_ms": 180000,
        "release_date": "2024-06-10",
        "isrc": "ITXYZ0003",
        "editorial_notes": "",
        "audio_traits": [],
        "has_lyrics": True,
        "content_rating": None,
        "artwork_bg_color": "",
        "artwork_url_template": "",
        "preview_url": "",
        "user_rating": None,
        "date_added_to_library": None,
        "service_source": "spotify",
    },
]

# Spotify artist genres use their own naming convention. Regional prefixes
# like "Italian Hip-Hop/Rap" are Apple Music format. Spotify uses lowercase
# e.g. "italian hip-hop", "italian pop". expand_genres splits these:
# "italian pop" -> ["italian pop", "pop"], "italian hip-hop" -> ["italian hip-hop", "hip-hop"]
SAMPLE_ARTISTS = [
    {
        "id": "artist_001",
        "name": "Ultimo",
        "genres": ["Italian Hip-Hop/Rap", "Italian Pop"],
    },
    {"id": "artist_002", "name": "Rose Villain", "genres": ["Italian Pop", "Pop"]},
    {"id": "artist_003", "name": "Tananai", "genres": ["Italian Pop"]},
]

SAMPLE_APPLE_SONGS = [
    {
        "catalog_id": "apple_track_001",
        "library_id": "l.abc001",
        "name": "Notti in Bianco",
        "artist_name": "Ultimo",
        "album_name": "Alba",
        "genre_names": ["Italian Hip-Hop/Rap", "Italian Pop"],
        "duration_ms": 240000,
        "release_date": "2023-04-15",
        "isrc": "ITXYZ0001",
        "editorial_notes": "",
        "audio_traits": ["lossless", "spatial"],
        "has_lyrics": True,
        "content_rating": None,
        "artwork_bg_color": "",
        "artwork_url_template": "",
        "preview_url": "",
        "user_rating": None,
        "date_added_to_library": None,
        "service_source": "apple_music",
    },
]


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


async def _insert_service_connection(
    engine: AsyncEngine,
    encryption: EncryptionService,
    user_id: str,
    service: str,
    *,
    expires_in: int | None = 3600,
) -> None:
    """Insert an encrypted service connection for testing."""
    token_expires_at = (
        datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None
    )
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            service_connections.insert().values(
                user_id=user_id,
                service=service,
                access_token_encrypted=encryption.encrypt("test_access_token"),
                refresh_token_encrypted=encryption.encrypt("test_refresh_token"),
                token_expires_at=token_expires_at,
                service_user_id=f"{service}_user_123",
                connected_at=now,
            )
        )


async def _insert_snapshot(
    engine: AsyncEngine,
    user_id: str,
    service: str,
    computed_at: datetime,
    profile_data: dict,
) -> None:
    """Insert a taste profile snapshot for testing."""
    # Strip tzinfo for SQLite compat
    computed_at_naive = computed_at.replace(tzinfo=None)
    async with engine.begin() as conn:
        await conn.execute(
            taste_profile_snapshots.insert().values(
                user_id=user_id,
                service_source=service,
                computed_at=computed_at_naive,
                genre_vector=json.dumps(
                    profile_data.get("genre_vector", {})
                ),
                top_artists=json.dumps(
                    profile_data.get("top_artists", [])
                ),
                audio_trait_preferences=json.dumps(
                    profile_data.get("audio_trait_preferences", {})
                ),
                release_year_distribution=json.dumps(
                    profile_data.get("release_year_distribution", {})
                ),
                familiarity_score=profile_data.get("familiarity_score", 0.0),
                total_songs_analyzed=profile_data.get("total_songs_analyzed", 0),
                listening_hours_estimated=profile_data.get(
                    "listening_hours_estimated", 0.0
                ),
            )
        )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for taste tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def test_settings() -> Settings:
    """Settings with Spotify configured."""
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
    test_engine: AsyncEngine, test_settings: Settings
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
    """Deterministic test user ID."""
    return "test-user-id-taste-01"


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


# ── Mock context manager ────────────────────────────────────────────────────


def _spotify_fetch_mocks():
    """Return a context manager that mocks all Spotify fetch functions."""
    return (
        patch(
            "musicmind.api.taste.service.fetch_spotify_top_tracks",
            new_callable=AsyncMock,
            return_value=SAMPLE_SONGS,
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_top_artists",
            new_callable=AsyncMock,
            return_value=SAMPLE_ARTISTS,
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_saved_tracks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_recently_played",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.refresh_spotify_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    )


def _apple_fetch_mocks():
    """Return a context manager that mocks Apple Music fetch functions."""
    return (
        patch(
            "musicmind.api.taste.service.fetch_apple_music_library",
            new_callable=AsyncMock,
            return_value=SAMPLE_APPLE_SONGS,
        ),
        patch(
            "musicmind.api.taste.service.fetch_apple_music_recently_played",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.generate_apple_developer_token",
            return_value="test-dev-token",
        ),
    )


# ── TAST-01: Genres with regional specificity ───────────────────────────────


async def test_genres_endpoint(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/taste/genres returns genres with regional specificity (TAST-01)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    p1, p2, p3, p4, p5 = _spotify_fetch_mocks()
    with p1, p2, p3, p4, p5:
        resp = await client.get(
            "/api/taste/genres",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "genres" in data
    genre_names = [g["genre"] for g in data["genres"]]
    # Regional specificity: "Italian Hip-Hop/Rap" must appear (not just "Hip-Hop/Rap")
    assert "Italian Hip-Hop/Rap" in genre_names, (
        f"Expected 'Italian Hip-Hop/Rap' in genres but got: {genre_names}"
    )
    # Genres should be sorted by weight descending
    weights = [g["weight"] for g in data["genres"]]
    assert weights == sorted(weights, reverse=True)


async def test_genre_regional_specificity() -> None:
    """build_genre_vector gives higher weight to regional genres than parents (TAST-01)."""
    from musicmind.engine.profile import build_genre_vector

    songs = [
        {"genre_names": ["Italian Hip-Hop/Rap"], "date_added_to_library": None},
        {"genre_names": ["Italian Hip-Hop/Rap"], "date_added_to_library": None},
        {"genre_names": ["Italian Hip-Hop/Rap"], "date_added_to_library": None},
    ]
    vector = build_genre_vector(songs, [])
    assert "Italian Hip-Hop/Rap" in vector
    assert "Hip-Hop/Rap" in vector
    # Regional genre must have higher weight than expanded parent (0.3x)
    assert vector["Italian Hip-Hop/Rap"] > vector["Hip-Hop/Rap"]


# ── TAST-02: Artists ranked by affinity ──────────────────────────────────────


async def test_artists_endpoint(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/taste/artists returns artists sorted by score descending (TAST-02)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    p1, p2, p3, p4, p5 = _spotify_fetch_mocks()
    with p1, p2, p3, p4, p5:
        resp = await client.get(
            "/api/taste/artists",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "artists" in data
    artists = data["artists"]
    assert len(artists) > 0
    for artist in artists:
        assert "name" in artist
        assert "score" in artist
        assert "song_count" in artist
    # Sorted by score descending
    scores = [a["score"] for a in artists]
    assert scores == sorted(scores, reverse=True)


# ── TAST-03: Audio traits ────────────────────────────────────────────────────


async def test_audio_traits_endpoint(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
    test_settings: Settings,
) -> None:
    """GET /api/taste/audio-traits with Apple Music data returns traits (TAST-03)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "apple_music", expires_in=None
    )

    # Override settings to include Apple Music config
    apple_settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=TEST_FERNET_KEY,
        jwt_secret_key=JWT_SECRET,
        debug=True,
        apple_team_id="TEST_TEAM",
        apple_key_id="TEST_KEY",
        apple_private_key_path="/tmp/test.p8",
    )
    app.state.settings = apple_settings

    p1, p2, p3 = _apple_fetch_mocks()
    with p1, p2, p3:
        resp = await client.get(
            "/api/taste/audio-traits?service=apple_music",
            cookies=auth_cookies,
        )

    # Restore settings
    app.state.settings = test_settings

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "traits" in data
    # Apple Music songs have audio_traits: ["lossless", "spatial"]
    assert len(data["traits"]) > 0
    assert data["note"] is None


async def test_audio_traits_spotify_note(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/taste/audio-traits?service=spotify returns note about unavailability (TAST-03)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    # Use songs without audio_traits
    songs_no_traits = [
        {**s, "audio_traits": []} for s in SAMPLE_SONGS
    ]

    with (
        patch(
            "musicmind.api.taste.service.fetch_spotify_top_tracks",
            new_callable=AsyncMock,
            return_value=songs_no_traits,
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_top_artists",
            new_callable=AsyncMock,
            return_value=SAMPLE_ARTISTS,
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_saved_tracks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_recently_played",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.refresh_spotify_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        resp = await client.get(
            "/api/taste/audio-traits?service=spotify",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["note"] is not None
    assert "not available" in data["note"].lower()


# ── TAST-04: Profile service isolation, staleness, refresh, error ────────────


async def test_profile_service_isolation(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/taste/profile?service=spotify returns Spotify-only data (TAST-04)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    p1, p2, p3, p4, p5 = _spotify_fetch_mocks()
    with p1, p2, p3, p4, p5:
        resp = await client.get(
            "/api/taste/profile?service=spotify",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["service"] == "spotify"


async def test_snapshot_staleness_check(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """Fresh snapshot (<24h) is returned without calling fetch functions (TAST-04)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    # Insert a fresh snapshot (1 hour ago)
    fresh_time = datetime.now(UTC) - timedelta(hours=1)
    await _insert_snapshot(
        test_engine,
        test_user_id,
        "spotify",
        fresh_time,
        {
            "genre_vector": {"Italian Pop": 0.8, "Pop": 0.2},
            "top_artists": [{"name": "Ultimo", "score": 1.0, "song_count": 5}],
            "audio_trait_preferences": {},
            "release_year_distribution": {"2023": 0.5, "2024": 0.5},
            "familiarity_score": 0.6,
            "total_songs_analyzed": 10,
            "listening_hours_estimated": 5.0,
        },
    )

    mock_fetch = AsyncMock(return_value=SAMPLE_SONGS)
    with patch(
        "musicmind.api.taste.service.fetch_spotify_top_tracks", mock_fetch
    ):
        resp = await client.get(
            "/api/taste/profile?service=spotify",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    # Fetch should NOT have been called -- cached snapshot used
    mock_fetch.assert_not_called()
    data = resp.json()
    assert data["total_songs_analyzed"] == 10


async def test_force_refresh(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """?refresh=true bypasses cache and re-fetches data (TAST-04)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    # Insert a fresh snapshot
    fresh_time = datetime.now(UTC) - timedelta(hours=1)
    await _insert_snapshot(
        test_engine,
        test_user_id,
        "spotify",
        fresh_time,
        {
            "genre_vector": {"Italian Pop": 1.0},
            "top_artists": [],
            "audio_trait_preferences": {},
            "release_year_distribution": {},
            "familiarity_score": 0.0,
            "total_songs_analyzed": 5,
            "listening_hours_estimated": 2.0,
        },
    )

    p1, p2, p3, p4, p5 = _spotify_fetch_mocks()
    with p1 as mock_top, p2, p3, p4, p5:
        resp = await client.get(
            "/api/taste/profile?service=spotify&refresh=true",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    # Despite fresh snapshot, fetch SHOULD have been called
    mock_top.assert_called_once()


async def test_no_service_returns_400(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """User with no connected service gets 400 (TAST-04)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await client.get(
        "/api/taste/profile",
        cookies=auth_cookies,
    )
    assert resp.status_code == 400
    assert "No connected service" in resp.json()["detail"]


async def test_empty_profile_response(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """Empty data returns 200 with total_songs_analyzed=0 (TAST-04)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    with (
        patch(
            "musicmind.api.taste.service.fetch_spotify_top_tracks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_top_artists",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_saved_tracks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_recently_played",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.refresh_spotify_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        resp = await client.get(
            "/api/taste/profile",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_songs_analyzed"] == 0


async def test_unauthenticated_returns_401(
    client: AsyncClient,
) -> None:
    """GET /api/taste/profile without auth cookie returns 401."""
    resp = await client.get("/api/taste/profile")
    assert resp.status_code == 401
