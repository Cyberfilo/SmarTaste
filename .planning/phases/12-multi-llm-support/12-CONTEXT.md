# Phase 12: Multi-LLM Support - Context

**Gathered:** 2026-03-28
**Status:** Ready for planning
**Source:** Auto-mode

<domain>
## Phase Boundary

This phase adds OpenAI GPT-4o/GPT-4.1 as an alternative AI backend alongside Claude. Users can store an OpenAI API key, switch between models in settings or per-conversation, and chat with either model using the same tool registry. A strong, music-domain-specific system prompt powers both.

</domain>

<decisions>
## Implementation Decisions

### BYOK Extension for OpenAI
- **D-01:** Extend user_api_keys table to support service="openai" alongside service="anthropic". The composite PK (user_id, service) already supports this.
- **D-02:** New endpoints: POST /api/openai/key, GET /api/openai/key/status, POST /api/openai/key/validate, DELETE /api/openai/key. Follow identical pattern to Claude BYOK.
- **D-03:** OpenAI key validation: call OpenAI API with a minimal request (model="gpt-4o", max_tokens=1).

### Model Selection
- **D-04:** Add a `preferred_model` field to user settings (stored in users table or a user_preferences table). Values: "claude" (default), "openai".
- **D-05:** Chat endpoint accepts optional `model` query param to override per-conversation. If not specified, uses user's preferred_model.
- **D-06:** Frontend settings page gets a model selector (radio or dropdown): Claude Sonnet 4 / GPT-4o / GPT-4.1.

### Chat Service Abstraction
- **D-07:** Create an abstract LLMProvider interface with two implementations: ClaudeProvider and OpenAIProvider.
- **D-08:** ClaudeProvider wraps existing ChatService logic (Anthropic SDK, tool_use, SSE).
- **D-09:** OpenAIProvider uses the OpenAI Python SDK with function_calling (the OpenAI equivalent of tool_use). Same tool definitions converted to OpenAI function schema format.
- **D-10:** Both providers yield the same SSE event format (text, tool_start, tool_end, error, done) so the frontend doesn't need to change.

### System Prompt
- **D-11:** Create a shared system prompt module used by both providers. The prompt includes:
  - MusicMind app description and capabilities
  - User's connected services and taste summary
  - Available tools and their descriptions
  - Tone guidance: knowledgeable music expert, conversational, uses specific track/artist references
  - Instruction to explain recommendations using scoring dimensions
- **D-12:** System prompt is identical for both Claude and OpenAI (with minor format adaptations for each API's system message format).

### Tool Registry Conversion
- **D-13:** Existing TOOL_DEFINITIONS (Anthropic format) need an OpenAI-compatible converter. Anthropic uses `input_schema` while OpenAI uses `parameters` in function definitions.
- **D-14:** Tool executors (TOOL_EXECUTORS) remain identical — only the definition format and API call/response parsing differ between providers.

### Dependencies
- **D-15:** Add `openai>=1.60` to pyproject.toml.

### Claude's Discretion
- Whether to add Alembic migration for user preferences or use the existing user_api_keys table
- Exact model IDs (gpt-4o-2024-11-20, gpt-4.1-2025-04-14, etc.)
- OpenAI streaming implementation details (AsyncOpenAI with stream=True)
- Frontend model selector component design
- Cost estimate differences between models

</decisions>

<canonical_refs>
## Canonical References

### Phase 9 Outputs (to extend)
- `backend/src/musicmind/api/chat/service.py` — ChatService to refactor into provider pattern
- `backend/src/musicmind/api/chat/tools.py` — TOOL_DEFINITIONS + TOOL_EXECUTORS to share
- `backend/src/musicmind/api/chat/router.py` — Chat router to add model param
- `backend/src/musicmind/api/chat/schemas.py` — Chat schemas to extend

### Phase 4 Outputs (BYOK pattern)
- `backend/src/musicmind/api/claude/` — BYOK pattern to duplicate for OpenAI

### Frontend
- `frontend/src/components/settings/byok-key-manager.tsx` — Extend for OpenAI key
- `frontend/src/components/chat/chat-interface.tsx` — Add model selector
- `frontend/src/hooks/use-chat.ts` — Pass model param

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- ChatService agentic loop — refactor into provider interface
- TOOL_DEFINITIONS — convert format for OpenAI
- TOOL_EXECUTORS — share directly between providers
- BYOK key management — duplicate pattern for OpenAI

### Established Patterns
- SSE streaming (both Anthropic and OpenAI support it)
- user_api_keys table with composite PK (user_id, service) — already supports multiple services

</code_context>

<specifics>
## Specific Ideas

The system prompt should be something like:
"You are MusicMind, an AI music discovery assistant. You have access to the user's real listening data from Spotify and Apple Music. You can analyze their taste profile, find new music they'll love, explain why songs match their preferences, and adjust recommendations based on natural language. Be specific — reference actual tracks, artists, genres, and scoring dimensions. Be conversational, not clinical."

</specifics>

<deferred>
## Deferred Ideas

- Support for more models (Gemini, Llama) → v2
- Per-conversation model history (tracking which model said what) → v2

</deferred>

---

*Phase: 12-multi-llm-support*
*Context gathered: 2026-03-28 via auto-mode*
