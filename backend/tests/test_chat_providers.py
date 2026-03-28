"""Tests for LLM provider dispatch in ChatService (12-01).

Verifies that ChatService correctly dispatches to ClaudeProvider or
OpenAIProvider based on the model parameter, and handles missing API keys.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.db.schema import (  # noqa: E402
    metadata,
    user_api_keys,
    users,
)
from musicmind.security.encryption import EncryptionService  # noqa: E402

TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="
TEST_USER_ID = "test-user-provider-01"
TEST_ANTHROPIC_KEY = "sk-ant-api03-test-key"
TEST_OPENAI_KEY = "sk-proj-test-openai-key"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for provider dispatch tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def encryption() -> EncryptionService:
    """EncryptionService using the test Fernet key."""
    return EncryptionService(TEST_FERNET_KEY)


@pytest.fixture
def test_settings() -> MagicMock:
    """Mock settings object."""
    settings = MagicMock()
    settings.debug = True
    return settings


@pytest.fixture
async def engine_with_user(test_engine: AsyncEngine) -> AsyncEngine:
    """Engine with a test user (no API keys)."""
    async with test_engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id=TEST_USER_ID,
                email="provider-test@example.com",
                password_hash="hashed",
                display_name="Provider Test User",
            )
        )
    return test_engine


@pytest.fixture
async def engine_with_claude_key(
    engine_with_user: AsyncEngine, encryption: EncryptionService
) -> AsyncEngine:
    """Engine with test user and Anthropic API key."""
    encrypted = encryption.encrypt(TEST_ANTHROPIC_KEY)
    async with engine_with_user.begin() as conn:
        await conn.execute(
            user_api_keys.insert().values(
                user_id=TEST_USER_ID,
                service="anthropic",
                api_key_encrypted=encrypted,
            )
        )
    return engine_with_user


@pytest.fixture
async def engine_with_openai_key(
    engine_with_user: AsyncEngine, encryption: EncryptionService
) -> AsyncEngine:
    """Engine with test user and OpenAI API key."""
    encrypted = encryption.encrypt(TEST_OPENAI_KEY)
    async with engine_with_user.begin() as conn:
        await conn.execute(
            user_api_keys.insert().values(
                user_id=TEST_USER_ID,
                service="openai",
                api_key_encrypted=encrypted,
            )
        )
    return engine_with_user


# ── Mock Provider Helper ───────────────────────────────────────────────────


async def _mock_provider_stream(**kwargs: Any):
    """Mock provider that yields text and done events."""
    yield {"event": "text", "data": {"text": "Mock response"}}


# ── Tests ──────────────────────────────────────────────────────────────────


class TestProviderDispatch:
    """Tests for model-based provider dispatch."""

    async def test_default_model_uses_claude(
        self,
        engine_with_claude_key: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """model=None defaults to ClaudeProvider."""
        from musicmind.api.chat.service import ChatService

        with (
            patch(
                "musicmind.api.chat.service.ClaudeProvider"
            ) as mock_claude,
            patch(
                "musicmind.api.chat.service.build_system_prompt",
                new=AsyncMock(return_value="system prompt"),
            ),
        ):
            mock_instance = mock_claude.return_value
            mock_instance.stream_response = MagicMock(
                return_value=_mock_provider_stream()
            )

            service = ChatService()
            events = []
            async for event in service.send_message(
                engine_with_claude_key,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Hello",
                model=None,
            ):
                events.append(event)

            mock_claude.assert_called_once()

    async def test_claude_model_uses_claude_provider(
        self,
        engine_with_claude_key: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """model='claude' uses ClaudeProvider."""
        from musicmind.api.chat.service import ChatService

        with (
            patch(
                "musicmind.api.chat.service.ClaudeProvider"
            ) as mock_claude,
            patch(
                "musicmind.api.chat.service.build_system_prompt",
                new=AsyncMock(return_value="system prompt"),
            ),
        ):
            mock_instance = mock_claude.return_value
            mock_instance.stream_response = MagicMock(
                return_value=_mock_provider_stream()
            )

            service = ChatService()
            events = []
            async for event in service.send_message(
                engine_with_claude_key,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Hello",
                model="claude",
            ):
                events.append(event)

            mock_claude.assert_called_once()

    async def test_openai_model_uses_openai_provider(
        self,
        engine_with_openai_key: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """model='openai' uses OpenAIProvider."""
        from musicmind.api.chat.service import ChatService

        with (
            patch(
                "musicmind.api.chat.service.OpenAIProvider"
            ) as mock_openai,
            patch(
                "musicmind.api.chat.service.build_system_prompt",
                new=AsyncMock(return_value="system prompt"),
            ),
        ):
            mock_instance = mock_openai.return_value
            mock_instance.stream_response = MagicMock(
                return_value=_mock_provider_stream()
            )

            service = ChatService()
            events = []
            async for event in service.send_message(
                engine_with_openai_key,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Hello",
                model="openai",
            ):
                events.append(event)

            mock_openai.assert_called_once()


class TestMissingApiKey:
    """Tests for missing API key error handling."""

    async def test_missing_claude_key_yields_error(
        self,
        engine_with_user: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """No Claude API key yields error event with appropriate message."""
        from musicmind.api.chat.service import ChatService

        service = ChatService()
        events = []
        async for event in service.send_message(
            engine_with_user,
            encryption,
            test_settings,
            user_id=TEST_USER_ID,
            conversation_id=None,
            message="Hello",
            model="claude",
        ):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) >= 1
        assert "anthropic" in error_events[0]["data"]["message"].lower() or \
               "no api key" in error_events[0]["data"]["message"].lower()

    async def test_missing_openai_key_yields_error(
        self,
        engine_with_user: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """No OpenAI API key yields error event with appropriate message."""
        from musicmind.api.chat.service import ChatService

        service = ChatService()
        events = []
        async for event in service.send_message(
            engine_with_user,
            encryption,
            test_settings,
            user_id=TEST_USER_ID,
            conversation_id=None,
            message="Hello",
            model="openai",
        ):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) >= 1
        assert "openai" in error_events[0]["data"]["message"].lower() or \
               "no openai" in error_events[0]["data"]["message"].lower()
