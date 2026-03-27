"""Tests for ChatService agentic loop, SSE streaming, and conversation persistence (09-02).

Tests the core ChatService: sending messages to Anthropic API with MusicMind tool
definitions, executing tool calls, streaming SSE events, managing context windows,
and persisting conversations.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.db.schema import (  # noqa: E402
    chat_conversations,
    metadata,
    service_connections,
    taste_profile_snapshots,
    user_api_keys,
    users,
)
from musicmind.security.encryption import EncryptionService  # noqa: E402

TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="
TEST_USER_ID = "test-user-chat-01"
TEST_API_KEY = "sk-ant-api03-test-chat-key"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for chat service tests."""
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
async def seeded_engine(
    test_engine: AsyncEngine,
    encryption: EncryptionService,
) -> AsyncEngine:
    """Engine with test user and API key pre-inserted."""
    encrypted_key = encryption.encrypt(TEST_API_KEY)
    async with test_engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id=TEST_USER_ID,
                email="chattest@example.com",
                password_hash="hashed",
                display_name="Chat Test User",
            )
        )
        await conn.execute(
            user_api_keys.insert().values(
                user_id=TEST_USER_ID,
                service="anthropic",
                api_key_encrypted=encrypted_key,
            )
        )
    return test_engine


# ── Mock Helpers ────────────────────────────────────────────────────────────


