"""ChatService: dispatches to LLM providers with MusicMind tools.

Sends user messages to the selected LLM provider (Claude or OpenAI) with
MusicMind tool definitions, streams responses as SSE events, manages
context windows, and persists conversations to the database.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.api.chat.providers.base import LLMProvider
from musicmind.api.chat.providers.claude import ClaudeProvider
from musicmind.api.chat.providers.openai import OpenAIProvider
from musicmind.api.chat.system_prompt import build_system_prompt
from musicmind.api.chat.tools import TOOL_DEFINITIONS, TOOL_EXECUTORS
from musicmind.db.schema import chat_conversations

logger = logging.getLogger(__name__)


class ChatService:
    """Orchestrates LLM provider <-> MusicMind tool bridge with streaming output.

    Dispatches to ClaudeProvider or OpenAIProvider based on the model parameter,
    manages conversation state, and yields SSE events.
    """

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
        model: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Send a message and yield SSE events as the response streams.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for key decryption.
            settings: Application settings.
            user_id: Current user's ID.
            conversation_id: Existing conversation ID or None for new.
            message: User message text.
            model: LLM provider: 'claude' or 'openai'. Defaults to 'claude'.

        Yields:
            Dict with "event" and "data" keys representing SSE events:
            - conversation_id: {id} -- first event for new conversations
            - text: {text} -- streamed text content
            - tool_start: {tool, input} -- tool invocation started
            - tool_end: {tool, result} -- tool invocation completed
            - error: {message} -- API or execution error
            - done: {} -- response complete
        """
        # 1. Determine provider
        provider_name = model or "claude"
        provider: LLMProvider

        if provider_name == "openai":
            from musicmind.api.openai.service import (
                get_decrypted_api_key as get_openai_key,
            )
            api_key = await get_openai_key(engine, encryption, user_id=user_id)
            if api_key is None:
                yield {
                    "event": "error",
                    "data": {
                        "message": (
                            "No OpenAI API key configured. "
                            "Please add your OpenAI API key in settings."
                        )
                    },
                }
                yield {"event": "done", "data": {}}
                return
            provider = OpenAIProvider()
        else:
            from musicmind.api.claude.service import (
                get_decrypted_api_key as get_claude_key,
            )
            api_key = await get_claude_key(engine, encryption, user_id=user_id)
            if api_key is None:
                yield {
                    "event": "error",
                    "data": {
                        "message": (
                            "No API key configured. "
                            "Please add your Anthropic API key in settings."
                        )
                    },
                }
                yield {"event": "done", "data": {}}
                return
            provider = ClaudeProvider()

        # 2. Conversation load/create
        conversation_messages: list[dict[str, Any]] = []
        if conversation_id is None:
            conversation_id = str(uuid.uuid7())
            title = message[:50]
            await self._create_conversation(engine, conversation_id, user_id, title)
            yield {"event": "conversation_id", "data": {"id": conversation_id}}
        else:
            conversation_messages = await self._load_conversation_messages(
                engine, conversation_id
            )

        # 3. Context window management
        context_messages = conversation_messages[-self.CONTEXT_WINDOW_MESSAGES :]
        context_messages.append({"role": "user", "content": message})

        # 4. Build system prompt
        system_prompt = await build_system_prompt(engine, user_id)

        # 5. Convert to provider message format
        provider_messages = self._to_anthropic_messages(context_messages)

        # 6. Stream from provider
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

        # 7. Conversation persistence (skip if errors occurred)
        if not has_error:
            # The provider mutated provider_messages in-place with assistant/tool msgs
            # Extract the new messages added by the provider
            new_messages = provider_messages[len(context_messages):]
            await self._persist_messages(
                engine, conversation_id, context_messages, new_messages
            )

        # 8. Yield "done" event
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
        """Persist the full message history to the database."""
        all_messages = context_messages + response_messages

        serializable_messages = []
        for msg in all_messages:
            serializable_messages.append(self._serialize_message(msg))

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

    def _serialize_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Serialize a message dict for JSON storage.

        Handles both simple string content and complex content block arrays
        from the Anthropic API format.
        """
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
        """Convert stored messages to Anthropic API message format.

        Handles both simple string content and stored messages with
        tool_use/tool_result blocks.
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
