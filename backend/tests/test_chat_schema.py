"""Tests for Claude chat integration schema, Pydantic models, and tool registry (09-01).

Covers database schema (chat_conversations table), Pydantic chat models,
and the curated tool registry that maps Claude tool_use calls to MusicMind services.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.db.schema import chat_conversations, metadata, users  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine for chat tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


# ── TestChatConversationsTable ────────────────────────────────────────────────


class TestChatConversationsTable:
    """Tests for chat_conversations table definition in schema.py."""

    def test_table_exists_in_metadata(self) -> None:
        """chat_conversations table exists in metadata."""
        assert "chat_conversations" in metadata.tables

    def test_table_has_expected_columns(self) -> None:
        """chat_conversations has all required columns."""
        col_names = [c.name for c in chat_conversations.columns]
        assert "id" in col_names
        assert "user_id" in col_names
        assert "title" in col_names
        assert "messages" in col_names
        assert "created_at" in col_names
        assert "updated_at" in col_names

    def test_id_is_primary_key(self) -> None:
        """id column is the primary key."""
        pk_cols = [c.name for c in chat_conversations.primary_key.columns]
        assert pk_cols == ["id"]

    def test_user_id_has_foreign_key_to_users(self) -> None:
        """user_id column references users.id with CASCADE delete."""
        fks = list(chat_conversations.c.user_id.foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"

    def test_user_id_is_indexed(self) -> None:
        """user_id column is indexed for query performance."""
        assert chat_conversations.c.user_id.index is True

    def test_title_not_nullable(self) -> None:
        """title column is not nullable."""
        assert chat_conversations.c.title.nullable is False

    async def test_create_table_and_insert(self, test_engine: AsyncEngine) -> None:
        """Can create table and insert a conversation row."""
        # Insert a user first for FK constraint
        async with test_engine.begin() as conn:
            await conn.execute(
                users.insert().values(
                    id="test-user-chat-01",
                    email="chat@test.example.com",
                    password_hash="hashed",
                    display_name="Chat User",
                )
            )
            await conn.execute(
                chat_conversations.insert().values(
                    id="conv-001",
                    user_id="test-user-chat-01",
                    title="Test Conversation",
                    messages="[]",
                )
            )

            result = await conn.execute(
                sa.select(chat_conversations).where(
                    chat_conversations.c.id == "conv-001",
                )
            )
            row = result.first()

        assert row is not None
        assert row.id == "conv-001"
        assert row.user_id == "test-user-chat-01"
        assert row.title == "Test Conversation"


# ── TestChatPydanticSchemas ───────────────────────────────────────────────────


class TestChatPydanticSchemas:
    """Tests for Pydantic request/response models in chat/schemas.py."""

    def test_send_message_request_validates_min_length(self) -> None:
        """SendMessageRequest requires message with min_length=1."""
        from musicmind.api.chat.schemas import SendMessageRequest

        # Valid message
        req = SendMessageRequest(message="Hello Claude")
        assert req.message == "Hello Claude"
        assert req.conversation_id is None

        # Empty message should fail validation
        with pytest.raises(Exception):
            SendMessageRequest(message="")

    def test_send_message_request_with_conversation_id(self) -> None:
        """SendMessageRequest accepts optional conversation_id."""
        from musicmind.api.chat.schemas import SendMessageRequest

        req = SendMessageRequest(conversation_id="conv-123", message="Follow up")
        assert req.conversation_id == "conv-123"
        assert req.message == "Follow up"

    def test_conversation_response_has_all_fields(self) -> None:
        """ConversationResponse has id, title, messages, created_at, updated_at."""
        from musicmind.api.chat.schemas import ConversationResponse

        resp = ConversationResponse(
            id="conv-001",
            title="My Chat",
            messages=[],
            created_at="2026-03-27T00:00:00Z",
            updated_at="2026-03-27T00:00:00Z",
        )
        assert resp.id == "conv-001"
        assert resp.title == "My Chat"
        assert resp.messages == []
        assert resp.created_at == "2026-03-27T00:00:00Z"
        assert resp.updated_at == "2026-03-27T00:00:00Z"

    def test_message_item_accepts_none_tool_fields(self) -> None:
        """MessageItem accepts tool_use=None and tool_result=None."""
        from musicmind.api.chat.schemas import MessageItem

        item = MessageItem(role="user", content="Hello")
        assert item.tool_use is None
        assert item.tool_result is None

    def test_message_item_with_tool_use(self) -> None:
        """MessageItem accepts tool_use dict."""
        from musicmind.api.chat.schemas import MessageItem

        item = MessageItem(
            role="assistant",
            content="",
            tool_use={"name": "get_taste_profile", "id": "tu_123", "input": {}},
        )
        assert item.tool_use is not None
        assert item.tool_use["name"] == "get_taste_profile"

    def test_conversation_list_item_fields(self) -> None:
        """ConversationListItem has id, title, message_count, timestamps."""
        from musicmind.api.chat.schemas import ConversationListItem

        item = ConversationListItem(
            id="conv-001",
            title="Music Chat",
            message_count=5,
            created_at="2026-03-27T00:00:00Z",
            updated_at="2026-03-27T01:00:00Z",
        )
        assert item.id == "conv-001"
        assert item.message_count == 5

    def test_conversation_list_response(self) -> None:
        """ConversationListResponse wraps a list of ConversationListItem."""
        from musicmind.api.chat.schemas import ConversationListItem, ConversationListResponse

        resp = ConversationListResponse(
            conversations=[
                ConversationListItem(
                    id="conv-001",
                    title="Chat 1",
                    message_count=3,
                    created_at="2026-03-27T00:00:00Z",
                    updated_at="2026-03-27T00:00:00Z",
                ),
            ]
        )
        assert len(resp.conversations) == 1
        assert resp.conversations[0].id == "conv-001"


# ── TestToolRegistry ─────────────────────────────────────────────────────────


class TestToolRegistry:
    """Tests for TOOL_DEFINITIONS and TOOL_EXECUTORS in chat/tools.py."""

    def test_tool_definitions_is_list_of_8(self) -> None:
        """TOOL_DEFINITIONS is a list of exactly 8 tool dicts."""
        from musicmind.api.chat.tools import TOOL_DEFINITIONS

        assert isinstance(TOOL_DEFINITIONS, list)
        assert len(TOOL_DEFINITIONS) == 8

    def test_each_definition_has_required_keys(self) -> None:
        """Each tool definition has name, description, input_schema keys."""
        from musicmind.api.chat.tools import TOOL_DEFINITIONS

        for tool in TOOL_DEFINITIONS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool {tool.get('name')} missing 'description'"
            assert "input_schema" in tool, f"Tool {tool.get('name')} missing 'input_schema'"

    def test_all_tool_names_are_unique(self) -> None:
        """Each tool name is unique across all definitions."""
        from musicmind.api.chat.tools import TOOL_DEFINITIONS

        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"

    def test_tool_executors_is_dict_mapping_names_to_callables(self) -> None:
        """TOOL_EXECUTORS maps each tool name to a callable."""
        from musicmind.api.chat.tools import TOOL_EXECUTORS

        assert isinstance(TOOL_EXECUTORS, dict)
        for name, executor in TOOL_EXECUTORS.items():
            assert callable(executor), f"Executor for '{name}' is not callable"

    def test_tool_names_match_between_definitions_and_executors(self) -> None:
        """Tool names in TOOL_DEFINITIONS match keys in TOOL_EXECUTORS."""
        from musicmind.api.chat.tools import TOOL_DEFINITIONS, TOOL_EXECUTORS

        definition_names = {t["name"] for t in TOOL_DEFINITIONS}
        executor_names = set(TOOL_EXECUTORS.keys())
        assert definition_names == executor_names, (
            f"Mismatch: defs={definition_names - executor_names}, "
            f"execs={executor_names - definition_names}"
        )

    def test_expected_tool_names_present(self) -> None:
        """All 8 expected tool names are present."""
        from musicmind.api.chat.tools import TOOL_DEFINITIONS

        names = {t["name"] for t in TOOL_DEFINITIONS}
        expected = {
            "get_taste_profile",
            "get_recommendations",
            "get_listening_stats_tracks",
            "get_listening_stats_artists",
            "get_top_genres",
            "give_feedback",
            "get_recommendations_by_description",
            "adjust_taste_preferences",
        }
        assert expected == names

    def test_input_schema_has_type_object(self) -> None:
        """Each tool's input_schema has type=object and properties."""
        from musicmind.api.chat.tools import TOOL_DEFINITIONS

        for tool in TOOL_DEFINITIONS:
            schema = tool["input_schema"]
            assert schema.get("type") == "object", (
                f"Tool {tool['name']} input_schema type is not 'object'"
            )
            assert "properties" in schema, (
                f"Tool {tool['name']} input_schema missing 'properties'"
            )

    def test_give_feedback_requires_catalog_id_and_feedback_type(self) -> None:
        """give_feedback tool has catalog_id and feedback_type as required."""
        from musicmind.api.chat.tools import TOOL_DEFINITIONS

        feedback_tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "give_feedback")
        required = feedback_tool["input_schema"].get("required", [])
        assert "catalog_id" in required
        assert "feedback_type" in required

    def test_tool_executors_count_matches_definitions(self) -> None:
        """TOOL_EXECUTORS has exactly 8 entries."""
        from musicmind.api.chat.tools import TOOL_EXECUTORS

        assert len(TOOL_EXECUTORS) == 8
