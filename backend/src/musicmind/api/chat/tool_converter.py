"""Tool definition format converter between Anthropic and OpenAI.

Converts Anthropic-format tool definitions (name, description, input_schema)
to OpenAI function-calling format (type, function: {name, description, parameters}).
"""

from __future__ import annotations

from typing import Any


def to_openai_functions(
    anthropic_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Anthropic tool definitions to OpenAI function-calling format.

    Anthropic format:
        {"name": ..., "description": ..., "input_schema": {...}}

    OpenAI format:
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}

    The input_schema maps directly to parameters since both use JSON Schema.

    Args:
        anthropic_tools: List of Anthropic-format tool definition dicts.

    Returns:
        List of OpenAI function-calling format tool dicts.
    """
    openai_tools: list[dict[str, Any]] = []
    for tool in anthropic_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return openai_tools
