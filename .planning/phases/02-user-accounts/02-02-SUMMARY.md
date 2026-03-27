---
phase: 02-user-accounts
plan: 02
subsystem: auth
tags: [fastapi, jwt, bcrypt, csrf, httponly-cookies, starlette-csrf, uuid7, sqlalchemy]

# Dependency graph
requires:
  - phase: 02-user-accounts plan 01
    provides: auth service layer (hash_password, verify_password, create_access_token, create_refresh_token, set_auth_cookies, clear_auth_cookies), schemas, dependencies, refresh_tokens table
provides:
  - Auth router with 5 endpoints (signup, login, logout, refresh, me) at /api/auth/*
  - CSRF middleware with double-submit cookie pattern
  - Integration test suite (16 tests) covering all auth flows
  - SQLite in-memory test infrastructure for auth testing
affects: [03-service-connections, dashboard-ui, claude-chat]

# Tech tracking
tech-stack:
  added: [starlette-csrf, aiosqlite (dev)]
  patterns: [CSRF double-submit cookie, cookie-based JWT auth, token rotation on refresh, module-level Settings for middleware config]

key-files:
  created:
    - backend/src/musicmind/auth/router.py
    - backend/tests/test_auth.py
  modified:
    - backend/src/musicmind/api/router.py
    - backend/src/musicmind/app.py
    - backend/tests/conftest.py
    - backend/pyproject.toml

key-decisions:
  - "Module-level _settings in app.py for CSRF middleware config (middleware must be configured at import time)"
  - "SQLite in-memory engine for integration tests (no PostgreSQL dependency in CI)"
  - "CSRF enforced only when sensitive cookies present (starlette-csrf sensitive_cookies config)"
  - "uuid7 for user IDs (Python 3.14 native, time-ordered UUIDs)"

patterns-established:
  - "CSRF test pattern: GET /health for csrftoken cookie, then POST with x-csrf-token header"
  - "Auth integration test pattern: AsyncClient with ASGITransport and test engine/settings overrides"
  - "Cookie extraction pattern: parse Set-Cookie headers directly for test assertions"

requirements-completed: [ACCT-01, ACCT-02, ACCT-03, ACCT-04]

# Metrics
duration: 9min
completed: 2026-03-27
---

# Phase 02 Plan 02: Auth Endpoints and Integration Summary

**Auth router with signup/login/logout/refresh/me endpoints, CSRF double-submit cookie middleware, and 16 passing integration tests covering all ACCT requirements**

## Performance

- **Duration:** 9 min
- **Started:** 2026-03-27T08:05:49Z
- **Completed:** 2026-03-27T08:14:46Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Auth router with 5 endpoints wired into FastAPI app via api_router
- CSRF middleware (starlette-csrf) with double-submit cookie pattern protecting authenticated POST requests
- 16 integration tests covering all four ACCT requirements with zero regressions (55 total tests pass)
- SQLite in-memory test infrastructure enabling fast, dependency-free test execution

## Task Commits

Each task was committed atomically:

1. **Task 1: Create auth router with all endpoints and wire into app with CSRF** - `b28aae0` (feat)
2. **Task 2: Integration tests for all auth endpoints and security properties** - `03a8420` (test)

## Files Created/Modified
- `backend/src/musicmind/auth/router.py` - Auth API endpoints: signup, login, logout, refresh, me
- `backend/src/musicmind/api/router.py` - Added auth_router include
- `backend/src/musicmind/app.py` - Added CSRFMiddleware, module-level _settings
- `backend/tests/test_auth.py` - 16 integration tests for all auth endpoints
- `backend/tests/conftest.py` - Added JWT test fixtures (test_settings, test_user_id, auth_cookies)
- `backend/pyproject.toml` - Added aiosqlite to dev dependencies
- `backend/uv.lock` - Updated lockfile

## Decisions Made
- Used module-level `_settings = Settings()` in app.py so CSRF middleware secret is available at import time, avoiding double-loading in both lifespan and middleware config
- Used SQLite in-memory database (aiosqlite) for integration tests instead of requiring PostgreSQL, enabling fast CI-friendly tests
- CSRF middleware configured with `sensitive_cookies={"access_token", "refresh_token"}` so CSRF is only enforced on POST/PUT/DELETE when auth cookies are present (unauthenticated signups work without CSRF)
- Used `uuid.uuid7()` (Python 3.14 native) for user IDs providing time-ordered UUIDs

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added aiosqlite dev dependency for SQLite-based test engine**
- **Found during:** Task 2 (Integration tests)
- **Issue:** PostgreSQL not available in test environment; aiosqlite needed for SQLAlchemy async SQLite engine
- **Fix:** Added `aiosqlite>=0.20` to `[project.optional-dependencies] dev` in pyproject.toml
- **Files modified:** backend/pyproject.toml, backend/uv.lock
- **Verification:** All 16 integration tests pass with SQLite in-memory engine
- **Committed in:** 03a8420 (Task 2 commit)

**2. [Rule 1 - Bug] Fixed CSRF token retrieval in test helpers**
- **Found during:** Task 2 (Integration tests)
- **Issue:** After first signup, httpx client jar stored csrftoken; subsequent GET /health responses didn't re-set the cookie, so `resp.cookies.get("csrftoken")` returned empty, causing CSRF validation failure
- **Fix:** Updated `_get_csrf_token` helper to fall back to `client.cookies.get("csrftoken")` when response cookies don't contain it
- **Files modified:** backend/tests/test_auth.py
- **Verification:** All 16 tests pass including duplicate signup and post-login flows
- **Committed in:** 03a8420 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both auto-fixes necessary for test infrastructure to work without PostgreSQL. No scope creep.

## Issues Encountered
- httpx per-request `cookies=` parameter is deprecated; tests produce deprecation warnings but function correctly. Future migration to client-level cookie management may be needed when httpx removes this API.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All four ACCT requirements (ACCT-01 through ACCT-04) are satisfied
- Auth system complete: signup, login, logout, refresh, and protected /me endpoint
- CSRF middleware active, cookie security flags verified (httpOnly, SameSite=Lax)
- Ready for Phase 03 (service connections) which will build on authenticated user context

## Self-Check: PASSED

All files verified present:
- backend/src/musicmind/auth/router.py
- backend/tests/test_auth.py
- backend/src/musicmind/api/router.py
- backend/src/musicmind/app.py
- backend/tests/conftest.py
- backend/pyproject.toml

All commits verified:
- b28aae0 (Task 1)
- 03a8420 (Task 2)

---
*Phase: 02-user-accounts*
*Completed: 2026-03-27*
