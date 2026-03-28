"""Abstract base class for LLM provider implementations.

Defines the LLMProvider interface that both ClaudeProvider and OpenAIProvider
implement, yielding identical SSE event dicts for the chat service.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from musicmind.api.chat.tools import TOOL_EXECUTORS

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 10


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    Each provider implements stream_response to yield SSE event dicts in
    a unified format regardless of the underlying API.
    """

    @abstractmethod
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
        """Stream a response from the LLM, yielding SSE event dicts.

        Args:
            api_key: Decrypted API key for the provider.
            messages: Conversation messages in provider-native format.
            system_prompt: System prompt text.
            tools: Tool definitions in provider-native format.
            tool_executors: Mapping of tool name to async executor function.
            engine: SQLAlchemy async engine.
            encryption: EncryptionService for key decryption.
            settings: Application settings.
            user_id: Current user's ID.

        Yields:
            Dict with "event" and "data" keys:
            - text: {text} -- streamed text content
            - tool_start: {tool, input} -- tool invocation started
            - tool_end: {tool, result} -- tool invocation completed
            - error: {message} -- API or execution error
            - done: {} -- response complete
        """
        yield {}  # pragma: no cover

    async def _execute_tool(
        self,
        tool_name: str,
        engine: Any,
        encryption: Any,
        settings: Any,
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
            tool_input: Tool input parameters from the LLM.

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
