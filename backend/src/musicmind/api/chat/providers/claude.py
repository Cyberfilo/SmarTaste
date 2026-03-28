"""Claude (Anthropic) LLM provider implementation.

Wraps the Anthropic messages.stream() API with the agentic tool-use loop,
yielding SSE event dicts in the unified format.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import anthropic

from musicmind.api.chat.providers.base import MAX_TOOL_CALLS, LLMProvider
from musicmind.api.chat.tools import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"


class ClaudeProvider(LLMProvider):
    """Anthropic Claude provider with streaming and tool-use support.

    Uses messages.stream() with sequential tool execution and yields
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
        """Stream response from Claude API with agentic tool-use loop.

        Args:
            api_key: Decrypted Anthropic API key.
            messages: Conversation messages in Anthropic format.
            system_prompt: System prompt text.
            tools: Tool definitions (ignored; uses TOOL_DEFINITIONS directly).
            tool_executors: Mapping of tool name to async executor function.
            engine: SQLAlchemy async engine.
            encryption: EncryptionService.
            settings: Application settings.
            user_id: Current user's ID.

        Yields:
            SSE event dicts: text, tool_start, tool_end, error, done.
        """
        client = anthropic.AsyncAnthropic(api_key=api_key)
        tool_call_count = 0

        try:
            while True:
                async with client.messages.stream(
                    model=MODEL,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice={
                        "type": "auto",
                        "disable_parallel_tool_use": True,
                    },
                ) as stream:
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
                                try:
                                    current_tool_block["input"] = json.loads(
                                        current_tool_block["partial_json"]
                                    )
                                except (json.JSONDecodeError, ValueError):
                                    pass
                                del current_tool_block["partial_json"]
                                tool_use_blocks.append(current_tool_block)
                                current_tool_block = None

                    final_message = await stream.get_final_message()

                stop_reason = final_message.stop_reason

                # Build assistant message for context
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

                assistant_msg = {"role": "assistant", "content": assistant_content}
                messages.append(assistant_msg)

                if stop_reason != "tool_use" or not tool_use_blocks:
                    break

                # Execute tools
                tool_results_content: list[dict[str, Any]] = []
                for tb in tool_use_blocks:
                    if tool_call_count >= MAX_TOOL_CALLS:
                        break

                    tool_name = tb["name"]
                    tool_input = tb["input"]
                    tool_id = tb["id"]

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

                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": (
                            json.dumps(result) if isinstance(result, dict) else str(result)
                        ),
                    })
                    tool_call_count += 1

                tool_result_msg = {"role": "user", "content": tool_results_content}
                messages.append(tool_result_msg)

                if tool_call_count >= MAX_TOOL_CALLS:
                    break

        except anthropic.AuthenticationError:
            yield {
                "event": "error",
                "data": {
                    "message": (
                        "API key expired or invalid. "
                        "Please update your key in settings."
                    )
                },
            }
        except anthropic.RateLimitError:
            yield {
                "event": "error",
                "data": {
                    "message": (
                        "Rate limit reached. Please wait a moment and try again."
                    )
                },
            }
        except anthropic.APIStatusError as e:
            status = getattr(e.response, "status_code", None)
            if status == 402 or "balance" in str(e).lower():
                yield {
                    "event": "error",
                    "data": {
                        "message": (
                            "Insufficient API balance. "
                            "Please check your Anthropic account."
                        )
                    },
                }
            else:
                yield {
                    "event": "error",
                    "data": {"message": f"API error: {e}"},
                }
        except anthropic.APIError as e:
            yield {
                "event": "error",
                "data": {"message": f"API error: {e}"},
            }
