"""Tests for unified multi-service taste profile (TAST-05, MSVC-01, MSVC-03).

Tests the TasteService unified path: fetching from both services,
deduplication, genre normalization, and merged profile building.
"""

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


# ── Sample Data ──────────────────────────────────────────────────────────────

# Spotify data -- genres come from artist enrichment (lowercase Spotify convention)
SPOTIFY_SONGS = [
    {
        "catalog_id": "sp_track_001",
        "library_id": None,
        "name": "Notti in Bianco",
        "artist_name": "Ultimo",
        "album_name": "Alba",
        "genre_names": ["italian hip-hop", "italian pop"],  # Spotify format
        "duration_ms": 240000,
        "release_date": "2023-04-15",
        "isrc": "ITXYZ0001",  # Same ISRC as Apple Music track below
        "editorial_notes": "",
        "audio_traits": [],
        "has_lyrics": True,
        "content_rating": None,
        "artwork_bg_color": "",
        "artwork_url_template": "",
        "preview_url": "https://spotify.com/preview/1",
        "user_rating": None,
        "date_added_to_library": None,
        "service_source": "spotify",
    },
    {
        "catalog_id": "sp_track_002",
        "library_id": None,
        "name": "Spotify Only Song",
        "artist_name": "SpotifyArtist",
        "album_name": "SpotifyAlbum",
        "genre_names": ["pop", "dance pop"],
        "duration_ms": 200000,
        "release_date": "2024-01-20",
        "isrc": "USXYZ0002",
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

SPOTIFY_HISTORY = [
    {
        "song_id": "sp_track_001",
        "song_name": "Notti in Bianco",
        "artist_name": "Ultimo",
        "album_name": "Alba",
        "genre_names": ["italian hip-hop"],
        "duration_ms": 240000,
    },
]

SPOTIFY_ARTISTS = [
    {"id": "artist_001", "name": "Ultimo", "genres": ["italian hip-hop", "italian pop"]},
    {"id": "artist_002", "name": "SpotifyArtist", "genres": ["pop", "dance pop"]},
]

# Apple Music data -- genres in Title Case with slashes
APPLE_SONGS = [
    {
        "catalog_id": "am_track_001",
        "library_id": "l.abc001",
        "name": "Notti in Bianco",
        "artist_name": "Ultimo",
        "album_name": "Alba",
        "genre_names": ["Italian Hip-Hop/Rap", "Italian Pop"],  # Apple Music format
        "duration_ms": 240000,
        "release_date": "2023-04-15",
        "isrc": "ITXYZ0001",  # Same ISRC as Spotify track above -> dedup target
        "editorial_notes": "A beautiful ballad from one of Italy's most beloved artists.",
        "audio_traits": ["lossless", "spatial"],
        "has_lyrics": True,
        "content_rating": None,
        "artwork_bg_color": "1a1a1a",
        "artwork_url_template": "https://apple.com/artwork/1",
        "preview_url": "",
        "user_rating": 1,
        "date_added_to_library": None,
        "service_source": "apple_music",
    },
    {
        "catalog_id": "am_track_003",
        "library_id": "l.abc003",
        "name": "Apple Only Song",
        "artist_name": "AppleArtist",
        "album_name": "AppleAlbum",
        "genre_names": ["Rock", "Alternative"],
        "duration_ms": 180000,
        "release_date": "2024-06-10",
        "isrc": "ITXYZ0003",
        "editorial_notes": "",
        "audio_traits": ["lossless"],
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

APPLE_HISTORY = [
    {
        "song_id": "am_track_001",
        "song_name": "Notti in Bianco",
        "artist_name": "Ultimo",
        "album_name": "Alba",
        "genre_names": ["Italian Hip-Hop/Rap"],
        "duration_ms": 240000,
    },
]


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _insert_test_user(engine: AsyncEngine, user_id: str) -> None:
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
) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            service_connections.insert().values(
                user_id=user_id,
                service=service,
                access_token_encrypted=encryption.encrypt("test_access_token"),
                refresh_token_encrypted=encryption.encrypt("test_refresh_token"),
                token_expires_at=now + timedelta(hours=1),
                service_user_id=f"{service}_user_123",
                connected_at=now,
            )
        )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=TEST_FERNET_KEY,
        jwt_secret_key=JWT_SECRET,
        debug=True,
        spotify_client_id="test-spotify-client-id",
    )


@pytest.fixture
def encryption() -> EncryptionService:
    return EncryptionService(TEST_FERNET_KEY)


@pytest.fixture
async def client(
    test_engine: AsyncEngine, test_settings: Settings
) -> AsyncIterator[AsyncClient]:
    app.state.engine = test_engine
    app.state.settings = test_settings
    app.state.encryption = EncryptionService(TEST_FERNET_KEY)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def test_user_id() -> str:
    return "test-user-id-unified-01"


@pytest.fixture
def auth_cookies(test_user_id: str) -> dict[str, str]:
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


# ── Mock helpers ─────────────────────────────────────────────────────────────


def _unified_fetch_mocks():
    """Return combined mocks for both Spotify and Apple Music fetch functions."""
    return (
        patch(
            "musicmind.api.taste.service.fetch_spotify_top_tracks",
            new_callable=AsyncMock,
            return_value=list(SPOTIFY_SONGS),
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_top_artists",
            new_callable=AsyncMock,
            return_value=SPOTIFY_ARTISTS,
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_saved_tracks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "musicmind.api.taste.service.fetch_spotify_recently_played",
            new_callable=AsyncMock,
            return_value=SPOTIFY_HISTORY,
        ),
        patch(
            "musicmind.api.taste.service.enrich_spotify_genres",
        ),
        patch(
            "musicmind.api.taste.service.fetch_apple_music_library",
            new_callable=AsyncMock,
            return_value=list(APPLE_SONGS),
        ),
        patch(
            "musicmind.api.taste.service.fetch_apple_music_recently_played",
            new_callable=AsyncMock,
            return_value=APPLE_HISTORY,
        ),
        patch(
            "musicmind.api.taste.service.generate_apple_developer_token",
            return_value="mock-dev-token",
        ),
    )


# ── TasteService Unit Tests ─────────────────────────────────────────────────


class TestUnifiedResolveService:
    """Test _resolve_service auto-detection of unified mode."""

    async def test_both_services_resolves_to_unified(
        self, test_engine, encryption, test_user_id
    ) -> None:
        from musicmind.api.taste.service import TasteService

        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
        await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

        svc = TasteService()
        result = await svc._resolve_service(
            test_engine, user_id=test_user_id, service=None
        )
        assert result == "unified"

    async def test_single_service_resolves_normally(
        self, test_engine, encryption, test_user_id
    ) -> None:
        from musicmind.api.taste.service import TasteService

        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

        svc = TasteService()
        result = await svc._resolve_service(
            test_engine, user_id=test_user_id, service=None
        )
        assert result == "spotify"

    async def test_explicit_service_overrides_unified(
        self, test_engine, encryption, test_user_id
    ) -> None:
        from musicmind.api.taste.service import TasteService

        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
        await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

        svc = TasteService()
        result = await svc._resolve_service(
            test_engine, user_id=test_user_id, service="spotify"
        )
        assert result == "spotify"


class TestBuildUnifiedProfile:
    """Test the unified profile building pipeline."""

    async def test_unified_profile_includes_both_services(
        self, test_engine, encryption, test_settings, test_user_id
    ) -> None:
        """Unified profile fetches from both services and includes both in services_included."""
        from musicmind.api.taste.service import TasteService

        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
        await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

        svc = TasteService()
        mocks = _unified_fetch_mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6], mocks[7]:
            profile = await svc.get_profile(
                test_engine, encryption, test_settings,
                user_id=test_user_id, service="unified", force_refresh=True,
            )

        assert profile["service"] == "unified"
        assert "spotify" in profile["services_included"]
        assert "apple_music" in profile["services_included"]

    async def test_unified_profile_deduplicates_by_isrc(
        self, test_engine, encryption, test_settings, test_user_id
    ) -> None:
        """Tracks with the same ISRC from both services are deduplicated."""
        from musicmind.api.taste.service import TasteService

        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
        await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

        svc = TasteService()
        mocks = _unified_fetch_mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6], mocks[7]:
            profile = await svc.get_profile(
                test_engine, encryption, test_settings,
                user_id=test_user_id, service="unified", force_refresh=True,
            )

        # "Notti in Bianco" appears in both services with same ISRC
        # After dedup: 3 unique songs (1 merged + 1 spotify-only + 1 apple-only)
        assert profile["total_songs_analyzed"] == 3

    async def test_unified_profile_normalizes_genres(
        self, test_engine, encryption, test_settings, test_user_id
    ) -> None:
        """Genres are normalized to canonical form in the unified profile."""
        from musicmind.api.taste.service import TasteService

        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
        await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

        svc = TasteService()
        mocks = _unified_fetch_mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6], mocks[7]:
            profile = await svc.get_profile(
                test_engine, encryption, test_settings,
                user_id=test_user_id, service="unified", force_refresh=True,
            )

        genre_vector = profile.get("genre_vector", {})
        # "italian hip-hop" (Spotify) should normalize to "Italian Hip-Hop/Rap" (canonical)
        # Both should merge into one key
        genre_keys = set(genre_vector.keys())
        # Should NOT have lowercase Spotify-format genres
        assert "italian hip-hop" not in genre_keys
        assert "italian pop" not in genre_keys

    async def test_unified_profile_saved_to_db(
        self, test_engine, encryption, test_settings, test_user_id
    ) -> None:
        """Unified profile is saved as snapshot with service_source='unified'."""
        from musicmind.api.taste.service import TasteService

        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
        await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

        svc = TasteService()
        mocks = _unified_fetch_mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6], mocks[7]:
            await svc.get_profile(
                test_engine, encryption, test_settings,
                user_id=test_user_id, service="unified", force_refresh=True,
            )

        # Check the snapshot was saved
        async with test_engine.begin() as conn:
            result = await conn.execute(
                sa.select(taste_profile_snapshots).where(
                    sa.and_(
                        taste_profile_snapshots.c.user_id == test_user_id,
                        taste_profile_snapshots.c.service_source == "unified",
                    )
                )
            )
            row = result.first()

        assert row is not None
        assert row.service_source == "unified"

    async def test_unified_fallback_single_service(
        self, test_engine, encryption, test_settings, test_user_id
    ) -> None:
        """If only one service connected, unified still works with that service."""
        from musicmind.api.taste.service import TasteService

        await _insert_test_user(test_engine, test_user_id)
        # Only Spotify connected
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

        svc = TasteService()
        mocks = _unified_fetch_mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6], mocks[7]:
            profile = await svc.get_profile(
                test_engine, encryption, test_settings,
                user_id=test_user_id, service="unified", force_refresh=True,
            )

        assert profile["service"] == "unified"
        assert "spotify" in profile["services_included"]
        # Apple Music skipped gracefully (no connection -> ValueError caught)
        assert profile["total_songs_analyzed"] > 0


