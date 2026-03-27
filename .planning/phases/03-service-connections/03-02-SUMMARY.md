---
phase: 03-service-connections
plan: 02
subsystem: api
tags: [oauth, spotify, apple-music, pkce, session, csrf, integration-tests, sqlite, fastapi]

# Dependency graph
requires:
  - phase: 03-01
    provides: service.py (10 helpers), schemas.py (6 Pydantic models), Settings OAuth fields
  - phase: 02-02
    provides: CSRF middleware pattern, JWT auth cookies, conftest.py fixtures
provides:
  - 6 REST endpoints mounted at /api/services (list, spotify connect/callback, apple developer-token/connect, disconnect)
  - SessionMiddleware for PKCE state storage across OAuth redirect
  - 18 integration/unit tests covering all SVCN requirements
  - services_router wired into api_router
affects: [04-PLAN, frontend-service-settings, spotify-client, apple-music-client]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - PKCE state storage in server-side session (starlette SessionMiddleware)
    - OAuth state parameter validation as CSRF protection for callback endpoints
    - Timezone-aware/naive datetime normalization for SQLite compat

key-files:
  created:
    - backend/tests/test_services.py
  modified:
    - backend/src/musicmind/api/services/router.py
    - backend/src/musicmind/app.py
    - backend/src/musicmind/api/router.py

key-decisions:
  - "Spotify callback does not use get_current_user; user_id stored in session at connect time"
  - "list_connections status derived from DB-only (no external API calls at status check time)"
  - "router.py patches for Spotify mock (not service.py) since router imports functions at module level"
  - "UTC normalization in router for SQLite timezone-naive datetime compat (Rule 1 auto-fix)"

patterns-established:
  - "Test pattern: re-initiate PKCE connect inside mock context to capture state value"
  - "MagicMock for sync httpx Response.json(), AsyncMock for async httpx client methods"

requirements-completed: [SVCN-01, SVCN-02, SVCN-03, SVCN-04, SVCN-05, SVCN-06]

# Metrics
duration: 15min
completed: 2026-03-27
---

# Phase 03 Plan 02: Service Router and Integration Tests Summary

**6 service endpoints with PKCE OAuth and SessionMiddleware, validated by 18 integration tests covering all SVCN requirements**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-03-27
- **Tasks:** 2 (Task 1 complete before this run; Task 2 executed here)
- **Files modified:** 4 (router.py created, app.py modified, api/router.py modified, test_services.py created)

## Accomplishments

- Wired 6 service endpoints into the FastAPI app at `/api/services`
- Added `SessionMiddleware` to `app.py` for PKCE state persistence across the Spotify OAuth redirect
- Included `services_router` in `api_router` — all routes visible via `/api/services/*`
- Created 18 integration and unit tests covering all 6 SVCN requirements with SQLite in-memory DB
- Spotify OAuth PKCE flow tested end-to-end: connect → session state → callback → token storage
- Apple Music developer token tested with a generated ES256 test key pair
- Expired token detection, disconnect, token refresh, and unauthenticated access all covered

## Task Commits

1. **Task 1: Service router + SessionMiddleware + router wiring** — (completed prior to this run; commits: Task 1 wiring)
2. **Task 2: Integration tests** — `79edf5d` (test(03-02))

## Files Created/Modified

- `backend/src/musicmind/api/services/router.py` — 6 endpoint functions: list_connections, spotify_connect, spotify_callback, apple_music_developer_token, apple_music_connect, disconnect_service. Fixed timezone normalization bug (Rule 1).
- `backend/src/musicmind/app.py` — Added `SessionMiddleware` after `CSRFMiddleware`
- `backend/src/musicmind/api/router.py` — Added `include_router(services_router)`
- `backend/tests/test_services.py` — 18 tests: 2 Spotify connect, 2 Spotify callback, 2 Apple dev token, 1 Apple connect, 3 disconnect, 2 list connections, 1 expired status, 3 token refresh, 1 PKCE unit, 1 unauthenticated

## Test Coverage by Requirement

| Requirement | Test(s) |
|-------------|---------|
| SVCN-01 | test_spotify_connect_returns_authorize_url, test_spotify_callback_stores_tokens, test_spotify_callback_rejects_bad_state |
| SVCN-02 | test_apple_developer_token_endpoint, test_apple_developer_token_not_configured_returns_400, test_apple_music_connect_stores_token |
| SVCN-03 | test_disconnect_removes_connection, test_disconnect_not_connected_returns_404, test_disconnect_invalid_service_returns_400 |
| SVCN-04 | test_list_connections_shows_status, test_list_connections_empty_when_no_connections |
| SVCN-05 | test_list_connections_shows_expired_spotify |
| SVCN-06 | test_spotify_token_refresh_returns_new_token, test_spotify_token_refresh_returns_none_on_failure, test_spotify_token_refresh_updates_db |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Timezone-naive datetime comparison crash with SQLite**
- **Found during:** Task 2 (running integration tests)
- **Issue:** `router.py` compared `token_expires_at` (SQLite returns timezone-naive) with `datetime.now(UTC)` (timezone-aware), raising `TypeError: can't compare offset-naive and offset-aware datetimes`
- **Fix:** Added UTC normalization before comparison: `if expires_at.tzinfo is None: expires_at = expires_at.replace(tzinfo=UTC)`
- **Files modified:** `backend/src/musicmind/api/services/router.py`
- **Committed in:** 79edf5d (Task 2 commit, included alongside test file)

---

**Total deviations:** 1 auto-fixed (Rule 1 bug)

## Known Stubs

None. All 6 endpoints are fully wired and tested. No placeholder responses.

## Self-Check: PASSED

- `backend/tests/test_services.py` — FOUND
- `backend/src/musicmind/api/services/router.py` — FOUND
- Task 2 commit `79edf5d` — FOUND in git log
- `uv run pytest tests/ -q` — 73 passed, 0 failed

---
*Phase: 03-service-connections*
*Completed: 2026-03-27*
