"""OpenAI GPT LLM provider implementation.

Wraps the OpenAI chat.completions.create() streaming API with tool-call support,
yielding SSE event dicts in the unified format identical to ClaudeProvider.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import openai

from musicmind.api.chat.providers.base import MAX_TOOL_CALLS, LLMProvider
from musicmind.api.chat.tool_converter import to_openai_functions

logger = logging.getLogger(__name__)

MODEL = "gpt-4o"


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider with streaming and function-calling support.

    Uses chat.completions.create(stream=True) with tool calls and yields
    SSE events: text, tool_start, tool_end, error, done.
    """

    async def stream_response(
        self,
        *,
        api_key: str,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        tool_executors: dict[str, Any],
        engine: Any,
        encryption: Any,
        settings: Any,
        user_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream response from OpenAI API with tool-call loop.

        Args:
            api_key: Decrypted OpenAI API key.
            messages: Conversation messages (will be prefixed with system message).
            system_prompt: System prompt text.
            tools: Tool definitions in Anthropic format (converted internally).
            tool_executors: Mapping of tool name to async executor function.
            engine: SQLAlchemy async engine.
            encryption: EncryptionService.
            settings: Application settings.
            user_id: Current user's ID.

        Yields:
            SSE event dicts: text, tool_start, tool_end, error, done.
        """
        client = openai.AsyncOpenAI(api_key=api_key)
        openai_tools = to_openai_functions(tools)
        tool_call_count = 0

        # Prepend system message
        api_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        # Convert Anthropic-style messages to OpenAI format
        for msg in messages:
            api_messages.append(self._to_openai_message(msg))

        try:
            while True:
                stream = await client.chat.completions.create(
                    model=MODEL,
                    max_tokens=4096,
                    messages=api_messages,
                    tools=openai_tools if openai_tools else openai.NOT_GIVEN,
                    stream=True,
                )

                collected_text = ""
                tool_calls_acc: dict[int, dict[str, Any]] = {}
                finish_reason: str | None = None

                async for chunk in stream:
                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]
                    delta = choice.delta

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                    # Text content
                    if delta and delta.content:
                        yield {
                            "event": "text",
                            "data": {"text": delta.content},
                        }
                        collected_text += delta.content

                    # Tool calls (accumulated across chunks)
                    if delta and delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc.id or "",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.id:
                                tool_calls_acc[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_acc[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_acc[idx]["arguments"] += (
                                        tc.function.arguments
                                    )

                # Build assistant message for context
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": collected_text or None,
                }
                if tool_calls_acc:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in tool_calls_acc.values()
                    ]
                api_messages.append(assistant_msg)

                # If no tool calls, we are done
                if finish_reason != "tool_calls" or not tool_calls_acc:
                    break

                # Execute tool calls
                for tc in tool_calls_acc.values():
                    if tool_call_count >= MAX_TOOL_CALLS:
                        break

                    tool_name = tc["name"]
                    try:
                        tool_input = json.loads(tc["arguments"])
                    except (json.JSONDecodeError, ValueError):
                        tool_input = {}

                    yield {
                        "event": "tool_start",
                        "data": {"tool": tool_name, "input": tool_input},
                    }

                    result = await self._execute_tool(
                        tool_name, engine, encryption, settings, user_id, tool_input
                    )

                    yield {
                        "event": "tool_end",
                        "data": {"tool": tool_name, "result": result},
                    }

                    # Append tool result message for next iteration
                    api_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": (
                            json.dumps(result) if isinstance(result, dict)
                            else str(result)
                        ),
                    })
                    tool_call_count += 1

                if tool_call_count >= MAX_TOOL_CALLS:
                    break

        except openai.AuthenticationError:
            yield {
                "event": "error",
                "data": {
                    "message": (
                        "OpenAI API key invalid. "
                        "Please update your key in settings."
                    )
                },
            }
        except openai.RateLimitError:
            yield {
                "event": "error",
                "data": {
                    "message": (
                        "OpenAI rate limit reached. "
                        "Please wait a moment and try again."
                    )
                },
            }
        except openai.APIStatusError as e:
            yield {
                "event": "error",
                "data": {"message": f"OpenAI API error: {e}"},
            }

    @staticmethod
    def _to_openai_message(msg: dict[str, Any]) -> dict[str, Any]:
        """Convert an Anthropic-style stored message to OpenAI format.

        Handles simple string content, tool_use blocks, and tool_result blocks.

        Args:
            msg: Message dict from conversation storage.

        Returns:
            OpenAI-compatible message dict.
        """
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Simple string content
        if isinstance(content, str):
            return {"role": role, "content": content}

        # List of content blocks (Anthropic format)
        if isinstance(content, list):
            text_parts = []
            tool_use_blocks = []
            tool_result_blocks = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    tool_use_blocks.append(block)
                elif block_type == "tool_result":
                    tool_result_blocks.append(block)

            # Assistant message with tool use
            if tool_use_blocks and role == "assistant":
                result: dict[str, Any] = {
                    "role": "assistant",
                    "content": " ".join(text_parts) if text_parts else None,
                    "tool_calls": [
                        {
                            "id": tb.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tb.get("name", ""),
                                "arguments": json.dumps(tb.get("input", {})),
                            },
                        }
                        for tb in tool_use_blocks
                    ],
                }
                return result

            # Tool result messages
            if tool_result_blocks:
                # OpenAI expects one message per tool result
                # Return the first one; caller should handle multiples if needed
                tr = tool_result_blocks[0]
                return {
                    "role": "tool",
                    "tool_call_id": tr.get("tool_use_id", ""),
                    "content": tr.get("content", ""),
                }

            return {"role": role, "content": " ".join(text_parts)}

        return {"role": role, "content": str(content)}
