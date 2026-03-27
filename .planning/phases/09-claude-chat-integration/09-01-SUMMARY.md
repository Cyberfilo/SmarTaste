---
phase: 09-claude-chat-integration
plan: 01
subsystem: chat
tags: [claude, anthropic, tool-use, chat, database, pydantic]

requires:
  - phase: 04-byok-claude-api-key-management
    provides: BYOK key storage and decryption
  - phase: 05-taste-profile-dashboard
    provides: TasteService for taste profile queries
  - phase: 06-listening-stats-dashboard
    provides: StatsService for listening stats queries
  - phase: 07-recommendation-feed
    provides: RecommendationService for recommendations and feedback
provides:
  - chat_conversations database table with Alembic migration
  - Pydantic request/response schemas for chat endpoints
  - 8 curated Claude tool definitions with executor mapping
affects: [09-02-chat-service, 09-03-chat-router, 11-ui-design-frontend-shell]

tech-stack:
  added: []
  patterns:
    - "Tool registry pattern: TOOL_DEFINITIONS list + TOOL_EXECUTORS dict for Claude tool_use integration"
    - "Description-to-mood inference for natural language tool inputs"

key-files:
  created:
    - backend/src/musicmind/api/chat/__init__.py
    - backend/src/musicmind/api/chat/schemas.py
    - backend/src/musicmind/api/chat/tools.py
    - backend/alembic/versions/004_add_chat_conversations.py
    - backend/tests/test_chat_schema.py
  modified:
    - backend/src/musicmind/db/schema.py
    - backend/tests/test_schema.py

key-decisions:
  - "8 curated tools (not all 30+ MCP tools) -- focused on chat-relevant operations"
  - "Description-based tools map to mood/strategy via keyword inference"
  - "adjust_taste_preferences uses genre_adjacent strategy for genre keywords, similar_artist for artist keywords"

patterns-established:
  - "Tool registry: TOOL_DEFINITIONS as Anthropic-compatible schemas, TOOL_EXECUTORS as async callables"
  - "Executor signature: async def execute_*(*, engine, encryption, settings, user_id, **kwargs) -> dict"

requirements-completed: [CHAT-01, CHAT-03, CHAT-05, CHAT-09]

duration: 5min
completed: 2026-03-27
---

# Phase 09 Plan 01: Chat Foundation Summary

**Chat conversation table, Pydantic schemas, and 8-tool Claude registry mapping to TasteService, RecommendationService, and StatsService**

## What Was Built

### Database Layer
- Added `chat_conversations` table to `schema.py` with id (uuid7), user_id (FK to users with CASCADE), title, messages (JSON array), created_at, updated_at
- Created Alembic migration 004 (depends on 003) with upgrade/downgrade

### Pydantic Models
- `SendMessageRequest`: conversation_id (optional), message (min_length=1)
- `MessageItem`: role, content, tool_use (optional dict), tool_result (optional dict)
- `ConversationResponse`: full conversation detail with messages list
- `ConversationListItem`: summary with message_count for listing
- `ConversationListResponse`: wrapper for conversation list

### Tool Registry
8 Claude tool definitions with Anthropic-compatible JSON schemas:
1. `get_taste_profile` -- TasteService.get_profile (optional service, force_refresh)
2. `get_recommendations` -- RecommendationService.get_recommendations (strategy, mood, limit)
3. `get_listening_stats_tracks` -- StatsService.get_top_tracks (period, limit)
4. `get_listening_stats_artists` -- StatsService.get_top_artists (period, limit)
5. `get_top_genres` -- StatsService.get_top_genres (period, limit)
6. `give_feedback` -- RecommendationService.submit_feedback (catalog_id, feedback_type required)
7. `get_recommendations_by_description` -- maps natural language to mood, calls get_recommendations
8. `adjust_taste_preferences` -- interprets adjustment text, selects strategy/mood, calls get_recommendations

### Tests
23 tests across 3 test classes:
- `TestChatConversationsTable` (7 tests): table existence, columns, PK, FK, index, insert
- `TestChatPydanticSchemas` (7 tests): validation, fields, defaults, tool_use handling
- `TestToolRegistry` (9 tests): definition count, keys, uniqueness, executor matching, required fields

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_schema.py table count 13->14**
- **Found during:** Task 2 verification (full test suite regression check)
- **Issue:** test_schema.py hardcoded `assert len(metadata.tables) == 13`; adding chat_conversations made it 14
- **Fix:** Updated count to 14, added `chat_conversations` to ALL_TABLE_NAMES and DATA_TABLE_NAMES lists
- **Files modified:** backend/tests/test_schema.py
- **Commit:** a94c3de

## Verification

- `chat_conversations` columns: ['id', 'user_id', 'title', 'messages', 'created_at', 'updated_at']
- TOOL_DEFINITIONS: 8, TOOL_EXECUTORS: 8
- All 245 tests passing (23 new + 222 existing, zero regressions)

## Known Stubs

None -- all code is fully functional with real service integrations.

## Self-Check: PASSED

All 7 files verified on disk. All 3 commits verified in git history.
