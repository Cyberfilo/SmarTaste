"""Integration tests for listening stats endpoints (STAT-01 through STAT-04)."""

from __future__ import annotations

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

from musicmind.app import app  # noqa: E402
from musicmind.config import Settings  # noqa: E402
from musicmind.db.schema import metadata, service_connections, users  # noqa: E402
from musicmind.security.encryption import EncryptionService  # noqa: E402

JWT_SECRET = "test-jwt-secret-key-for-testing-only"
TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="

# ── Sample Data ─────────────────────────────────────────────────────────────

SAMPLE_SPOTIFY_TOP_TRACKS = [
    {
        "rank": 1,
        "name": "Notti in Bianco",
        "artist_name": "Ultimo",
        "album_name": "Alba",
        "duration_ms": 240000,
        "catalog_id": "sp_track_001",
        "preview_url": "https://example.com/preview1.mp3",
    },
    {
        "rank": 2,
        "name": "Come Un Tuono",
        "artist_name": "Rose Villain",
        "album_name": "Radio Gotham",
        "duration_ms": 200000,
        "catalog_id": "sp_track_002",
        "preview_url": "https://example.com/preview2.mp3",
    },
    {
        "rank": 3,
        "name": "Tango",
        "artist_name": "Tananai",
        "album_name": "CALMOCOBRA",
        "duration_ms": 180000,
        "catalog_id": "sp_track_003",
        "preview_url": "",
    },
]

SAMPLE_SPOTIFY_TOP_ARTISTS = [
    {
        "rank": 1,
        "name": "Ultimo",
        "id": "artist_001",
        "genres": ["Italian Hip-Hop/Rap", "Italian Pop"],
    },
    {
        "rank": 2,
        "name": "Rose Villain",
        "id": "artist_002",
        "genres": ["Italian Pop", "Pop"],
    },
    {
        "rank": 3,
        "name": "Tananai",
        "id": "artist_003",
        "genres": ["Italian Pop"],
    },
]

