"""Tests for Anthropic-to-OpenAI tool definition converter (12-01).

Verifies that to_openai_functions correctly maps Anthropic tool schemas
to OpenAI function-calling format for all 8 MusicMind tool definitions.
"""

from __future__ import annotations

import os

os.environ.setdefault("MUSICMIND_FERNET_KEY", "dGVzdC1mZXJuZXQta2V5LXRoYXQtaXMtMzItYnl0ZXM=")
os.environ.setdefault("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("MUSICMIND_DEBUG", "true")

from musicmind.api.chat.tool_converter import to_openai_functions  # noqa: E402
from musicmind.api.chat.tools import TOOL_DEFINITIONS  # noqa: E402


class TestToOpenAiFunctions:
    """Tests for the to_openai_functions converter."""

    def test_converts_all_tool_definitions(self) -> None:
        """All 8 TOOL_DEFINITIONS are converted to OpenAI format."""
        result = to_openai_functions(TOOL_DEFINITIONS)
        assert len(result) == len(TOOL_DEFINITIONS)

    def test_output_has_type_function(self) -> None:
        """Each converted tool has type='function'."""
        result = to_openai_functions(TOOL_DEFINITIONS)
        for tool in result:
            assert tool["type"] == "function"

    def test_name_preserved(self) -> None:
        """Tool names are preserved in conversion."""
        result = to_openai_functions(TOOL_DEFINITIONS)
        original_names = {t["name"] for t in TOOL_DEFINITIONS}
        converted_names = {t["function"]["name"] for t in result}
        assert original_names == converted_names

    def test_description_preserved(self) -> None:
        """Tool descriptions are preserved in conversion."""
        result = to_openai_functions(TOOL_DEFINITIONS)
        for original, converted in zip(TOOL_DEFINITIONS, result):
            assert converted["function"]["description"] == original["description"]

    def test_input_schema_maps_to_parameters(self) -> None:
        """input_schema maps directly to parameters (both are JSON Schema)."""
        result = to_openai_functions(TOOL_DEFINITIONS)
        for original, converted in zip(TOOL_DEFINITIONS, result):
            assert converted["function"]["parameters"] == original["input_schema"]

    def test_required_fields_carried_over(self) -> None:
        """Required fields from input_schema are in parameters."""
        # give_feedback has required fields
        feedback_tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "give_feedback")
        result = to_openai_functions([feedback_tool])
        params = result[0]["function"]["parameters"]
        assert "required" in params
        assert "catalog_id" in params["required"]
        assert "feedback_type" in params["required"]

    def test_empty_input_schema(self) -> None:
        """Empty input_schema produces valid output."""
        tools = [{"name": "test_tool", "description": "A test", "input_schema": {}}]
        result = to_openai_functions(tools)
        assert len(result) == 1
        assert result[0]["function"]["parameters"] == {}

    def test_missing_input_schema_gets_default(self) -> None:
        """Missing input_schema gets a default empty object schema."""
        tools = [{"name": "test_tool", "description": "A test"}]
        result = to_openai_functions(tools)
        assert len(result) == 1
        assert result[0]["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }

    def test_function_structure(self) -> None:
        """Each converted tool has the correct nested structure."""
        result = to_openai_functions(TOOL_DEFINITIONS[:1])
        tool = result[0]
        assert "type" in tool
        assert "function" in tool
        func = tool["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func
