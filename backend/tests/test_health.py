"""Health endpoint integration tests."""

from __future__ import annotations

import os

import httpx
import pytest

from musicmind.security.encryption import EncryptionService


@pytest.fixture
def app_env(monkeypatch):
    """Set required environment variables for the app."""
    key = EncryptionService.generate_key()
    monkeypatch.setenv("MUSICMIND_FERNET_KEY", key)
    db_url = os.environ.get(
        "MUSICMIND_TEST_DATABASE_URL",
        "postgresql+asyncpg://musicmind:musicmind@localhost:5432/musicmind_test",
    )
    monkeypatch.setenv("MUSICMIND_DATABASE_URL", db_url)


@pytest.mark.asyncio
async def test_health_check_returns_200(app_env):
    """Health endpoint returns 200 with status field."""
    from musicmind.app import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database" in data
