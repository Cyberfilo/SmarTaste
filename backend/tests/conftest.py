"""Shared test fixtures for MusicMind Web backend tests."""

from __future__ import annotations

import os

import pytest

from musicmind.security.encryption import EncryptionService


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
