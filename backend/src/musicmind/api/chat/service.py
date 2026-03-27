"""ChatService: agentic loop orchestrating Claude API with MusicMind tools.

Sends user messages to Anthropic's API with MusicMind tool definitions, executes
tool calls against existing services, streams responses as SSE events, manages
context windows, and persists conversations to the database.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import uuid

import anthropic
import sqlalchemy as sa

from musicmind.api.chat.tools import TOOL_DEFINITIONS, TOOL_EXECUTORS
from musicmind.api.claude.service import get_decrypted_api_key
from musicmind.db.schema import chat_conversations, service_connections, taste_profile_snapshots

logger = logging.getLogger(__name__)


class ChatService:
    """Orchestrates the Claude API <-> MusicMind tool bridge with streaming output.

    Implements the full agentic loop: send message to Claude, handle tool_use
    responses by executing MusicMind tools, feed results back, and iterate
    until Claude returns a final text response or hits the tool-call cap.
    """

    MAX_TOOL_CALLS = 10  # D-17: hard cap on tool calls per message
    CONTEXT_WINDOW_MESSAGES = 20  # D-09: max messages to include in context
    MODEL = "claude-sonnet-4-20250514"

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
        """Send a message and yield SSE events as the response streams.

        Args:
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for key decryption.
            settings: Application settings.
            user_id: Current user's ID.
            conversation_id: Existing conversation ID or None for new.
            message: User message text.

        Yields:
            Dict with "event" and "data" keys representing SSE events:
            - conversation_id: {id} -- first event for new conversations
            - text: {text} -- streamed text content
            - tool_start: {tool, input} -- tool invocation started
            - tool_end: {tool, result} -- tool invocation completed
            - error: {message} -- API or execution error
            - done: {} -- response complete
        """
        # 1. BYOK key retrieval
        api_key = await get_decrypted_api_key(engine, encryption, user_id=user_id)
        if api_key is None:
            yield {"event": "error", "data": {"message": "No API key configured. Please add your Anthropic API key in settings."}}
            yield {"event": "done", "data": {}}
            return

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

        # 3. Context window management (D-09)
        context_messages = conversation_messages[-self.CONTEXT_WINDOW_MESSAGES :]
        context_messages.append({"role": "user", "content": message})

        # 4. Build system prompt
        system_prompt = await self._build_system_prompt(engine, user_id)

        # 5. Convert to Anthropic message format
        anthropic_messages = self._to_anthropic_messages(context_messages)

        # 6. Per-request AsyncAnthropic client (D-03)
        client = anthropic.AsyncAnthropic(api_key=api_key)

        # 7. Agentic loop (D-01)
        tool_call_count = 0
        all_response_messages: list[dict[str, Any]] = []

        try:
            while True:
                # Call Claude with streaming
                async with client.messages.stream(
                    model=self.MODEL,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=anthropic_messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice={
                        "type": "auto",
                        "disable_parallel_tool_use": True,  # D-06: sequential tools
                    },
                ) as stream:
                    # Collect content blocks and text from the stream
                    collected_text = ""
                    tool_use_blocks: list[dict[str, Any]] = []
                    current_tool_block: dict[str, Any] | None = None

                    async for event in stream:
                        if event.type == "content_block_start":
                            if event.content_block.type == "tool_use":
                                current_tool_block = {
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input": {},
                                    "partial_json": "",
                                }
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                yield {
                                    "event": "text",
                                    "data": {"text": event.delta.text},
                                }
                                collected_text += event.delta.text
                            elif event.delta.type == "input_json_delta":
                                if current_tool_block is not None:
                                    current_tool_block["partial_json"] += (
                                        event.delta.partial_json
                                    )
                        elif event.type == "content_block_stop":
                            if current_tool_block is not None:
                                # Parse accumulated JSON
                                try:
                                    current_tool_block["input"] = json.loads(
                                        current_tool_block["partial_json"]
                                    )
                                except (json.JSONDecodeError, ValueError):
                                    pass
                                del current_tool_block["partial_json"]
                                tool_use_blocks.append(current_tool_block)
                                current_tool_block = None

                    # Get the final message for stop_reason
                    final_message = await stream.get_final_message()

                stop_reason = final_message.stop_reason

                # Build the assistant message for context
                assistant_content: list[dict[str, Any]] = []
                if collected_text:
                    assistant_content.append({"type": "text", "text": collected_text})
                for tb in tool_use_blocks:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tb["id"],
                        "name": tb["name"],
                        "input": tb["input"],
                    })

                # Record the assistant message
                assistant_msg = {"role": "assistant", "content": assistant_content}
                all_response_messages.append(assistant_msg)
                anthropic_messages.append(assistant_msg)

                # If no tool_use, we are done
                if stop_reason != "tool_use" or not tool_use_blocks:
                    break

                # Execute tools
                tool_results_content: list[dict[str, Any]] = []
                for tb in tool_use_blocks:
                    if tool_call_count >= self.MAX_TOOL_CALLS:
                        break

                    tool_name = tb["name"]
                    tool_input = tb["input"]
                    tool_id = tb["id"]

                    # Yield tool_start (D-11)
                    yield {
                        "event": "tool_start",
                        "data": {"tool": tool_name, "input": tool_input},
                    }

                    # Execute tool
                    result = await self._execute_tool(
                        tool_name, engine, encryption, settings, user_id, tool_input
                    )

                    # Yield tool_end (D-11)
                    yield {
                        "event": "tool_end",
                        "data": {"tool": tool_name, "result": result},
                    }

                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(result) if isinstance(result, dict) else str(result),
                    })
                    tool_call_count += 1

                # Append tool results to messages for the next iteration
                tool_result_msg = {"role": "user", "content": tool_results_content}
                all_response_messages.append(tool_result_msg)
                anthropic_messages.append(tool_result_msg)

                # Check cap
                if tool_call_count >= self.MAX_TOOL_CALLS:
                    break

        except anthropic.AuthenticationError:
            yield {
                "event": "error",
                "data": {
                    "message": "API key expired or invalid. Please update your key in settings."
                },
            }
            yield {"event": "done", "data": {}}
            return
        except anthropic.RateLimitError:
            yield {
                "event": "error",
                "data": {
                    "message": "Rate limit reached. Please wait a moment and try again."
                },
            }
            yield {"event": "done", "data": {}}
            return
        except anthropic.APIStatusError as e:
            status = getattr(e.response, "status_code", None)
            if status == 402 or "balance" in str(e).lower():
                yield {
                    "event": "error",
                    "data": {
                        "message": "Insufficient API balance. Please check your Anthropic account."
                    },
                }
            else:
                yield {
                    "event": "error",
                    "data": {"message": f"API error: {e}"},
                }
            yield {"event": "done", "data": {}}
            return
        except anthropic.APIError as e:
            yield {
                "event": "error",
                "data": {"message": f"API error: {e}"},
            }
            yield {"event": "done", "data": {}}
            return

        # 9. Conversation persistence
        await self._persist_messages(
            engine, conversation_id, context_messages, all_response_messages
        )

        # 10. Yield "done" event
        yield {"event": "done", "data": {}}

    async def _execute_tool(
        self,
        tool_name: str,
        engine,
        encryption,
        settings,
        user_id: str,
        tool_input: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a MusicMind tool via the TOOL_EXECUTORS mapping.

        Args:
            tool_name: Name of the tool to execute.
            engine: SQLAlchemy async engine.
            encryption: EncryptionService.
            settings: Application settings.
            user_id: Current user's ID.
            tool_input: Tool input parameters from Claude.

        Returns:
            Tool execution result as a dict.
        """
        executor = TOOL_EXECUTORS.get(tool_name)
        if not executor:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return await executor(
                engine=engine,
                encryption=encryption,
                settings=settings,
                user_id=user_id,
                **tool_input,
            )
        except Exception as e:
            logger.warning("Tool execution error for %s: %s", tool_name, e)
            return {"error": str(e)}

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
        # Combine existing context messages with new response messages
        all_messages = context_messages + response_messages

        # Serialize complex content blocks for storage
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
            # Complex content blocks (tool_use, tool_result, text)
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
                # Assistant message with tool use
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
                # User message with tool result
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_result["tool_use_id"],
                        "content": tool_result.get("content", ""),
                    }],
                })
            elif isinstance(content, list):
                # Already in block format
                anthropic_messages.append({"role": role, "content": content})
            else:
                anthropic_messages.append({"role": role, "content": content})

        return anthropic_messages

    async def _build_system_prompt(self, engine, user_id: str) -> str:
        """Build the system prompt with user context.

        Includes:
        - MusicMind identity
        - User's connected services
        - Brief taste summary (top genres if available)
        - Available tool descriptions
        """
        parts = [
            "You are MusicMind, a music discovery assistant. You have access to the "
            "user's music library and taste profile across their connected services. "
            "Help them discover new music, understand their taste, and get personalized "
            "recommendations.",
        ]

        # Connected services
        services = await self._get_connected_services(engine, user_id)
        if services:
            svc_names = ", ".join(s.replace("_", " ").title() for s in services)
            parts.append(f"\nConnected services: {svc_names}.")
        else:
            parts.append("\nNo music services connected yet.")

        # Taste summary
        taste_summary = await self._get_taste_summary(engine, user_id)
        if taste_summary:
            parts.append(f"\n{taste_summary}")

        # Available tools
        tool_names = [t["name"] for t in TOOL_DEFINITIONS]
        parts.append(
            f"\nAvailable tools: {', '.join(tool_names)}. "
            "Use these tools to access the user's music data and provide recommendations."
        )

        return "\n".join(parts)

    async def _get_connected_services(self, engine, user_id: str) -> list[str]:
        """Query connected services for the user."""
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(service_connections.c.service).where(
                    service_connections.c.user_id == user_id,
                )
            )
            return [row.service for row in result.fetchall()]

    async def _get_taste_summary(self, engine, user_id: str) -> str | None:
        """Get a brief taste summary from the latest taste profile snapshot."""
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    taste_profile_snapshots.c.genre_vector,
                    taste_profile_snapshots.c.top_artists,
                )
                .where(taste_profile_snapshots.c.user_id == user_id)
                .order_by(taste_profile_snapshots.c.computed_at.desc())
                .limit(1)
            )
            row = result.first()

        if not row:
            return None

        genre_vector = row.genre_vector
        if isinstance(genre_vector, str):
            genre_vector = json.loads(genre_vector)

        if not genre_vector:
            return None

        # Get top 3 genres
        sorted_genres = sorted(genre_vector.items(), key=lambda x: x[1], reverse=True)[:3]
        top_genres = [g[0] for g in sorted_genres]

        top_artists = row.top_artists
        if isinstance(top_artists, str):
            top_artists = json.loads(top_artists)

        parts = [f"User's top genres: {', '.join(top_genres)}."]
        if top_artists:
            artist_names = top_artists[:3] if isinstance(top_artists, list) else []
            if artist_names:
                parts.append(f"Top artists: {', '.join(artist_names)}.")

        return " ".join(parts)
