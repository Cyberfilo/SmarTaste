"""Tests for cross-service unified recommendations (MSVC-02, MSVC-04).

Tests that RecommendationService draws from both Spotify and Apple Music
catalogs when the user has both services connected, with cross-service
deduplication and genre normalization applied to candidates.
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

from musicmind.app import app  # noqa: E402
from musicmind.config import Settings  # noqa: E402
from musicmind.db.schema import (  # noqa: E402
    metadata,
    service_connections,
    users,
)
from musicmind.security.encryption import EncryptionService  # noqa: E402

JWT_SECRET = "test-jwt-secret-key-for-testing-only"
TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="


# ── Sample Data ──────────────────────────────────────────────────────────────

UNIFIED_PROFILE = {
    "service": "unified",
    "computed_at": "2026-03-26T10:00:00",
    "genre_vector": {
        "Italian Hip-Hop/Rap": 0.5,
        "Italian Pop": 0.3,
        "Pop": 0.1,
        "Rock": 0.1,
    },
    "top_artists": [
        {"name": "Ultimo", "score": 0.9, "song_count": 15},
        {"name": "Rose Villain", "score": 0.6, "song_count": 8},
    ],
    "audio_trait_preferences": {"energy": 0.7},
    "release_year_distribution": {"2024": 0.5, "2023": 0.3, "2022": 0.2},
    "familiarity_score": 0.4,
    "total_songs_analyzed": 80,
    "listening_hours_estimated": 150.0,
    "services_included": ["spotify", "apple_music"],
}

# Spotify discovery candidates
SPOTIFY_CANDIDATES = [
    {
        "catalog_id": "sp_reco_001",
        "name": "Luna Piena",
        "artist_name": "Gazzelle",
        "album_name": "Tuttevogliamobene",
        "genre_names": ["italian pop", "indie pop"],  # Spotify format
        "duration_ms": 220000,
        "release_date": "2024-03-01",
        "isrc": "ITRECO001",
        "artwork_url_template": "https://example.com/art/1.jpg",
        "preview_url": "https://example.com/preview1.mp3",
        "service_source": "spotify",
    },
    {
        "catalog_id": "sp_reco_002",
        "name": "Cenere",
        "artist_name": "Lazza",
        "album_name": "Sirio",
        "genre_names": ["italian hip-hop", "rap"],  # Spotify format
        "duration_ms": 195000,
        "release_date": "2023-05-15",
        "isrc": "ITRECO002",  # Same ISRC as Apple candidate below -> should dedup
        "artwork_url_template": "https://example.com/art/2.jpg",
        "preview_url": "https://example.com/preview2.mp3",
        "service_source": "spotify",
    },
]

# Apple Music discovery candidates
APPLE_CANDIDATES = [
    {
        "catalog_id": "am_reco_002",
        "name": "Cenere",
        "artist_name": "Lazza",
        "album_name": "Sirio",
        "genre_names": ["Italian Hip-Hop/Rap", "Hip-Hop/Rap"],  # Apple format
        "duration_ms": 195000,
        "release_date": "2023-05-15",
        "isrc": "ITRECO002",  # Same ISRC as Spotify -> cross-service dedup
        "artwork_url_template": "https://example.com/art/2_am.jpg",
        "preview_url": "",
        "service_source": "apple_music",
        "editorial_notes": "Lazza delivers hard-hitting bars over a moody beat.",
    },
    {
        "catalog_id": "am_reco_003",
        "name": "Maledetta",
        "artist_name": "Annalisa",
        "album_name": "E poi siamo finiti nel vortice",
        "genre_names": ["Italian Pop", "Pop"],
        "duration_ms": 210000,
        "release_date": "2024-01-10",
        "isrc": "ITRECO003",
        "artwork_url_template": "https://example.com/art/3.jpg",
        "preview_url": "",
        "service_source": "apple_music",
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
    return "test-user-id-unified-reco-01"


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


def _unified_reco_mocks(
    *,
    spotify_candidates: list | None = None,
    apple_candidates: list | None = None,
):
    """Return patch context managers for unified recommendation mocks.

    Discovery functions return service-specific candidates. The first call
    returns Spotify candidates, the second returns Apple Music candidates
    (since asyncio.gather calls them in connection order).
    """
    sp_cands = spotify_candidates if spotify_candidates is not None else SPOTIFY_CANDIDATES
    am_cands = apple_candidates if apple_candidates is not None else APPLE_CANDIDATES

    # Mock discover functions to alternate between services
    # When called for spotify, return spotify candidates; for apple_music, return apple candidates
    async def _mock_similar_artists(service, *args, **kwargs):
        return list(sp_cands) if service == "spotify" else list(am_cands)

    async def _mock_genre_adjacent(service, *args, **kwargs):
        return list(sp_cands) if service == "spotify" else list(am_cands)

    async def _mock_editorial(service, *args, **kwargs):
        return list(sp_cands) if service == "spotify" else list(am_cands)

    async def _mock_chart_filter(service, *args, **kwargs):
        return list(sp_cands) if service == "spotify" else list(am_cands)

    return (
        patch(
            "musicmind.api.recommendations.service.discover_similar_artists",
            side_effect=_mock_similar_artists,
        ),
        patch(
            "musicmind.api.recommendations.service.discover_genre_adjacent",
            side_effect=_mock_genre_adjacent,
        ),
        patch(
            "musicmind.api.recommendations.service.discover_editorial",
            side_effect=_mock_editorial,
        ),
        patch(
            "musicmind.api.recommendations.service.discover_chart_filter",
            side_effect=_mock_chart_filter,
        ),
        patch(
            "musicmind.api.recommendations.service._taste_service.get_profile",
            new_callable=AsyncMock,
            return_value=UNIFIED_PROFILE,
        ),
        patch(
            "musicmind.api.recommendations.service.refresh_spotify_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "musicmind.api.recommendations.service.generate_apple_developer_token",
            return_value="mock-dev-token",
        ),
    )


# ── Test: Unified recommendations draw from both services ────────────────────


async def test_unified_recommendations_returns_results(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/recommendations returns results when both services connected."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
    await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

    p1, p2, p3, p4, p5, p6, p7 = _unified_reco_mocks()
    with p1, p2, p3, p4, p5, p6, p7:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    assert len(data["items"]) > 0


async def test_unified_recommendations_deduplicates_across_services(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """Cross-service candidates with same ISRC are deduplicated."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
    await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

    p1, p2, p3, p4, p5, p6, p7 = _unified_reco_mocks()
    with p1, p2, p3, p4, p5, p6, p7:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    data = resp.json()
    # "Cenere" by Lazza appears in both services with same ISRC
    # Should appear at most once in results
    cenere_items = [
        item for item in data["items"]
        if item["name"] == "Cenere" and item["artist_name"] == "Lazza"
    ]
    assert len(cenere_items) <= 1


