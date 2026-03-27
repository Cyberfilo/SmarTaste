"""Integration tests for recommendation endpoints (RECO-01 through RECO-06)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
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
    metadata,
    recommendation_feedback,
    service_connections,
    users,
)
from musicmind.security.encryption import EncryptionService  # noqa: E402

JWT_SECRET = "test-jwt-secret-key-for-testing-only"
TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="

# ── Sample Data ──────────────────────────────────────────────────────────────

CANNED_PROFILE = {
    "service": "spotify",
    "computed_at": "2026-03-26T10:00:00",
    "genre_vector": {
        "Italian Hip-Hop/Rap": 0.6,
        "Italian Pop": 0.3,
        "Pop": 0.1,
    },
    "top_artists": [
        {"name": "Ultimo", "score": 0.9, "song_count": 15},
        {"name": "Rose Villain", "score": 0.6, "song_count": 8},
    ],
    "audio_trait_preferences": {"energy": 0.7},
    "release_year_distribution": {"2024": 0.5, "2023": 0.3, "2022": 0.2},
    "familiarity_score": 0.4,
    "total_songs_analyzed": 50,
    "listening_hours_estimated": 100.0,
}

# Canned candidates with varying genre mixes to exercise scoring diversity
CANNED_CANDIDATES = [
    {
        "catalog_id": "sp_cand_001",
        "name": "Luna Piena",
        "artist_name": "Gazzelle",
        "album_name": "Tuttevogliamobene",
        "genre_names": ["Italian Pop", "Italian Indie"],
        "duration_ms": 220000,
        "release_date": "2024-03-01",
        "artwork_url_template": "https://example.com/art/{w}x{h}.jpg",
        "preview_url": "https://example.com/preview1.mp3",
    },
    {
        "catalog_id": "sp_cand_002",
        "name": "Cenere",
        "artist_name": "Lazza",
        "album_name": "Sirio",
        "genre_names": ["Italian Hip-Hop/Rap", "Hip-Hop/Rap"],
        "duration_ms": 195000,
        "release_date": "2023-05-15",
        "artwork_url_template": "https://example.com/art2/{w}x{h}.jpg",
        "preview_url": "https://example.com/preview2.mp3",
    },
    {
        "catalog_id": "sp_cand_003",
        "name": "Maledetta",
        "artist_name": "Annalisa",
        "album_name": "E poi siamo finiti nel vortice",
        "genre_names": ["Italian Pop", "Pop"],
        "duration_ms": 210000,
        "release_date": "2024-01-10",
        "artwork_url_template": "https://example.com/art3/{w}x{h}.jpg",
        "preview_url": "",
    },
    {
        "catalog_id": "sp_cand_004",
        "name": "Supereroi",
        "artist_name": "Mr. Rain",
        "album_name": "Fiori Di Chernobyl",
        "genre_names": ["Italian Pop", "Italian Hip-Hop/Rap"],
        "duration_ms": 230000,
        "release_date": "2022-10-07",
        "artwork_url_template": "https://example.com/art4/{w}x{h}.jpg",
        "preview_url": "https://example.com/preview4.mp3",
    },
    {
        "catalog_id": "sp_cand_005",
        "name": "Rosa e Olindo",
        "artist_name": "Tony Effe",
        "album_name": "Icon",
        "genre_names": ["Italian Hip-Hop/Rap", "Drill"],
        "duration_ms": 185000,
        "release_date": "2024-07-20",
        "artwork_url_template": "https://example.com/art5/{w}x{h}.jpg",
        "preview_url": "https://example.com/preview5.mp3",
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


async def _insert_feedback_rows(
    engine: AsyncEngine,
    user_id: str,
    count: int,
    feedback_type: str = "thumbs_up",
) -> None:
    """Insert multiple feedback rows to reach/exceed the optimization threshold."""
    async with engine.begin() as conn:
        for i in range(count):
            await conn.execute(
                recommendation_feedback.insert().values(
                    user_id=user_id,
                    catalog_id=f"sp_feedback_{i:03d}",
                    feedback_type=feedback_type,
                    predicted_score=0.7,
                    weight_snapshot=json.dumps({"genre": 0.35}),
                )
            )


async def _get_csrf_token(client: AsyncClient) -> str:
    """GET /health to obtain a csrftoken cookie."""
    resp = await client.get("/health")
    return resp.cookies.get("csrftoken") or client.cookies.get("csrftoken", "")


async def _authenticated_post(
    client: AsyncClient,
    url: str,
    *,
    json_body: dict | None = None,
    auth_cookies: dict[str, str],
) -> httpx.Response:
    """POST with CSRF token and auth cookies (required for protected POST endpoints)."""
    csrf_token = await _get_csrf_token(client)
    all_cookies = {"csrftoken": csrf_token, **auth_cookies}
    return await client.post(
        url, json=json_body, headers={"x-csrf-token": csrf_token}, cookies=all_cookies
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for recommendation tests."""
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
    """Deterministic test user ID for recommendation tests."""
    return "test-user-id-reco-01"


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


