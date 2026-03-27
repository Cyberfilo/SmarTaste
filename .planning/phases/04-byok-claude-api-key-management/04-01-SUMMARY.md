---
phase: 04-byok-claude-api-key-management
plan: 01
subsystem: api
tags: [anthropic, byok, encryption, fernet, pydantic, sqlalchemy]

# Dependency graph
requires:
  - phase: 01-infrastructure-foundation
    provides: EncryptionService (Fernet), Settings config, SQLAlchemy metadata
  - phase: 02-user-accounts
    provides: users table (FK target for user_api_keys)
provides:
  - user_api_keys table with composite PK (user_id, service)
  - 4 Pydantic request/response models for BYOK endpoints
  - 7 service functions (store, retrieve, delete, validate, mask, cost estimate)
  - anthropic SDK dependency for key validation
affects: [04-02-PLAN, 09-claude-chat-integration]

# Tech tracking
tech-stack:
  added: [anthropic>=0.40]
  patterns: [dialect-agnostic upsert for user_api_keys, mask_api_key pure function for safe display]

key-files:
  created:
    - backend/src/musicmind/api/claude/__init__.py
    - backend/src/musicmind/api/claude/schemas.py
    - backend/src/musicmind/api/claude/service.py
    - backend/tests/test_claude_byok.py
  modified:
    - backend/src/musicmind/db/schema.py
    - backend/pyproject.toml
    - backend/uv.lock
    - backend/tests/test_schema.py

key-decisions:
  - "Composite PK (user_id, service) on user_api_keys for future multi-provider key support"
  - "Static cost estimate function with hardcoded Sonnet 4 pricing (not real-time tracking)"
  - "validate_anthropic_key uses max_tokens=1 for minimal token spend during validation"

patterns-established:
  - "BYOK service module follows Phase 3 pattern: async DB ops with engine param, EncryptionService for encrypt/decrypt"
  - "mask_api_key pure function: 'sk-ant-...{last4}' format for safe key display"

requirements-completed: [BYOK-01, BYOK-02, BYOK-03, BYOK-04]

# Metrics
duration: 6min
completed: 2026-03-27
---

# Phase 4 Plan 1: BYOK Foundation Summary

**Encrypted API key storage with Fernet, 7 service helpers (store/validate/mask/cost), and Anthropic SDK integration for Claude BYOK**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-27T10:17:08Z
- **Completed:** 2026-03-27T10:23:33Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- user_api_keys table added to schema.py with composite PK (user_id, service) and FK cascade to users
- 4 Pydantic models: StoreKeyRequest, KeyStatusResponse, ValidateKeyResponse, CostEstimateResponse
- 7 service functions covering full BYOK lifecycle: store, status, decrypt, delete, validate, mask, cost
- anthropic>=0.40 added as runtime dependency with lockfile updated
- 23 new tests (all passing), 96 total tests with zero regressions

## Task Commits

Each task was committed atomically:

1. **TDD RED: Failing tests for schema, models, and service** - `52144b3` (test)
2. **Task 1: Database table, Pydantic schemas, anthropic dependency** - `efde275` (feat)
3. **Task 2: BYOK service helpers + schema test fix** - `f7ef8cd` (feat)

_TDD approach: tests written first, then implementation to pass them._

## Files Created/Modified
- `backend/src/musicmind/api/claude/__init__.py` - Empty init for claude API module
- `backend/src/musicmind/api/claude/schemas.py` - 4 Pydantic request/response models for BYOK endpoints
- `backend/src/musicmind/api/claude/service.py` - 7 service functions: store, status, decrypt, delete, validate, mask, cost
- `backend/src/musicmind/db/schema.py` - Added user_api_keys table after service_connections
- `backend/pyproject.toml` - Added anthropic>=0.40 to dependencies
- `backend/uv.lock` - Updated lockfile with anthropic and transitive deps
- `backend/tests/test_claude_byok.py` - 23 tests covering all schema, model, and service behavior
- `backend/tests/test_schema.py` - Updated table count from 12 to 13, added user_api_keys to lists

## Decisions Made
- Composite PK (user_id, service) on user_api_keys allows future multi-provider key support (e.g., OpenAI, Gemini) without schema change
- Static cost estimate with hardcoded Sonnet 4 pricing -- real-time cost tracking deferred to Phase 9
- validate_anthropic_key uses max_tokens=1 with a minimal "hi" message to minimize token spend during validation
- mask_api_key shows "sk-ant-...{last4}" for recognizable but safe display; keys shorter than 8 chars show "****"

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_schema.py table count for new user_api_keys table**
- **Found during:** Task 2 (service helpers)
- **Issue:** test_all_tables_present expected exactly 12 tables, now 13 with user_api_keys
- **Fix:** Added user_api_keys to ALL_TABLE_NAMES and DATA_TABLE_NAMES lists, updated count to 13
- **Files modified:** backend/tests/test_schema.py
- **Verification:** All 96 tests pass
- **Committed in:** f7ef8cd (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Necessary fix for test compatibility with new table. No scope creep.

## Issues Encountered
None - execution proceeded without problems.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 7 service functions ready for Plan 02 router to wire into HTTP endpoints
- Schemas ready for request validation and response serialization
- user_api_keys table ready for Alembic migration generation
- anthropic SDK installed for key validation calls

---
*Phase: 04-byok-claude-api-key-management*
*Completed: 2026-03-27*

## Self-Check: PASSED

All 5 created files verified on disk. All 3 commit hashes found in git log.
