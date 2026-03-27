"""Shared test fixtures for MusicMind Web backend tests."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from musicmind.config import Settings
from musicmind.security.encryption import EncryptionService

# Ensure env vars are set before any app module imports
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

JWT_TEST_SECRET = "test-jwt-secret-key-for-testing-only"


@pytest.fixture
def fernet_key() -> str:
    """Generate a fresh Fernet key for testing."""
    return EncryptionService.generate_key()


@pytest.fixture
def encryption_service(fernet_key: str) -> EncryptionService:
    """Create an EncryptionService with a test key."""
    return EncryptionService(fernet_key)


@pytest.fixture
def test_database_url() -> str:
    """Return test database URL from environment or default."""
    return os.environ.get(
        "MUSICMIND_TEST_DATABASE_URL",
        "postgresql+asyncpg://musicmind:musicmind@localhost:5432/musicmind_test",
    )


@pytest.fixture
def test_settings(fernet_key: str) -> Settings:
    """Create Settings with test values for auth testing."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=fernet_key,
        jwt_secret_key=JWT_TEST_SECRET,
        debug=True,
    )


@pytest.fixture
def test_user_id() -> str:
    """Return a deterministic test user ID."""
    return str(uuid.uuid4())


@pytest.fixture
def auth_cookies(test_user_id: str) -> dict[str, str]:
    """Create valid access token cookie dict for protected endpoint tests."""
    now = datetime.now(UTC)
    access_token = jwt.encode(
        {
            "sub": test_user_id,
            "email": "test@example.com",
            "iat": now,
            "exp": now + timedelta(minutes=30),
            "type": "access",
        },
        JWT_TEST_SECRET,
        algorithm="HS256",
    )
    return {"access_token": access_token}