# ── Integration Tests (HTTP) ────────────────────────────────────────────────


class TestUnifiedTasteEndpoints:
    """Test taste profile API endpoints with unified service."""

    async def test_profile_endpoint_returns_unified(
        self, client, test_engine, encryption, test_user_id, auth_cookies
    ) -> None:
        """GET /api/taste/profile?service=unified returns unified profile."""
        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
        await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

        mocks = _unified_fetch_mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6], mocks[7]:
            resp = await client.get(
                "/api/taste/profile?service=unified&refresh=true",
                cookies=auth_cookies,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "unified"
        assert isinstance(data["services_included"], list)
        assert len(data["services_included"]) >= 1

    async def test_profile_endpoint_autodetects_unified(
        self, client, test_engine, encryption, test_user_id, auth_cookies
    ) -> None:
        """GET /api/taste/profile without service param auto-detects unified."""
        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
        await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

        mocks = _unified_fetch_mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6], mocks[7]:
            resp = await client.get(
                "/api/taste/profile?refresh=true",
                cookies=auth_cookies,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "unified"

    async def test_services_included_field_present(
        self, client, test_engine, encryption, test_user_id, auth_cookies
    ) -> None:
        """Response includes services_included when unified."""
        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
        await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

        mocks = _unified_fetch_mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6], mocks[7]:
            resp = await client.get(
                "/api/taste/profile?service=unified&refresh=true",
                cookies=auth_cookies,
            )

        data = resp.json()
        assert "services_included" in data
        assert "spotify" in data["services_included"]
        assert "apple_music" in data["services_included"]

    async def test_single_service_still_works(
        self, client, test_engine, encryption, test_user_id, auth_cookies
    ) -> None:
        """Explicit service=spotify still works as before."""
        await _insert_test_user(test_engine, test_user_id)
        await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

        mocks = _unified_fetch_mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
            resp = await client.get(
                "/api/taste/profile?service=spotify&refresh=true",
                cookies=auth_cookies,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "spotify"
        # Single-service: services_included should be empty
        assert data["services_included"] == []
