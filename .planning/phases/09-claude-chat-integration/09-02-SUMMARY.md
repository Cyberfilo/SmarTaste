---
phase: 09-claude-chat-integration
plan: 02
subsystem: api
tags: [anthropic, claude, tool_use, sse, streaming, agentic-loop, chat]

# Dependency graph
requires:
  - phase: 09-01
    provides: "TOOL_DEFINITIONS, TOOL_EXECUTORS, chat schemas, chat_conversations table"
  - phase: 04-claude-byok
    provides: "get_decrypted_api_key for BYOK key retrieval"
  - phase: 05-taste-profile
    provides: "TasteService for get_taste_profile tool"
  - phase: 06-listening-stats
    provides: "StatsService for get_listening_stats/top_genres tools"
  - phase: 07-recommendations
    provides: "RecommendationService for get_recommendations tool"
provides:
  - "ChatService class with full agentic loop (send_message async generator)"
  - "SSE event streaming: text, tool_start, tool_end, error, done, conversation_id"
  - "Anthropic API error handling: 401/429/402 mapped to user-friendly messages"
  - "Conversation persistence: create, load, persist messages"
  - "Context window management: last 20 messages truncation"
  - "System prompt with user context (connected services, taste summary)"
affects: [09-03-chat-router, 11-ui-design]

# Tech tracking
tech-stack:
  added: []
  patterns: ["agentic loop with tool_use streaming", "per-request API client instantiation", "async generator SSE event protocol"]

key-files:
  created:
    - "backend/src/musicmind/api/chat/service.py"
  modified:
    - "backend/tests/test_chat_service.py"

key-decisions:
  - "Used messages.stream() with async context manager instead of messages.create(stream=True) for cleaner streaming API"
  - "disable_parallel_tool_use via tool_choice param (not top-level) per Anthropic SDK 0.86 API"
  - "uuid.uuid7() for conversation IDs (Python 3.14 native, matching existing auth pattern)"
  - "System prompt dynamically includes connected services and top 3 genres from latest taste snapshot"

patterns-established:
  - "SSE event protocol: {event, data} dict yielded from async generator"
  - "Agentic loop: stream -> collect content blocks -> check stop_reason -> execute tools -> continue"
  - "Message serialization: complex content blocks (tool_use/tool_result) stored in JSON with role, content, tool_use, tool_result fields"

requirements-completed: [CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05, CHAT-06, CHAT-07, CHAT-08, CHAT-10]

# Metrics
duration: 7min
completed: 2026-03-27
---

# Phase 9 Plan 2: ChatService Summary

**ChatService with full agentic loop: per-request AsyncAnthropic client, SSE streaming (text/tool_start/tool_end/error/done), 10-tool-call cap, context window management, conversation persistence, and Anthropic error mapping (401/429/402)**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-27T19:36:50Z
- **Completed:** 2026-03-27T19:44:11Z
- **Tasks:** 1 (TDD: RED -> GREEN)
- **Files modified:** 2

## Accomplishments
- ChatService.send_message() async generator yields SSE events for the full Claude <-> MusicMind tool bridge
- Agentic loop correctly handles text-only and multi-tool-use conversations with 10-call safety cap
- Anthropic API errors (401 auth, 429 rate limit, 402 balance) produce user-friendly error SSE events
- Conversation persistence: create new with auto-generated title, load existing, persist full message history after response
- Context window management truncates to last 20 messages for API budget control
- System prompt dynamically includes connected services and user's top 3 genres from taste profile

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: ChatService failing tests** - `4e6fccb` (test)
2. **Task 1 GREEN: ChatService implementation** - `c970228` (feat)

## Files Created/Modified
- `backend/src/musicmind/api/chat/service.py` - ChatService class with agentic loop, SSE streaming, conversation persistence, error handling (361 lines)
- `backend/tests/test_chat_service.py` - 17 tests covering text response, tool use, error handling, persistence, context window, system prompt (1044 lines)

## Decisions Made
- Used `messages.stream()` API with async context manager for cleaner streaming event handling vs `messages.create(stream=True)`
- `disable_parallel_tool_use` passed via `tool_choice={"type": "auto", "disable_parallel_tool_use": True}` parameter (Anthropic SDK 0.86.0 API)
- Used Python 3.14 native `uuid.uuid7()` for conversation IDs, matching existing auth service pattern
- System prompt dynamically queries `service_connections` and `taste_profile_snapshots` tables for user context
- Tool results serialized to JSON strings for the Anthropic API tool_result content field

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed uuid_utils import to use stdlib uuid**
- **Found during:** Task 1 (implementation)
- **Issue:** Initial implementation used `uuid_utils.uuid7()` but the package is not installed; existing codebase uses `uuid.uuid7()` (Python 3.14 native)
- **Fix:** Changed import to `import uuid` and call to `uuid.uuid7()`
- **Files modified:** backend/src/musicmind/api/chat/service.py
- **Verification:** All tests pass, import succeeds
- **Committed in:** c970228 (part of task commit)

**2. [Rule 1 - Bug] Fixed disable_parallel_tool_use parameter placement**
- **Found during:** Task 1 (acceptance criteria verification)
- **Issue:** Plan specified `disable_parallel_tool_use=True` as a top-level parameter, but Anthropic SDK 0.86.0 requires it inside `tool_choice` dict
- **Fix:** Moved to `tool_choice={"type": "auto", "disable_parallel_tool_use": True}`
- **Files modified:** backend/src/musicmind/api/chat/service.py
- **Verification:** All tests pass, grep confirms presence
- **Committed in:** c970228 (part of task commit)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered
- Mock list mutation: MagicMock captures list references (not copies), so the `anthropic_messages` list grows after the API call returns. Test assertion adjusted to verify truncation by checking first message content rather than list length.

## Known Stubs
None - all functionality is fully wired to existing services.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- ChatService ready for Plan 09-03 (chat router endpoints)
- send_message() async generator can be consumed by SSE endpoint
- All 262 tests pass, no regressions

---
*Phase: 09-claude-chat-integration*
*Completed: 2026-03-27*
