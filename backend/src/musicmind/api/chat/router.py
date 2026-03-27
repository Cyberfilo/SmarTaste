"""Chat HTTP endpoints with SSE streaming and conversation CRUD.

Exposes ChatService to HTTP clients via 4 endpoints:
- POST /api/chat/message -- SSE streaming chat
- GET /api/chat/conversations -- list conversations
- GET /api/chat/conversations/{conversation_id} -- conversation detail
- DELETE /api/chat/conversations/{conversation_id} -- delete conversation
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import StreamingResponse

from musicmind.api.chat.schemas import (
    ConversationListItem,
    ConversationListResponse,
    ConversationResponse,
    MessageItem,
    SendMessageRequest,
)
from musicmind.api.chat.service import ChatService
from musicmind.auth.dependencies import get_current_user
from musicmind.db.schema import chat_conversations

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


# ── SSE Helpers ──────────────────────────────────────────────────────────────


async def _sse_generator(events):
    """Format ChatService events as SSE text lines.

    Each event dict has "event" and "data" keys. Outputs standard SSE format:
    event: {type}
    data: {json}

    """
    async for event in events:
        event_type = event.get("event", "message")
        data = json.dumps(event.get("data", {}))
        yield f"event: {event_type}\ndata: {data}\n\n"


# ── POST /api/chat/message ───────────────────────────────────────────────────


@router.post("/message")
async def send_message(
    request: Request,
    body: SendMessageRequest,
    current_user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """Send a chat message and receive a streaming SSE response.

    Creates a ChatService instance and streams events from the agentic loop.
    The response is a Server-Sent Events stream with Content-Type text/event-stream.
    """
    engine = request.app.state.engine
    encryption = request.app.state.encryption
    settings = request.app.state.settings
    user_id = current_user["user_id"]

    chat_service = ChatService()
    events = chat_service.send_message(
        engine,
        encryption,
        settings,
        user_id=user_id,
        conversation_id=body.conversation_id,
        message=body.message,
    )

    return StreamingResponse(
        _sse_generator(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /api/chat/conversations ──────────────────────────────────────────────


@router.get("/conversations")
async def list_conversations(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> ConversationListResponse:
    """List the authenticated user's chat conversations.

    Returns conversations ordered by most recently updated first, with
    metadata including message count for each.
    """
    engine = request.app.state.engine
    user_id = current_user["user_id"]

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                chat_conversations.c.id,
                chat_conversations.c.title,
                chat_conversations.c.messages,
                chat_conversations.c.created_at,
                chat_conversations.c.updated_at,
            )
            .where(chat_conversations.c.user_id == user_id)
            .order_by(chat_conversations.c.updated_at.desc())
        )
        rows = result.fetchall()

    conversations = []
    for row in rows:
        messages = row.messages
        if isinstance(messages, str):
            messages = json.loads(messages)
        message_count = len(messages) if isinstance(messages, list) else 0

        conversations.append(
            ConversationListItem(
                id=row.id,
                title=row.title,
                message_count=message_count,
                created_at=row.created_at.isoformat() if row.created_at else "",
                updated_at=row.updated_at.isoformat() if row.updated_at else "",
            )
        )

    return ConversationListResponse(conversations=conversations)


# ── GET /api/chat/conversations/{conversation_id} ────────────────────────────


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    request: Request,
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
) -> ConversationResponse:
    """Retrieve a full conversation with all messages.

    Returns 404 if the conversation does not exist or belongs to another user
    (user isolation).
    """
    engine = request.app.state.engine
    user_id = current_user["user_id"]

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                chat_conversations.c.id,
                chat_conversations.c.title,
                chat_conversations.c.messages,
                chat_conversations.c.created_at,
                chat_conversations.c.updated_at,
            ).where(
                sa.and_(
                    chat_conversations.c.id == conversation_id,
                    chat_conversations.c.user_id == user_id,
                )
            )
        )
        row = result.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    messages_raw = row.messages
    if isinstance(messages_raw, str):
        messages_raw = json.loads(messages_raw)

    messages = []
    if isinstance(messages_raw, list):
        for msg in messages_raw:
            messages.append(
                MessageItem(
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    tool_use=msg.get("tool_use"),
                    tool_result=msg.get("tool_result"),
                )
            )

    return ConversationResponse(
        id=row.id,
        title=row.title,
        messages=messages,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


# ── DELETE /api/chat/conversations/{conversation_id} ─────────────────────────


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    request: Request,
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Delete a conversation.

    Returns 404 if the conversation does not exist or belongs to another user
    (user isolation -- attempting to delete another user's conversation
    appears identical to deleting a non-existent one).
    """
    engine = request.app.state.engine
    user_id = current_user["user_id"]

    async with engine.begin() as conn:
        result = await conn.execute(
            chat_conversations.delete().where(
                sa.and_(
                    chat_conversations.c.id == conversation_id,
                    chat_conversations.c.user_id == user_id,
                )
            )
        )

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    logger.info("Conversation %s deleted for user %s", conversation_id, user_id)
    return {"message": "Conversation deleted"}