# ── Mock helpers ──────────────────────────────────────────────────────────────


def _reco_mocks(
    *,
    similar_artist_return: list | None = None,
    genre_adjacent_return: list | None = None,
    editorial_return: list | None = None,
    chart_return: list | None = None,
):
    """Return patch context managers for all recommendation discovery functions.

    Mocks at the service module level where the imported names are used.
    Each discovery function returns CANNED_CANDIDATES by default.
    """
    candidates = CANNED_CANDIDATES
    return (
        patch(
            "musicmind.api.recommendations.service.discover_similar_artists",
            new_callable=AsyncMock,
            return_value=similar_artist_return if similar_artist_return is not None else candidates,
        ),
        patch(
            "musicmind.api.recommendations.service.discover_genre_adjacent",
            new_callable=AsyncMock,
            return_value=genre_adjacent_return if genre_adjacent_return is not None else candidates,
        ),
        patch(
            "musicmind.api.recommendations.service.discover_editorial",
            new_callable=AsyncMock,
            return_value=editorial_return if editorial_return is not None else candidates,
        ),
        patch(
            "musicmind.api.recommendations.service.discover_chart_filter",
            new_callable=AsyncMock,
            return_value=chart_return if chart_return is not None else candidates,
        ),
        patch(
            "musicmind.api.recommendations.service._taste_service.get_profile",
            new_callable=AsyncMock,
            return_value=CANNED_PROFILE,
        ),
        patch(
            "musicmind.api.recommendations.service.refresh_spotify_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    )


# ── RECO-01: Personalized recommendations feed ───────────────────────────────


async def test_get_recommendations_returns_list(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/recommendations returns 200 with scored items list (RECO-01)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    p1, p2, p3, p4, p5, p6 = _reco_mocks()
    with p1, p2, p3, p4, p5, p6:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    assert len(data["items"]) > 0
    assert "total" in data
    assert data["total"] == len(data["items"])
    assert "strategy" in data
    assert data["strategy"] == "all"
    assert "weights_adapted" in data

    # Each item must have required fields
    for item in data["items"]:
        assert "catalog_id" in item
        assert "name" in item
        assert "artist_name" in item
        assert "score" in item
        assert isinstance(item["score"], float)
        assert 0.0 <= item["score"] <= 1.0
        assert "explanation" in item


async def test_get_recommendations_no_service(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/recommendations with no connected service returns 400 (RECO-01)."""
    await _insert_test_user(test_engine, test_user_id)
    # No service connection inserted

    p1, p2, p3, p4, p5, p6 = _reco_mocks()
    with p1, p2, p3, p4, p5, p6:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    assert resp.status_code == 400


async def test_get_recommendations_unauthenticated(
    client: AsyncClient,
) -> None:
    """GET /api/recommendations without auth cookie returns 401 (RECO-01)."""
    resp = await client.get("/api/recommendations")
    assert resp.status_code == 401


# ── RECO-02: Natural-language explanations ───────────────────────────────────


async def test_explanations_present(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """Each recommendation has a non-empty natural-language explanation (RECO-02)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    p1, p2, p3, p4, p5, p6 = _reco_mocks()
    with p1, p2, p3, p4, p5, p6:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    assert resp.status_code == 200, resp.text
    data = resp.json()

    for item in data["items"]:
        explanation = item["explanation"]
        assert isinstance(explanation, str)
        assert len(explanation) > 0
        # Must be natural language -- should not be purely numeric
        assert not explanation.strip().replace(".", "").replace("-", "").isnumeric()


# ── RECO-03: Feedback storage ────────────────────────────────────────────────


async def test_submit_feedback_stored(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """POST /api/recommendations/{catalog_id}/feedback stores feedback (RECO-03)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    catalog_id = "sp_cand_001"
    resp = await _authenticated_post(
        client,
        f"/api/recommendations/{catalog_id}/feedback",
        json_body={"feedback_type": "thumbs_up"},
        auth_cookies=auth_cookies,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["catalog_id"] == catalog_id
    assert data["feedback_type"] == "thumbs_up"
    assert data["recorded"] is True

    # Verify the row was actually inserted into the DB
    async with test_engine.begin() as conn:
        result = await conn.execute(
            sa.select(recommendation_feedback).where(
                sa.and_(
                    recommendation_feedback.c.user_id == test_user_id,
                    recommendation_feedback.c.catalog_id == catalog_id,
                )
            )
        )
        row = result.first()

    assert row is not None
    assert row.feedback_type == "thumbs_up"


async def test_submit_feedback_thumbs_down(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """POST feedback with thumbs_down stores correctly (RECO-03)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await _authenticated_post(
        client,
        "/api/recommendations/sp_cand_002/feedback",
        json_body={"feedback_type": "thumbs_down"},
        auth_cookies=auth_cookies,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["feedback_type"] == "thumbs_down"
    assert data["recorded"] is True


async def test_submit_feedback_invalid_type(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """POST /api/recommendations/{catalog_id}/feedback with invalid type returns 422 (RECO-03)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await _authenticated_post(
        client,
        "/api/recommendations/sp_cand_001/feedback",
        json_body={"feedback_type": "invalid"},
        auth_cookies=auth_cookies,
    )

    assert resp.status_code == 422


# ── RECO-04: Adaptive weights after feedback ─────────────────────────────────


async def test_weights_adapt_after_feedback(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """With 10+ feedback records, response has weights_adapted=true (RECO-04)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    # Insert 12 feedback rows -- above the MIN_FEEDBACK_FOR_OPTIMIZATION threshold (10)
    await _insert_feedback_rows(test_engine, test_user_id, count=12)

    p1, p2, p3, p4, p5, p6 = _reco_mocks()
    with p1, p2, p3, p4, p5, p6:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["weights_adapted"] is True


async def test_weights_default_insufficient_feedback(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """With fewer than 10 feedback records, response has weights_adapted=false (RECO-04)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    # Insert only 5 feedback rows -- below the threshold
    await _insert_feedback_rows(test_engine, test_user_id, count=5)

    p1, p2, p3, p4, p5, p6 = _reco_mocks()
    with p1, p2, p3, p4, p5, p6:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["weights_adapted"] is False


async def test_weights_default_no_feedback(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """With no feedback records at all, response has weights_adapted=false (RECO-04)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    p1, p2, p3, p4, p5, p6 = _reco_mocks()
    with p1, p2, p3, p4, p5, p6:
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["weights_adapted"] is False


# ── RECO-05: Strategy selection ───────────────────────────────────────────────


async def test_strategy_selection(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/recommendations?strategy=similar_artist calls only similar_artist (RECO-05)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    similar_mock = AsyncMock(return_value=CANNED_CANDIDATES)
    genre_mock = AsyncMock(return_value=[])
    editorial_mock = AsyncMock(return_value=[])
    chart_mock = AsyncMock(return_value=[])
    profile_mock = AsyncMock(return_value=CANNED_PROFILE)
    refresh_mock = AsyncMock(return_value=None)

    with (
        patch("musicmind.api.recommendations.service.discover_similar_artists", similar_mock),
        patch("musicmind.api.recommendations.service.discover_genre_adjacent", genre_mock),
        patch("musicmind.api.recommendations.service.discover_editorial", editorial_mock),
        patch("musicmind.api.recommendations.service.discover_chart_filter", chart_mock),
        patch("musicmind.api.recommendations.service._taste_service.get_profile", profile_mock),
        patch("musicmind.api.recommendations.service.refresh_spotify_token", refresh_mock),
    ):
        resp = await client.get(
            "/api/recommendations?strategy=similar_artist",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["strategy"] == "similar_artist"

    # Only the similar_artist mock should have been called
    similar_mock.assert_called_once()
    genre_mock.assert_not_called()
    editorial_mock.assert_not_called()
    chart_mock.assert_not_called()


async def test_invalid_strategy(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/recommendations?strategy=invalid returns 400 (RECO-05)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await client.get(
        "/api/recommendations?strategy=invalid",
        cookies=auth_cookies,
    )

    assert resp.status_code == 400
    assert "strategy" in resp.json()["detail"].lower()


async def test_strategy_all_runs_all_discoverers(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/recommendations with default strategy=all calls all 4 discoverers (RECO-05)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    similar_mock = AsyncMock(return_value=CANNED_CANDIDATES[:2])
    genre_mock = AsyncMock(return_value=CANNED_CANDIDATES[2:4])
    editorial_mock = AsyncMock(return_value=CANNED_CANDIDATES[4:])
    chart_mock = AsyncMock(return_value=[])
    profile_mock = AsyncMock(return_value=CANNED_PROFILE)
    refresh_mock = AsyncMock(return_value=None)

    with (
        patch("musicmind.api.recommendations.service.discover_similar_artists", similar_mock),
        patch("musicmind.api.recommendations.service.discover_genre_adjacent", genre_mock),
        patch("musicmind.api.recommendations.service.discover_editorial", editorial_mock),
        patch("musicmind.api.recommendations.service.discover_chart_filter", chart_mock),
        patch("musicmind.api.recommendations.service._taste_service.get_profile", profile_mock),
        patch("musicmind.api.recommendations.service.refresh_spotify_token", refresh_mock),
    ):
        resp = await client.get("/api/recommendations", cookies=auth_cookies)

    assert resp.status_code == 200, resp.text
    # All 4 discoverers must have been invoked
    similar_mock.assert_called_once()
    genre_mock.assert_called_once()
    editorial_mock.assert_called_once()
    chart_mock.assert_called_once()


# ── RECO-06: Mood filtering ───────────────────────────────────────────────────


async def test_mood_filtering(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/recommendations?mood=chill returns 200 with mood echoed back (RECO-06)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    p1, p2, p3, p4, p5, p6 = _reco_mocks()
    with p1, p2, p3, p4, p5, p6:
        resp = await client.get(
            "/api/recommendations?mood=chill",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mood"] == "chill"


async def test_unknown_mood_422(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
) -> None:
    """GET /api/recommendations?mood=unknown returns 422 (RECO-06)."""
    await _insert_test_user(test_engine, test_user_id)

    resp = await client.get(
        "/api/recommendations?mood=unknown",
        cookies=auth_cookies,
    )

    assert resp.status_code == 422


async def test_mood_alias_energy(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/recommendations?mood=energy returns 200 (aliased to workout) (RECO-06)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    p1, p2, p3, p4, p5, p6 = _reco_mocks()
    with p1, p2, p3, p4, p5, p6:
        resp = await client.get(
            "/api/recommendations?mood=energy",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # "energy" is aliased to "workout" in the service, but the response
    # echoes the requested mood keyword back
    assert data["mood"] == "energy"


async def test_mood_alias_melancholy(
    client: AsyncClient,
    test_engine: AsyncEngine,
    test_user_id: str,
    auth_cookies: dict[str, str],
    encryption: EncryptionService,
) -> None:
    """GET /api/recommendations?mood=melancholy returns 200 (aliased to sad) (RECO-06)."""
    await _insert_test_user(test_engine, test_user_id)
    await _insert_service_connection(test_engine, encryption, test_user_id, "spotify")

    p1, p2, p3, p4, p5, p6 = _reco_mocks()
    with p1, p2, p3, p4, p5, p6:
        resp = await client.get(
            "/api/recommendations?mood=melancholy",
            cookies=auth_cookies,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mood"] == "melancholy"
