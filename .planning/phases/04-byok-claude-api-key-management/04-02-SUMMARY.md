---
phase: 04-byok-claude-api-key-management
plan: 02
subsystem: api
tags: [fastapi, router, byok, integration-tests, anthropic, endpoints]

# Dependency graph
requires:
  - phase: 04-byok-claude-api-key-management
    plan: 01
    provides: service.py (7 helpers), schemas.py (4 Pydantic models), user_api_keys table
  - phase: 02-user-accounts
    provides: get_current_user auth dependency, JWT cookie auth
  - phase: 01-infrastructure-foundation
    provides: EncryptionService, Settings, SQLAlchemy metadata

provides:
  - Claude BYOK router with 5 REST endpoints at /api/claude/*
  - 11 integration tests covering BYOK-01 through BYOK-04

affects:
  - backend/src/musicmind/api/router.py (added claude_router include)

# Tech stack
added: []
patterns:
  - FastAPI APIRouter with prefix and tags
  - Depends(get_current_user) on all endpoints
  - request.app.state for engine/encryption access
  - AsyncMock patching at router import level for validation tests

# Key files
created:
  - backend/src/musicmind/api/claude/router.py
  - backend/tests/test_claude_key.py
modified:
  - backend/src/musicmind/api/router.py

# Decisions
decisions:
  - Mock validate_anthropic_key at router import level (not SDK level) for integration tests
  - Hardcoded test user ID in claude test fixtures (not using conftest uuid4)

# Metrics
duration: 3min
completed: 2026-03-27
tasks: 2
files: 3
tests_added: 11
tests_total: 107
---

# Phase 04 Plan 02: BYOK Router Endpoints & Integration Tests Summary

5 REST endpoints at /api/claude/* wired into api_router, plus 11 integration tests covering all BYOK requirements (store, validate, update, delete, cost estimate)

## What Was Built

### Claude BYOK Router (Task 1)

Created `backend/src/musicmind/api/claude/router.py` with 5 endpoints following the services/router.py pattern:

| Endpoint | Method | Description | Status Code |
|----------|--------|-------------|-------------|
| `/api/claude/key` | POST | Store/update encrypted API key | 201 |
| `/api/claude/key/status` | GET | Check if key configured + masked preview | 200 |
| `/api/claude/key/validate` | POST | Test key against Anthropic API | 200 |
| `/api/claude/key` | DELETE | Remove stored key | 200/404 |
| `/api/claude/key/cost` | GET | Static Sonnet 4 pricing | 200 |

All endpoints are protected by `Depends(get_current_user)` and access `request.app.state.engine` and `request.app.state.encryption` for database and encryption operations.

Updated `backend/src/musicmind/api/router.py` to include `claude_router` in `api_router`.

### Integration Tests (Task 2)

Created `backend/tests/test_claude_key.py` with 11 tests covering all 4 BYOK requirements:

| # | Test | Requirement | Validates |
|---|------|------------|-----------|
| 1 | test_store_key_returns_201 | BYOK-01 | POST stores key, status shows masked preview |
| 2 | test_store_key_unauthenticated_returns_401 | BYOK-01 | Auth required |
| 3 | test_validate_key_success | BYOK-02 | Mocked Anthropic success returns valid=true |
| 4 | test_validate_key_invalid | BYOK-02 | Mocked auth error returns valid=false |
| 5 | test_validate_no_key_stored | BYOK-02 | No key returns valid=false with error message |
| 6 | test_update_key_overwrites | BYOK-03 | Second POST overwrites, status shows latest |
| 7 | test_delete_key_removes | BYOK-03 | DELETE removes, status shows configured=false |
| 8 | test_delete_no_key_returns_404 | BYOK-03 | DELETE with no key returns 404 |
| 9 | test_cost_estimate_returns_pricing | BYOK-04 | Returns model name and pricing strings |
| 10 | test_status_no_key | - | No key returns configured=false, masked_key=null |
| 11 | test_mask_api_key_formats | - | Various key lengths produce correct masks |

## Decisions Made

1. **Mock at router import level**: Patched `musicmind.api.claude.router.validate_anthropic_key` rather than the Anthropic SDK. This tests the full router -> service flow without hitting external APIs.

2. **Hardcoded test user ID**: Used deterministic `test-user-claude-01` instead of conftest's `uuid4()` for simpler fixture setup. The claude tests are self-contained like test_services.py.

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None - all endpoints are fully wired to the service layer with no placeholder data.

## Commits

| Task | Hash | Message |
|------|------|---------|
| 1 | f1dc866 | feat(04-02): add Claude BYOK router with 5 endpoints and wire into api_router |
| 2 | a806987 | test(04-02): add 11 integration tests covering all BYOK requirements |

## Test Results

```
107 passed, 0 failed (96 existing + 11 new)
```

## Self-Check: PASSED

- All created files exist on disk
- All commit hashes found in git log
- 107 tests pass with 0 failures