SAMPLE_APPLE_MUSIC_LIBRARY = [
    {
        "catalog_id": "apple_track_001",
        "library_id": "l.abc001",
        "name": "Notti in Bianco",
        "artist_name": "Ultimo",
        "album_name": "Alba",
        "genre_names": ["Italian Hip-Hop/Rap", "Italian Pop"],
        "duration_ms": 240000,
        "release_date": "2023-04-15",
        "date_added_to_library": (datetime.now(UTC) - timedelta(days=5)).isoformat(),
        "service_source": "apple_music",
    },
    {
        "catalog_id": "apple_track_002",
        "library_id": "l.abc002",
        "name": "Come Un Tuono",
        "artist_name": "Rose Villain",
        "album_name": "Radio Gotham",
        "genre_names": ["Italian Pop", "Pop"],
        "duration_ms": 200000,
        "release_date": "2024-01-20",
        "date_added_to_library": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
        "service_source": "apple_music",
    },
    {
        "catalog_id": "apple_track_003",
        "library_id": "l.abc003",
        "name": "Tango",
        "artist_name": "Tananai",
        "album_name": "CALMOCOBRA",
        "genre_names": ["Italian Pop"],
        "duration_ms": 180000,
        "release_date": "2024-06-10",
        "date_added_to_library": (datetime.now(UTC) - timedelta(days=15)).isoformat(),
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


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for stats tests."""
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
    return "test-user-id-stats-01"


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


# ── Mock Helpers ─────────────────────────────────────────────────────────────


def _spotify_stats_mocks(period: str = "month"):
    """Return patch context managers for Spotify stats fetch functions.

    Patches at the service module level where the imported names are used.
    """
    return (
        patch(
            "musicmind.api.stats.service.fetch_spotify_top_tracks_for_period",
            new_callable=AsyncMock,
            return_value=SAMPLE_SPOTIFY_TOP_TRACKS,
        ),
        patch(
            "musicmind.api.stats.service.fetch_spotify_top_artists_for_period",
            new_callable=AsyncMock,
            return_value=SAMPLE_SPOTIFY_TOP_ARTISTS,
        ),
        patch(
            "musicmind.api.stats.service.refresh_spotify_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    )


def _apple_stats_mocks():
    """Return patch context managers for Apple Music stats functions."""
    return (
        patch(
            "musicmind.api.stats.service.fetch_apple_music_library",
            new_callable=AsyncMock,
            return_value=SAMPLE_APPLE_MUSIC_LIBRARY,
        ),
        patch(
            "musicmind.api.stats.service.generate_apple_developer_token",
            return_value="test-dev-token",
        ),
    )


# ── STAT-01: Top tracks by period ───────────────────────────────────────────


async def test_top_tracks_spotify_month(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/stats/tracks?period=month returns top tracks with correct fields (STAT-01)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    p1, p2, p3 = _spotify_stats_mocks(period="month")
    with p1, p2, p3:
        resp = await client.get(
            "/api/stats/tracks?period=month",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["service"] == "spotify"
    assert data["period"] == "month"
    assert len(data["items"]) == 3
    assert data["total"] == 3

    # Verify each item has the required fields
    for item in data["items"]:
        assert "rank" in item
        assert "name" in item
        assert "artist_name" in item
        assert "album_name" in item

    # First item should be "Notti in Bianco"
    assert data["items"][0]["name"] == "Notti in Bianco"
    assert data["items"][0]["artist_name"] == "Ultimo"


async def test_top_tracks_spotify_alltime(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/stats/tracks?period=alltime returns 200 (STAT-01)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    p1, p2, p3 = _spotify_stats_mocks(period="alltime")
    with p1, p2, p3:
        resp = await client.get(
            "/api/stats/tracks?period=alltime",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["period"] == "alltime"
    assert len(data["items"]) > 0


async def test_top_tracks_apple_music(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
    test_settings: Settings,
) -> None:
    """GET /api/stats/tracks?period=month&service=apple_music returns 200 (STAT-01)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "apple_music", expires_in=None
    )

    # Override settings for Apple Music config
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

    p1, p2 = _apple_stats_mocks()
    with p1, p2:
        resp = await client.get(
            "/api/stats/tracks?period=month&service=apple_music",
            cookies=auth_cookies,
        )

    # Restore settings
    app.state.settings = test_settings

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["service"] == "apple_music"
    assert data["period"] == "month"
    assert len(data["items"]) > 0
    for item in data["items"]:
        assert "rank" in item
        assert "name" in item
        assert "artist_name" in item
        assert "album_name" in item


# ── STAT-02: Top artists by period ──────────────────────────────────────────


async def test_top_artists_spotify(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/stats/artists?period=6months returns artists with genres (STAT-02)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    p1, p2, p3 = _spotify_stats_mocks(period="6months")
    with p1, p2, p3:
        resp = await client.get(
            "/api/stats/artists?period=6months",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["service"] == "spotify"
    assert data["period"] == "6months"
    assert len(data["items"]) == 3
    assert data["total"] == 3

    for item in data["items"]:
        assert "rank" in item
        assert "name" in item
        assert "genres" in item

    # First artist should be Ultimo with Italian genres
    assert data["items"][0]["name"] == "Ultimo"
    assert "Italian Hip-Hop/Rap" in data["items"][0]["genres"]


async def test_top_artists_apple_music(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
    test_settings: Settings,
) -> None:
    """GET /api/stats/artists?period=month&service=apple_music returns 200 (STAT-02)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "apple_music", expires_in=None
    )

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

    p1, p2 = _apple_stats_mocks()
    with p1, p2:
        resp = await client.get(
            "/api/stats/artists?period=month&service=apple_music",
            cookies=auth_cookies,
        )

    app.state.settings = test_settings

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["service"] == "apple_music"
    assert len(data["items"]) > 0
    for item in data["items"]:
        assert "rank" in item
        assert "name" in item
        assert "genres" in item


# ── STAT-03: Top genres by period ───────────────────────────────────────────


async def test_top_genres_spotify(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/stats/genres?period=month returns genres with counts (STAT-03)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    p1, p2, p3 = _spotify_stats_mocks(period="month")
    with p1, p2, p3:
        resp = await client.get(
            "/api/stats/genres?period=month",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["service"] == "spotify"
    assert data["period"] == "month"
    assert len(data["items"]) > 0

    for item in data["items"]:
        assert "rank" in item
        assert "genre" in item
        assert "track_count" in item
        assert "artist_count" in item
        assert item["track_count"] > 0
        assert item["artist_count"] > 0


async def test_top_genres_apple_music(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
    test_settings: Settings,
) -> None:
    """GET /api/stats/genres?period=alltime&service=apple_music returns 200 (STAT-03)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "apple_music", expires_in=None
    )

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

    p1, p2 = _apple_stats_mocks()
    with p1, p2:
        resp = await client.get(
            "/api/stats/genres?period=alltime&service=apple_music",
            cookies=auth_cookies,
        )

    app.state.settings = test_settings

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["service"] == "apple_music"
    assert data["period"] == "alltime"
    assert len(data["items"]) > 0
    for item in data["items"]:
        assert "rank" in item
        assert "genre" in item
        assert "track_count" in item
        assert "artist_count" in item


# ── STAT-04: Error handling and service isolation ────────────────────────────


async def test_no_service_returns_400(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """User with no connected service gets 400 (STAT-04)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await client.get(
        "/api/stats/tracks",
        cookies=auth_cookies,
    )
    assert resp.status_code == 400
    assert "No connected service" in resp.json()["detail"]


async def test_invalid_period_returns_400(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/stats/tracks?period=invalid returns 400 (STAT-04)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await client.get(
        "/api/stats/tracks?period=invalid",
        cookies=auth_cookies,
    )
    assert resp.status_code == 400
    assert "Invalid period" in resp.json()["detail"]


async def test_unauthenticated_returns_401(
    client: AsyncClient,
) -> None:
    """GET /api/stats/tracks without auth cookie returns 401 (STAT-04)."""
    resp = await client.get("/api/stats/tracks")
    assert resp.status_code == 401


async def test_limit_param(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/stats/tracks?limit=2 returns at most 2 items (STAT-04)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(
        test_engine, encryption, test_user_id, "spotify"
    )

    # Return only 2 items when limit=2
    limited_tracks = SAMPLE_SPOTIFY_TOP_TRACKS[:2]
    with (
        patch(
            "musicmind.api.stats.service.fetch_spotify_top_tracks_for_period",
            new_callable=AsyncMock,
            return_value=limited_tracks,
        ),
        patch(
            "musicmind.api.stats.service.refresh_spotify_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        resp = await client.get(
            "/api/stats/tracks?limit=2",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["items"]) <= 2
