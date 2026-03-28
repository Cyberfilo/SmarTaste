---
phase: 12-multi-llm-support
plan: 01
subsystem: chat-providers
tags: [llm-abstraction, openai, byok, tool-converter, system-prompt]
dependency_graph:
  requires: [chat-service, claude-byok, db-schema]
  provides: [llm-provider-abstraction, openai-byok, tool-converter, shared-system-prompt]
  affects: [chat-router, chat-schemas]
tech_stack:
  added: [openai>=1.60]
  patterns: [provider-pattern, strategy-dispatch, format-converter]
key_files:
  created:
    - backend/src/musicmind/api/chat/providers/__init__.py
    - backend/src/musicmind/api/chat/providers/base.py
    - backend/src/musicmind/api/chat/providers/claude.py
    - backend/src/musicmind/api/chat/providers/openai.py
    - backend/src/musicmind/api/chat/system_prompt.py
    - backend/src/musicmind/api/chat/tool_converter.py
    - backend/src/musicmind/api/openai/__init__.py
    - backend/src/musicmind/api/openai/schemas.py
    - backend/src/musicmind/api/openai/service.py
    - backend/src/musicmind/api/openai/router.py
    - backend/tests/test_tool_converter.py
    - backend/tests/test_openai_byok.py
    - backend/tests/test_chat_providers.py
  modified:
    - backend/pyproject.toml
    - backend/src/musicmind/api/router.py
    - backend/src/musicmind/api/chat/service.py
    - backend/src/musicmind/api/chat/schemas.py
    - backend/src/musicmind/api/chat/router.py
    - backend/tests/test_chat_service.py
decisions:
  - "Provider pattern with ABC for LLM dispatch -- ClaudeProvider and OpenAIProvider yield identical SSE events"
  - "System prompt extracted to shared module with enhanced text per design context"
  - "Tool converter maps Anthropic input_schema directly to OpenAI parameters (both JSON Schema)"
  - "ChatService dispatches lazily via model param with 'claude' as default"
  - "OpenAI BYOK mirrors Claude BYOK pattern exactly (same DB table, different service value)"
metrics:
  duration: 11min
  completed: 2026-03-28
  tasks: 2
  files: 20
---

# Phase 12 Plan 01: Backend LLM Provider Abstraction Summary

LLM provider abstraction with ClaudeProvider and OpenAIProvider yielding identical SSE events, OpenAI BYOK endpoints, tool definition converter, and shared system prompt module.

## What Was Built

### Task 1: LLM Provider Interface, Tool Converter, System Prompt, OpenAI BYOK
**Commit:** 8b45a6e

Created the provider abstraction layer:
- **providers/base.py**: Abstract `LLMProvider` with `stream_response()` and shared `_execute_tool()`. `MAX_TOOL_CALLS=10` constant.
- **providers/claude.py**: `ClaudeProvider` wrapping the existing Anthropic agentic loop (messages.stream, tool_use handling, error mapping).
- **providers/openai.py**: `OpenAIProvider` using chat.completions.create(stream=True) with tool_calls accumulation across chunks, role="tool" result messages.
- **system_prompt.py**: Extracted `build_system_prompt()` with enhanced text ("Be specific -- reference actual tracks, artists, genres, and scoring dimensions").
- **tool_converter.py**: `to_openai_functions()` mapping Anthropic `input_schema` to OpenAI `parameters`.
- **openai/ BYOK module**: Full mirror of Claude BYOK (service.py, router.py, schemas.py) with service="openai", GPT-4o pricing, sk-... masking.
- **pyproject.toml**: Added `openai>=1.60` dependency.
- **api/router.py**: Wired `openai_router`.

### Task 2: ChatService Refactor + Tests
**Commit:** e91def3

Refactored ChatService to use provider dispatch:
- **service.py**: Removed inline Anthropic code. `send_message()` gains `model` param, determines provider (Claude/OpenAI), retrieves appropriate BYOK key, delegates to `provider.stream_response()`.
- **schemas.py**: Added `model: str | None` field to `SendMessageRequest`.
- **router.py**: Passes `body.model` to ChatService.
- **test_chat_service.py**: Updated mock paths from `service.anthropic` to `providers.claude.anthropic` and `service.TOOL_EXECUTORS` to `providers.base.TOOL_EXECUTORS`.
- **test_tool_converter.py**: 9 tests covering all 8 tool definitions, format structure, required fields, edge cases.
- **test_openai_byok.py**: 22 tests covering service functions (mask, store, status, delete, validate) and HTTP integration (7 endpoint tests).
- **test_chat_providers.py**: 5 tests for provider dispatch (default=claude, explicit claude, explicit openai, missing keys).

## Decisions Made

1. **Provider pattern with ABC**: `LLMProvider` abstract base with `stream_response()` async generator. Both providers yield identical event dicts (`text`, `tool_start`, `tool_end`, `error`). ChatService always appends `done`.
2. **Lazy imports for key retrieval**: ChatService imports `get_decrypted_api_key` from the appropriate service module inside the if/else branch, avoiding circular imports.
3. **Tool converter is one-way**: Only Anthropic-to-OpenAI conversion needed. TOOL_EXECUTORS mapping is format-agnostic.
4. **OpenAI BYOK mirrors Claude exactly**: Same composite PK table (user_id, "openai"), same endpoint structure, same validation pattern (max_tokens=1 probe).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated existing test mock paths**
- **Found during:** Task 2
- **Issue:** Existing test_chat_service.py mocked `musicmind.api.chat.service.anthropic.AsyncAnthropic` which no longer exists after refactoring anthropic imports to providers/claude.py
- **Fix:** Updated all mock paths to `musicmind.api.chat.providers.claude.anthropic.AsyncAnthropic` and `musicmind.api.chat.providers.base.TOOL_EXECUTORS`
- **Files modified:** backend/tests/test_chat_service.py
- **Commit:** e91def3

**2. [Rule 3 - Blocking] Updated MAX_TOOL_CALLS reference in tests**
- **Found during:** Task 2
- **Issue:** Test referenced `ChatService.MAX_TOOL_CALLS` which was moved to `providers.base.MAX_TOOL_CALLS`
- **Fix:** Changed test to import `MAX_TOOL_CALLS` from `providers.base`
- **Files modified:** backend/tests/test_chat_service.py
- **Commit:** e91def3

## Test Results

- **New tests:** 36 (9 tool converter + 22 OpenAI BYOK + 5 provider dispatch)
- **Existing tests updated:** 17 (test_chat_service.py mock path updates)
- **Total passing:** 291 (excluding 2 pre-existing failures in test_auth.py and test_services.py)
- **Pre-existing failures:** test_auth.py::test_csrf_protection (CSRF disabled in earlier phase), test_services.py::test_spotify_callback_stores_tokens (redirect behavior)

## Known Stubs

None. All provider implementations are fully wired with real API client creation and tool execution paths.

## Self-Check: PASSED

All 13 created files exist. Both task commits (8b45a6e, e91def3) verified in git log.