def _make_text_block(text: str) -> MagicMock:
    """Create a mock TextBlock."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(tool_id: str, name: str, input_data: dict) -> MagicMock:
    """Create a mock ToolUseBlock."""
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_data
    return block


def _make_text_delta(text: str) -> MagicMock:
    """Create a mock TextDelta event."""
    delta = MagicMock()
    delta.type = "text_delta"
    delta.text = text
    return delta


def _make_input_json_delta(partial_json: str) -> MagicMock:
    """Create a mock InputJSONDelta event."""
    delta = MagicMock()
    delta.type = "input_json_delta"
    delta.partial_json = partial_json
    return delta


def _make_content_block_start(index: int, content_block: MagicMock) -> MagicMock:
    """Create a mock ContentBlockStartEvent."""
    event = MagicMock()
    event.type = "content_block_start"
    event.index = index
    event.content_block = content_block
    return event


def _make_content_block_delta(index: int, delta: MagicMock) -> MagicMock:
    """Create a mock ContentBlockDeltaEvent."""
    event = MagicMock()
    event.type = "content_block_delta"
    event.index = index
    event.delta = delta
    return event


def _make_content_block_stop(index: int) -> MagicMock:
    """Create a mock ContentBlockStopEvent."""
    event = MagicMock()
    event.type = "content_block_stop"
    event.index = index
    return event


def _make_message_start(message_id: str = "msg_01") -> MagicMock:
    """Create a mock MessageStartEvent."""
    event = MagicMock()
    event.type = "message_start"
    event.message = MagicMock()
    event.message.id = message_id
    event.message.role = "assistant"
    event.message.content = []
    event.message.stop_reason = None
    return event


def _make_message_delta(stop_reason: str = "end_turn") -> MagicMock:
    """Create a mock MessageDeltaEvent."""
    event = MagicMock()
    event.type = "message_delta"
    event.delta = MagicMock()
    event.delta.stop_reason = stop_reason
    event.usage = MagicMock()
    event.usage.output_tokens = 50
    return event


def _make_message_stop() -> MagicMock:
    """Create a mock MessageStopEvent."""
    event = MagicMock()
    event.type = "message_stop"
    return event


class MockAsyncStream:
    """Mock AsyncMessageStream that yields events and provides final message."""

    def __init__(
        self,
        events: list[MagicMock],
        final_message: MagicMock | None = None,
    ) -> None:
        self._events = events
        self._final_message = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self._iter_events()

    async def _iter_events(self):
        for event in self._events:
            yield event

    async def get_final_message(self) -> MagicMock:
        return self._final_message


def _build_text_response_stream(
    text: str,
    stop_reason: str = "end_turn",
) -> MockAsyncStream:
    """Build a mock stream that yields a simple text response."""
    text_block = _make_text_block(text)
    final_msg = MagicMock()
    final_msg.id = "msg_text_01"
    final_msg.role = "assistant"
    final_msg.content = [text_block]
    final_msg.stop_reason = stop_reason

    events = [
        _make_message_start("msg_text_01"),
        _make_content_block_start(0, text_block),
        _make_content_block_delta(0, _make_text_delta(text)),
        _make_content_block_stop(0),
        _make_message_delta(stop_reason),
        _make_message_stop(),
    ]
    return MockAsyncStream(events, final_msg)


def _build_tool_use_stream(
    tool_id: str,
    tool_name: str,
    tool_input: dict,
) -> MockAsyncStream:
    """Build a mock stream that yields a tool_use block."""
    tool_block = _make_tool_use_block(tool_id, tool_name, tool_input)
    final_msg = MagicMock()
    final_msg.id = "msg_tool_01"
    final_msg.role = "assistant"
    final_msg.content = [tool_block]
    final_msg.stop_reason = "tool_use"

    events = [
        _make_message_start("msg_tool_01"),
        _make_content_block_start(0, tool_block),
        _make_content_block_delta(0, _make_input_json_delta(json.dumps(tool_input))),
        _make_content_block_stop(0),
        _make_message_delta("tool_use"),
        _make_message_stop(),
    ]
    return MockAsyncStream(events, final_msg)


# ── TestChatServiceTextResponse ─────────────────────────────────────────────


class TestChatServiceTextResponse:
    """Tests for simple text responses (no tool use)."""

    async def test_text_response_yields_text_events(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """send_message yields 'text' events for a simple text response."""
        from musicmind.api.chat.service import ChatService

        stream = _build_text_response_stream("Hello! I can help with music.")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Hello!",
            ):
                events.append(event)

        event_types = [e["event"] for e in events]
        assert "text" in event_types
        assert "done" in event_types
        # Last event must be "done"
        assert events[-1]["event"] == "done"

    async def test_text_response_yields_conversation_id(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """send_message yields 'conversation_id' as the first event for new conversations."""
        from musicmind.api.chat.service import ChatService

        stream = _build_text_response_stream("Hi there!")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Hey!",
            ):
                events.append(event)

        assert events[0]["event"] == "conversation_id"
        assert "id" in events[0]["data"]
        assert len(events[0]["data"]["id"]) > 0

    async def test_text_response_yields_done(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """send_message yields a 'done' event at the end."""
        from musicmind.api.chat.service import ChatService

        stream = _build_text_response_stream("Done responding!")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Wrap up.",
            ):
                events.append(event)

        done_events = [e for e in events if e["event"] == "done"]
        assert len(done_events) == 1
        assert events[-1]["event"] == "done"


# ── TestChatServiceToolUse ──────────────────────────────────────────────────


class TestChatServiceToolUse:
    """Tests for tool_use responses and the agentic loop."""

    async def test_tool_use_yields_tool_start_and_tool_end(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """When Claude returns tool_use, service yields tool_start then tool_end."""
        from musicmind.api.chat.service import ChatService

        # First call: Claude requests a tool
        tool_stream = _build_tool_use_stream(
            "toolu_01", "get_taste_profile", {"service": "spotify"}
        )
        # Second call: Claude returns text after getting tool result
        text_stream = _build_text_response_stream(
            "Based on your taste profile, you like rock music."
        )

        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(side_effect=[tool_stream, text_stream])

        mock_executor = AsyncMock(
            return_value={"genre_vector": {"rock": 0.8}, "top_artists": ["Artist A"]}
        )

        with (
            patch(
                "musicmind.api.chat.service.anthropic.AsyncAnthropic",
                return_value=mock_client,
            ),
            patch(
                "musicmind.api.chat.service.TOOL_EXECUTORS",
                {"get_taste_profile": mock_executor},
            ),
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="What is my taste profile?",
            ):
                events.append(event)

        event_types = [e["event"] for e in events]
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        # tool_start should come before tool_end
        start_idx = event_types.index("tool_start")
        end_idx = event_types.index("tool_end")
        assert start_idx < end_idx

    async def test_tool_executor_called_with_correct_args(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """Tool executor is called with engine, encryption, settings, user_id, and tool input."""
        from musicmind.api.chat.service import ChatService

        tool_stream = _build_tool_use_stream(
            "toolu_02", "get_recommendations", {"strategy": "all", "limit": 5}
        )
        text_stream = _build_text_response_stream("Here are your recommendations.")

        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(side_effect=[tool_stream, text_stream])

        mock_executor = AsyncMock(return_value={"tracks": []})

        with (
            patch(
                "musicmind.api.chat.service.anthropic.AsyncAnthropic",
                return_value=mock_client,
            ),
            patch(
                "musicmind.api.chat.service.TOOL_EXECUTORS",
                {"get_recommendations": mock_executor},
            ),
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Recommend me music",
            ):
                events.append(event)

        mock_executor.assert_called_once()
        call_kwargs = mock_executor.call_args
        assert call_kwargs.kwargs["user_id"] == TEST_USER_ID
        assert call_kwargs.kwargs["strategy"] == "all"
        assert call_kwargs.kwargs["limit"] == 5

    async def test_agentic_loop_caps_at_max_tool_calls(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """Agentic loop stops after MAX_TOOL_CALLS (10) tool invocations."""
        from musicmind.api.chat.service import ChatService

        # Create 12 tool_use streams (exceeds the 10 cap)
        tool_streams = [
            _build_tool_use_stream(f"toolu_{i:02d}", "get_top_genres", {})
            for i in range(12)
        ]
        # Final text stream (should be reached after cap hit)
        text_stream = _build_text_response_stream("Done after many tools.")

        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(
            side_effect=tool_streams + [text_stream]
        )

        mock_executor = AsyncMock(return_value={"genres": ["rock"]})

        with (
            patch(
                "musicmind.api.chat.service.anthropic.AsyncAnthropic",
                return_value=mock_client,
            ),
            patch(
                "musicmind.api.chat.service.TOOL_EXECUTORS",
                {"get_top_genres": mock_executor},
            ),
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Keep calling tools",
            ):
                events.append(event)

        tool_start_count = sum(1 for e in events if e["event"] == "tool_start")
        assert tool_start_count <= ChatService.MAX_TOOL_CALLS
        assert events[-1]["event"] == "done"


# ── TestChatServiceErrorHandling ────────────────────────────────────────────


class TestChatServiceErrorHandling:
    """Tests for Anthropic API error handling."""

    async def test_401_yields_error_event(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """401 AuthenticationError yields error event with user-friendly message."""
        import anthropic

        from musicmind.api.chat.service import ChatService

        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(
            side_effect=anthropic.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401),
                body=None,
            )
        )

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Test auth error",
            ):
                events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) >= 1
        assert "expired or invalid" in error_events[0]["data"]["message"].lower() or \
               "api key" in error_events[0]["data"]["message"].lower()

    async def test_429_yields_rate_limit_error(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """429 RateLimitError yields error event about rate limiting."""
        import anthropic

        from musicmind.api.chat.service import ChatService

        mock_response = MagicMock(status_code=429)
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(
            side_effect=anthropic.RateLimitError(
                message="Rate limit exceeded",
                response=mock_response,
                body=None,
            )
        )

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Test rate limit",
            ):
                events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) >= 1
        assert "rate limit" in error_events[0]["data"]["message"].lower()

    async def test_insufficient_balance_yields_error(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """Insufficient balance (402) yields error about API balance."""
        import anthropic

        from musicmind.api.chat.service import ChatService

        mock_response = MagicMock(status_code=402)
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(
            side_effect=anthropic.APIStatusError(
                message="Insufficient balance",
                response=mock_response,
                body=None,
            )
        )

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Test balance error",
            ):
                events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) >= 1
        msg = error_events[0]["data"]["message"].lower()
        assert "balance" in msg or "insufficient" in msg

    async def test_no_api_key_yields_error(
        self,
        test_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """No API key stored yields error event 'No API key configured'."""
        from musicmind.api.chat.service import ChatService

        # Insert user without API key
        async with test_engine.begin() as conn:
            await conn.execute(
                users.insert().values(
                    id="no-key-user",
                    email="nokey@example.com",
                    password_hash="hashed",
                    display_name="No Key User",
                )
            )

        service = ChatService()
        events = []
        async for event in service.send_message(
            test_engine,
            encryption,
            test_settings,
            user_id="no-key-user",
            conversation_id=None,
            message="Test no key",
        ):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) >= 1
        assert "no api key" in error_events[0]["data"]["message"].lower()


# ── TestChatServiceConversationPersistence ──────────────────────────────────


class TestChatServiceConversationPersistence:
    """Tests for conversation create/load/persist."""

    async def test_new_conversation_creates_db_row(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """New conversation (conversation_id=None) creates a row in chat_conversations."""
        from musicmind.api.chat.service import ChatService

        stream = _build_text_response_stream("Saved response!")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            conv_id = None
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Start a new conversation",
            ):
                if event["event"] == "conversation_id":
                    conv_id = event["data"]["id"]

        assert conv_id is not None

        # Verify row exists in DB
        async with seeded_engine.begin() as conn:
            result = await conn.execute(
                sa.select(chat_conversations).where(
                    chat_conversations.c.id == conv_id,
                )
            )
            row = result.first()

        assert row is not None
        assert row.user_id == TEST_USER_ID

    async def test_conversation_title_from_first_message(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """Conversation title is auto-generated from first user message (first 50 chars)."""
        from musicmind.api.chat.service import ChatService

        stream = _build_text_response_stream("Response!")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        long_message = "This is a really long message that should get truncated at fifty characters for the title"

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            conv_id = None
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message=long_message,
            ):
                if event["event"] == "conversation_id":
                    conv_id = event["data"]["id"]

        async with seeded_engine.begin() as conn:
            result = await conn.execute(
                sa.select(chat_conversations.c.title).where(
                    chat_conversations.c.id == conv_id,
                )
            )
            row = result.first()

        assert row is not None
        assert len(row.title) <= 50
        assert row.title == long_message[:50]

    async def test_messages_persisted_after_response(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """Messages (user + assistant) are persisted to chat_conversations after response."""
        from musicmind.api.chat.service import ChatService

        stream = _build_text_response_stream("Persisted response!")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            conv_id = None
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Persist this message",
            ):
                if event["event"] == "conversation_id":
                    conv_id = event["data"]["id"]

        async with seeded_engine.begin() as conn:
            result = await conn.execute(
                sa.select(chat_conversations.c.messages).where(
                    chat_conversations.c.id == conv_id,
                )
            )
            row = result.first()

        messages = row.messages if isinstance(row.messages, list) else json.loads(row.messages)
        assert len(messages) >= 2  # At least user + assistant
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles

    async def test_existing_conversation_loads_messages(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """Existing conversation loads previous messages for context."""
        from musicmind.api.chat.service import ChatService

        # Pre-insert a conversation with messages
        existing_messages = json.dumps([
            {"role": "user", "content": "Previous message"},
            {"role": "assistant", "content": "Previous response"},
        ])
        async with seeded_engine.begin() as conn:
            await conn.execute(
                chat_conversations.insert().values(
                    id="existing-conv-01",
                    user_id=TEST_USER_ID,
                    title="Existing Conversation",
                    messages=existing_messages,
                )
            )

        stream = _build_text_response_stream("Continuing conversation!")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id="existing-conv-01",
                message="Continue the conversation",
            ):
                events.append(event)

        # Verify the mock was called with messages including previous context
        call_args = mock_client.messages.stream.call_args
        messages_sent = call_args.kwargs.get("messages", [])
        # Should include previous messages + new user message
        assert len(messages_sent) >= 3  # 2 previous + 1 new


# ── TestChatServiceContextWindow ────────────────────────────────────────────


class TestChatServiceContextWindow:
    """Tests for context window management (last 20 messages)."""

    async def test_context_window_truncates_old_messages(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """Context window keeps last 20 messages when conversation is longer."""
        from musicmind.api.chat.service import ChatService

        # Pre-insert a conversation with 30 messages
        old_messages = []
        for i in range(30):
            role = "user" if i % 2 == 0 else "assistant"
            old_messages.append({"role": role, "content": f"Message {i}"})

        async with seeded_engine.begin() as conn:
            await conn.execute(
                chat_conversations.insert().values(
                    id="long-conv-01",
                    user_id=TEST_USER_ID,
                    title="Long Conversation",
                    messages=json.dumps(old_messages),
                )
            )

        stream = _build_text_response_stream("Short response.")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            events = []
            async for event in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id="long-conv-01",
                message="New message",
            ):
                events.append(event)

        # Check that messages sent to API are truncated
        call_args = mock_client.messages.stream.call_args
        messages_sent = call_args.kwargs.get("messages", [])
        # Should be at most CONTEXT_WINDOW_MESSAGES (20) + 1 new message = 21
        assert len(messages_sent) <= ChatService.CONTEXT_WINDOW_MESSAGES + 1


# ── TestChatServiceSystemPrompt ─────────────────────────────────────────────


class TestChatServiceSystemPrompt:
    """Tests for system prompt construction."""

    async def test_system_prompt_includes_musicmind_identity(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """System prompt includes MusicMind assistant identity."""
        from musicmind.api.chat.service import ChatService

        stream = _build_text_response_stream("I am MusicMind!")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            async for _ in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="Who are you?",
            ):
                pass

        call_args = mock_client.messages.stream.call_args
        system_prompt = call_args.kwargs.get("system", "")
        assert "musicmind" in system_prompt.lower()

    async def test_system_prompt_includes_connected_services(
        self,
        seeded_engine: AsyncEngine,
        encryption: EncryptionService,
        test_settings: MagicMock,
    ) -> None:
        """System prompt includes user's connected services info."""
        from musicmind.api.chat.service import ChatService

        # Insert a spotify connection for the test user
        async with seeded_engine.begin() as conn:
            await conn.execute(
                service_connections.insert().values(
                    user_id=TEST_USER_ID,
                    service="spotify",
                    access_token_encrypted="encrypted-token",
                )
            )

        stream = _build_text_response_stream("I see your Spotify!")
        mock_client = AsyncMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=stream)

        with patch(
            "musicmind.api.chat.service.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            service = ChatService()
            async for _ in service.send_message(
                seeded_engine,
                encryption,
                test_settings,
                user_id=TEST_USER_ID,
                conversation_id=None,
                message="What services do I have?",
            ):
                pass

        call_args = mock_client.messages.stream.call_args
        system_prompt = call_args.kwargs.get("system", "")
        assert "spotify" in system_prompt.lower()