async def test_unified_recommendations_normalizes_genres(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """Candidate genres are normalized after cross-service dedup."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
    await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

    p1, p2, p3, p4, p5, p6, p7 = _unified_reco_mocks()
    with p1, p2, p3, p4, p5, p6, p7:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    data = resp.json()
    # Check that Spotify-format genres have been normalized
    for item in data["items"]:
        for genre in item.get("genre_names", []):
            # Spotify "italian hip-hop" should be "Italian Hip-Hop/Rap"
            assert genre != "italian hip-hop"
            assert genre != "italian pop"
            assert genre != "rap"


async def test_single_service_still_works(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """Single service recommendations still work correctly."""
    await _insert_test_user(test_engine, test_user_id)
    # Only Spotify connected
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    # Use single-service profile
    single_profile = dict(UNIFIED_PROFILE)
    single_profile["service"] = "spotify"
    single_profile["services_included"] = []

    mocks = (
        patch(
            "musicmind.api.recommendations.service.discover_similar_artists",
            new_callable=AsyncMock,
            return_value=SPOTIFY_CANDIDATES,
        ),
        patch(
            "musicmind.api.recommendations.service.discover_genre_adjacent",
            new_callable=AsyncMock,
            return_value=SPOTIFY_CANDIDATES,
        ),
        patch(
            "musicmind.api.recommendations.service.discover_editorial",
            new_callable=AsyncMock,
            return_value=SPOTIFY_CANDIDATES,
        ),
        patch(
            "musicmind.api.recommendations.service.discover_chart_filter",
            new_callable=AsyncMock,
            return_value=SPOTIFY_CANDIDATES,
        ),
        patch(
            "musicmind.api.recommendations.service._taste_service.get_profile",
            new_callable=AsyncMock,
            return_value=single_profile,
        ),
        patch(
            "musicmind.api.recommendations.service.refresh_spotify_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    )

    with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["items"]) > 0


async def test_unified_recommendations_with_strategy_filter(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """Strategy filter works with unified cross-service recommendations."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")
    await _insert_service_connection(test_engine, encryption, test_user_id, "apple_music")

    p1, p2, p3, p4, p5, p6, p7 = _unified_reco_mocks()
    with p1, p2, p3, p4, p5, p6, p7:
        resp = await client.get(
            "/api/recommendations?strategy=similar_artist",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["strategy"] == "similar_artist"
    assert len(data["items"]) > 0
