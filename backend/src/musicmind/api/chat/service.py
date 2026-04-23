"""ChatService: dispatches user messages to OpenAI with SmarTaste tools.

V 6.410 — BYOK removed. Chat always uses the global `settings.openai_api_key`
(MUSICMIND_OPENAI_API_KEY). Claude support dropped.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.api.chat.providers.openai import OpenAIProvider
from musicmind.api.chat.system_prompt import build_system_prompt
from musicmind.api.chat.tools import TOOL_DEFINITIONS, TOOL_EXECUTORS
from musicmind.db.schema import chat_conversations, chat_messages

# uuid7 is Python 3.14+ — fall back to uuid4 on 3.13 (Docker/Essentia compat)
_uuid7 = getattr(uuid, "uuid7", uuid.uuid4)

logger = logging.getLogger(__name__)


class ChatService:
    """Orchestrates OpenAI <-> SmarTaste tool bridge with streaming output."""

    CONTEXT_WINDOW_MESSAGES = 20  # max messages to include in context

    async def send_message(
        self,
        engine,
        encryption,
        settings,
        *,
        user_id: str,
        conversation_id: str | None,
        message: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Send a message and yield SSE events as the response streams."""
        api_key = getattr(settings, "openai_api_key", None)
        if not api_key:
            logger.error("MUSICMIND_OPENAI_API_KEY is not configured")
            yield {
                "event": "error",
                "data": {
                    "message": "Chat is temporarily unavailable.",
                },
            }
            yield {"event": "done", "data": {}}
            return

        conversation_messages: list[dict[str, Any]] = []
        if conversation_id is None:
            conversation_id = str(_uuid7())
            title = message[:50]
            await self._create_conversation(engine, conversation_id, user_id, title)
            yield {"event": "conversation_id", "data": {"id": conversation_id}}
        else:
            conversation_messages = await self._load_conversation_messages(
                engine, conversation_id
            )

        context_messages = conversation_messages[-self.CONTEXT_WINDOW_MESSAGES :]
        context_messages.append({"role": "user", "content": message})

        system_prompt = await build_system_prompt(engine, user_id)
        provider_messages = self._to_anthropic_messages(context_messages)

        provider = OpenAIProvider()
        has_error = False

        async for event in provider.stream_response(
            api_key=api_key,
            messages=provider_messages,
            system_prompt=system_prompt,
            tools=TOOL_DEFINITIONS,
            tool_executors=TOOL_EXECUTORS,
            engine=engine,
            encryption=encryption,
            settings=settings,
            user_id=user_id,
        ):
            yield event
            if event.get("event") == "error":
                has_error = True

        if not has_error:
            new_messages = provider_messages[len(context_messages):]
            await self._persist_messages(
                engine, conversation_id, context_messages, new_messages
            )

        yield {"event": "done", "data": {}}

    async def _create_conversation(
        self,
        engine,
        conversation_id: str,
        user_id: str,
        title: str,
    ) -> None:
        """Create a new conversation row in the database."""
        now = datetime.now(UTC)
        async with engine.begin() as conn:
            await conn.execute(
                chat_conversations.insert().values(
                    id=conversation_id,
                    user_id=user_id,
                    title=title,
                    messages=json.dumps([]),
                    created_at=now,
                    updated_at=now,
                )
            )

    async def _load_conversation_messages(
        self,
        engine,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        """Load messages from an existing conversation."""
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(chat_conversations.c.messages).where(
                    chat_conversations.c.id == conversation_id,
                )
            )
            row = result.first()

        if not row:
            return []

        messages = row.messages
        if isinstance(messages, str):
            messages = json.loads(messages)
        return messages if isinstance(messages, list) else []

    async def _persist_messages(
        self,
        engine,
        conversation_id: str,
        context_messages: list[dict[str, Any]],
        response_messages: list[dict[str, Any]],
    ) -> None:
        """Persist messages to both JSON blob (backward compat) and normalized table."""
        all_messages = context_messages + response_messages

        serializable_messages = [self._serialize_message(m) for m in all_messages]

        now = datetime.now(UTC)
        async with engine.begin() as conn:
            await conn.execute(
                chat_conversations.update()
                .where(chat_conversations.c.id == conversation_id)
                .values(
                    messages=json.dumps(serializable_messages),
                    updated_at=now,
                )
            )

            for msg in response_messages:
                serialized = self._serialize_message(msg)
                tool_data = None
                if serialized.get("tool_use"):
                    tool_data = json.dumps(serialized["tool_use"])
                elif serialized.get("tool_result"):
                    tool_data = json.dumps(serialized["tool_result"])

                try:
                    await conn.execute(
                        chat_messages.insert().values(
                            conversation_id=conversation_id,
                            role=serialized.get("role", "assistant"),
                            content=serialized.get("content", ""),
                            tool_calls=tool_data,
                            created_at=now,
                        )
                    )
                except Exception:
                    logger.warning(
                        "Failed to write to chat_messages for conversation %s",
                        conversation_id,
                    )

    def _serialize_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Serialize a message dict for JSON storage."""
        content = msg.get("content", "")
        role = msg.get("role", "user")

        if isinstance(content, str):
            return {"role": role, "content": content}

        if isinstance(content, list):
            text_parts = []
            tool_use = None
            tool_result = None
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_use = {
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "input": block.get("input"),
                        }
                    elif block.get("type") == "tool_result":
                        tool_result = {
                            "tool_use_id": block.get("tool_use_id"),
                            "content": block.get("content"),
                        }

            result: dict[str, Any] = {
                "role": role,
                "content": " ".join(text_parts) if text_parts else "",
            }
            if tool_use:
                result["tool_use"] = tool_use
            if tool_result:
                result["tool_result"] = tool_result
            return result

        return {"role": role, "content": str(content)}

    def _to_anthropic_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert stored messages to Anthropic-style blocks (the OpenAI
        provider internally flattens to OpenAI format). Retained because
        tool_use/tool_result blocks round-trip cleanly through this shape.
        """
        anthropic_messages: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            tool_use = msg.get("tool_use")
            tool_result = msg.get("tool_result")

            if tool_use:
                content_blocks: list[dict[str, Any]] = []
                if content:
                    content_blocks.append({"type": "text", "text": content})
                content_blocks.append({
                    "type": "tool_use",
                    "id": tool_use["id"],
                    "name": tool_use["name"],
                    "input": tool_use.get("input", {}),
                })
                anthropic_messages.append({"role": "assistant", "content": content_blocks})
            elif tool_result:
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_result["tool_use_id"],
                        "content": tool_result.get("content", ""),
                    }],
                })
            elif isinstance(content, list):
                anthropic_messages.append({"role": role, "content": content})
            else:
                anthropic_messages.append({"role": role, "content": content})

        return anthropic_messages
