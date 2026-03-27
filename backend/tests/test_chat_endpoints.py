"""Integration tests for chat HTTP endpoints (CHAT-01, CHAT-02, CHAT-04, CHAT-05, CHAT-09, CHAT-10)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Set env vars before importing app modules
os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.app import app  # noqa: E402
from musicmind.config import Settings  # noqa: E402
from musicmind.db.schema import chat_conversations, metadata, users  # noqa: E402
from musicmind.security.encryption import EncryptionService  # noqa: E402

JWT_SECRET = "test-jwt-secret-key-for-testing-only"
TEST_FERNET_KEY = "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM="
TEST_USER_ID = "test-user-chat-01"
OTHER_USER_ID = "test-user-chat-02"


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_csrf_token(client: AsyncClient) -> str:
    """GET /health to obtain a csrftoken cookie."""
    resp = await client.get("/health")
    return resp.cookies.get("csrftoken") or client.cookies.get("csrftoken", "")


async def _authenticated_post(
    client: AsyncClient,
    url: str,
    *,
    json_data: dict | None = None,
    auth_cookies: dict[str, str],
) -> httpx.Response:
    """POST with CSRF token and auth cookies."""
    csrf_token = await _get_csrf_token(client)
    all_cookies = {"csrftoken": csrf_token, **auth_cookies}
    return await client.post(
        url, json=json_data, headers={"x-csrf-token": csrf_token}, cookies=all_cookies
    )


async def _authenticated_delete(
    client: AsyncClient,
    url: str,
    *,
    auth_cookies: dict[str, str],
) -> httpx.Response:
    """DELETE with CSRF token and auth cookies."""
    csrf_token = await _get_csrf_token(client)
    all_cookies = {"csrftoken": csrf_token, **auth_cookies}
    return await client.delete(url, headers={"x-csrf-token": csrf_token}, cookies=all_cookies)


async def _authenticated_get(
    client: AsyncClient,
    url: str,
    *,
    auth_cookies: dict[str, str],
) -> httpx.Response:
    """GET with auth cookies."""
    return await client.get(url, cookies=auth_cookies)


def _make_jwt(user_id: str, email: str = "test@example.com") -> str:
    """Create a JWT access token for the given user."""
    import jwt

    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "iat": now,
            "exp": now + timedelta(minutes=30),
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def engine_and_tables() -> AsyncIterator[AsyncEngine]:
    """Create an in-memory SQLite engine with all tables and test users."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with engine.begin() as conn:
        await conn.execute(
            users.insert().values(
                id=TEST_USER_ID,
                email="chat-test@example.com",
                password_hash="hashed",
                display_name="Chat Test User",
            )
        )
        await conn.execute(
            users.insert().values(
                id=OTHER_USER_ID,
                email="other@example.com",
                password_hash="hashed",
                display_name="Other User",
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
def test_settings() -> Settings:
    """Settings for chat endpoint tests."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        fernet_key=TEST_FERNET_KEY,
        jwt_secret_key=JWT_SECRET,
        debug=True,
    )


@pytest.fixture
async def client(
    engine_and_tables: AsyncEngine, test_settings: Settings
) -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient with test DB and settings overrides."""
    app.state.engine = engine_and_tables
    app.state.settings = test_settings
    app.state.encryption = EncryptionService(TEST_FERNET_KEY)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_cookies() -> dict[str, str]:
    """Valid JWT access_token cookie for the test user."""
    return {"access_token": _make_jwt(TEST_USER_ID)}


@pytest.fixture
def other_auth_cookies() -> dict[str, str]:
    """Valid JWT access_token cookie for another user (isolation tests)."""
    return {"access_token": _make_jwt(OTHER_USER_ID, email="other@example.com")}


@pytest.fixture
async def conversation_in_db(engine_and_tables: AsyncEngine) -> str:
    """Insert a conversation for TEST_USER_ID and return its ID."""
    conv_id = "conv-test-001"
    now = datetime.now(UTC)
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    async with engine_and_tables.begin() as conn:
        await conn.execute(
            chat_conversations.insert().values(
                id=conv_id,
                user_id=TEST_USER_ID,
                title="Hello",
                messages=json.dumps(messages),
                created_at=now,
                updated_at=now,
            )
        )
    return conv_id


@pytest.fixture
async def other_user_conversation(engine_and_tables: AsyncEngine) -> str:
    """Insert a conversation for OTHER_USER_ID and return its ID."""
    conv_id = "conv-other-001"
    now = datetime.now(UTC)
    messages = [{"role": "user", "content": "Private message"}]
    async with engine_and_tables.begin() as conn:
        await conn.execute(
            chat_conversations.insert().values(
                id=conv_id,
                user_id=OTHER_USER_ID,
                title="Private",
                messages=json.dumps(messages),
                created_at=now,
                updated_at=now,
            )
        )
    return conv_id


# ── Mock for ChatService.send_message ────────────────────────────────────────


async def _mock_sse_events():
    """Async generator that yields mock SSE events."""
    yield {"event": "conversation_id", "data": {"id": "mock-conv-id"}}
    yield {"event": "text", "data": {"text": "Hello! How can I help you?"}}
    yield {"event": "done", "data": {}}


# ── TestChatMessageEndpoint ──────────────────────────────────────────────────


class TestChatMessageEndpoint:
    """Tests for POST /api/chat/message SSE streaming endpoint."""

    async def test_message_returns_sse_content_type(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """POST /api/chat/message returns Content-Type text/event-stream."""
        with patch(
            "musicmind.api.chat.router.ChatService"
        ) as MockChatService:
            instance = MockChatService.return_value
            instance.send_message = lambda *a, **kw: _mock_sse_events()
            resp = await _authenticated_post(
                client,
                "/api/chat/message",
                json_data={"message": "Hello"},
                auth_cookies=auth_cookies,
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    async def test_message_without_auth_returns_401(
        self,
        client: AsyncClient,
    ) -> None:
        """POST /api/chat/message without auth returns 401."""
        csrf_token = await _get_csrf_token(client)
        resp = await client.post(
            "/api/chat/message",
            json={"message": "Hello"},
            headers={"x-csrf-token": csrf_token},
            cookies={"csrftoken": csrf_token},
        )
        assert resp.status_code == 401

    async def test_message_sse_format(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """POST /api/chat/message SSE stream contains event and data fields."""
        with patch(
            "musicmind.api.chat.router.ChatService"
        ) as MockChatService:
            instance = MockChatService.return_value
            instance.send_message = lambda *a, **kw: _mock_sse_events()
            resp = await _authenticated_post(
                client,
                "/api/chat/message",
                json_data={"message": "Tell me about jazz"},
                auth_cookies=auth_cookies,
            )
        assert resp.status_code == 200
        body = resp.text
        assert "event: conversation_id" in body
        assert "event: text" in body
        assert "event: done" in body
        assert "data: " in body

    async def test_message_empty_body_returns_422(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """POST /api/chat/message with empty message returns 422."""
        resp = await _authenticated_post(
            client,
            "/api/chat/message",
            json_data={"message": ""},
            auth_cookies=auth_cookies,
        )
        assert resp.status_code == 422


# ── TestConversationListEndpoint ─────────────────────────────────────────────


class TestConversationListEndpoint:
    """Tests for GET /api/chat/conversations."""

    async def test_list_empty_for_new_user(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """GET /api/chat/conversations returns empty list for new user."""
        resp = await _authenticated_get(
            client, "/api/chat/conversations", auth_cookies=auth_cookies
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversations"] == []

    async def test_list_returns_conversations(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
        conversation_in_db: str,
    ) -> None:
        """GET /api/chat/conversations returns existing conversations."""
        resp = await _authenticated_get(
            client, "/api/chat/conversations", auth_cookies=auth_cookies
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["conversations"]) == 1
        conv = data["conversations"][0]
        assert conv["id"] == "conv-test-001"
        assert conv["title"] == "Hello"
        assert conv["message_count"] == 2

    async def test_list_without_auth_returns_401(
        self,
        client: AsyncClient,
    ) -> None:
        """GET /api/chat/conversations without auth returns 401."""
        resp = await client.get("/api/chat/conversations")
        assert resp.status_code == 401

    async def test_list_user_isolation(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
        other_user_conversation: str,
    ) -> None:
        """GET /api/chat/conversations does not show other user's conversations."""
        resp = await _authenticated_get(
            client, "/api/chat/conversations", auth_cookies=auth_cookies
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["conversations"]) == 0


# ── TestConversationDetailEndpoint ───────────────────────────────────────────


class TestConversationDetailEndpoint:
    """Tests for GET /api/chat/conversations/{id}."""

    async def test_detail_returns_conversation(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
        conversation_in_db: str,
    ) -> None:
        """GET /api/chat/conversations/{id} returns full conversation."""
        resp = await _authenticated_get(
            client,
            f"/api/chat/conversations/{conversation_in_db}",
            auth_cookies=auth_cookies,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "conv-test-001"
        assert data["title"] == "Hello"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Hello"
        assert data["messages"][1]["role"] == "assistant"

    async def test_detail_not_found_returns_404(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """GET /api/chat/conversations/{id} returns 404 for non-existent ID."""
        resp = await _authenticated_get(
            client,
            "/api/chat/conversations/nonexistent-id",
            auth_cookies=auth_cookies,
        )
        assert resp.status_code == 404

    async def test_detail_other_user_returns_404(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
        other_user_conversation: str,
    ) -> None:
        """GET /api/chat/conversations/{id} returns 404 for other user's conversation."""
        resp = await _authenticated_get(
            client,
            f"/api/chat/conversations/{other_user_conversation}",
            auth_cookies=auth_cookies,
        )
        assert resp.status_code == 404

    async def test_detail_without_auth_returns_401(
        self,
        client: AsyncClient,
    ) -> None:
        """GET /api/chat/conversations/{id} without auth returns 401."""
        resp = await client.get("/api/chat/conversations/some-id")
        assert resp.status_code == 401


# ── TestConversationDeleteEndpoint ───────────────────────────────────────────


class TestConversationDeleteEndpoint:
    """Tests for DELETE /api/chat/conversations/{id}."""

    async def test_delete_removes_conversation(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
        conversation_in_db: str,
    ) -> None:
        """DELETE /api/chat/conversations/{id} removes the conversation."""
        resp = await _authenticated_delete(
            client,
            f"/api/chat/conversations/{conversation_in_db}",
            auth_cookies=auth_cookies,
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Conversation deleted"

        # Verify it's gone
        get_resp = await _authenticated_get(
            client,
            f"/api/chat/conversations/{conversation_in_db}",
            auth_cookies=auth_cookies,
        )
        assert get_resp.status_code == 404

    async def test_delete_not_found_returns_404(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
    ) -> None:
        """DELETE /api/chat/conversations/{id} returns 404 for non-existent ID."""
        resp = await _authenticated_delete(
            client,
            "/api/chat/conversations/nonexistent-id",
            auth_cookies=auth_cookies,
        )
        assert resp.status_code == 404

    async def test_delete_other_user_returns_404(
        self,
        client: AsyncClient,
        auth_cookies: dict[str, str],
        other_user_conversation: str,
    ) -> None:
        """DELETE /api/chat/conversations/{id} returns 404 for other user's conversation."""
        resp = await _authenticated_delete(
            client,
            f"/api/chat/conversations/{other_user_conversation}",
            auth_cookies=auth_cookies,
        )
        assert resp.status_code == 404

    async def test_delete_without_auth_returns_401(
        self,
        client: AsyncClient,
    ) -> None:
        """DELETE /api/chat/conversations/{id} without auth returns 401."""
        csrf_token = await _get_csrf_token(client)
        resp = await client.delete(
            "/api/chat/conversations/some-id",
            headers={"x-csrf-token": csrf_token},
            cookies={"csrftoken": csrf_token},
        )
        assert resp.status_code == 401


# ── TestChatAuthRequired ─────────────────────────────────────────────────────


class TestChatAuthRequired:
    """Tests that all chat endpoints require authentication."""

    async def test_message_requires_auth(self, client: AsyncClient) -> None:
        """POST /api/chat/message requires auth."""
        csrf_token = await _get_csrf_token(client)
        resp = await client.post(
            "/api/chat/message",
            json={"message": "Hello"},
            headers={"x-csrf-token": csrf_token},
            cookies={"csrftoken": csrf_token},
        )
        assert resp.status_code == 401

    async def test_list_requires_auth(self, client: AsyncClient) -> None:
        """GET /api/chat/conversations requires auth."""
        resp = await client.get("/api/chat/conversations")
        assert resp.status_code == 401

    async def test_detail_requires_auth(self, client: AsyncClient) -> None:
        """GET /api/chat/conversations/{id} requires auth."""
        resp = await client.get("/api/chat/conversations/any-id")
        assert resp.status_code == 401

    async def test_delete_requires_auth(self, client: AsyncClient) -> None:
        """DELETE /api/chat/conversations/{id} requires auth."""
        csrf_token = await _get_csrf_token(client)
        resp = await client.delete(
            "/api/chat/conversations/any-id",
            headers={"x-csrf-token": csrf_token},
            cookies={"csrftoken": csrf_token},
        )
        assert resp.status_code == 401
