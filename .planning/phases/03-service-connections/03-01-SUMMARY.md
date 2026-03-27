---
phase: 03-service-connections
plan: 01
subsystem: api
tags: [oauth, spotify, apple-music, pkce, es256, jwt, httpx, encryption]

# Dependency graph
requires:
  - phase: 01-infrastructure-foundation
    provides: Settings(BaseSettings), EncryptionService, service_connections table
  - phase: 02-user-accounts
    provides: Auth package pattern (schemas, service, router), JWT auth
provides:
  - Settings with 6 Spotify/Apple Music OAuth configuration fields
  - 6 Pydantic schemas for service endpoint request/response shapes
  - PKCE code_verifier/code_challenge generation
  - Spotify authorize URL builder with S256 challenge
  - Apple Developer Token ES256 JWT generator
  - Spotify token exchange and refresh (PKCE, no client_secret)
  - Apple Music token health check (handles 401 and 403)
  - Dialect-agnostic service connection DB upsert/delete/list
affects: [03-02-PLAN, service-router, spotify-client, apple-music-client]

# Tech tracking
tech-stack:
  added: [httpx (runtime dependency)]
  patterns: [PKCE S256 flow, dialect-agnostic upsert, ES256 JWT generation]

key-files:
  created:
    - backend/src/musicmind/api/services/__init__.py
    - backend/src/musicmind/api/services/schemas.py
    - backend/src/musicmind/api/services/service.py
  modified:
    - backend/src/musicmind/config.py
    - backend/pyproject.toml

key-decisions:
  - "httpx promoted from dev to runtime dependency for Spotify token exchange"
  - "PKCE flow with no client_secret for both exchange and refresh per Spotify Nov 2025 policy"
  - "Dialect-agnostic SELECT-then-INSERT/UPDATE for upsert instead of PostgreSQL ON CONFLICT"
  - "spotify_redirect_uri defaults to 127.0.0.1 not localhost (Spotify blocks HTTP localhost)"

patterns-established:
  - "Service helper module: pure functions for sync ops, async functions for HTTP/DB"
  - "PKCE pair generation with secrets.token_urlsafe(64) + SHA256 + base64url"
  - "Cross-dialect DB upsert pattern within engine.begin() transaction"

requirements-completed: [SVCN-01, SVCN-02, SVCN-03, SVCN-04, SVCN-05, SVCN-06]

# Metrics
duration: 6min
completed: 2026-03-27
---

# Phase 03 Plan 01: Service Connections Foundation Summary

**Spotify PKCE OAuth + Apple Music ES256 token helpers with encrypted DB storage and 6 Pydantic endpoint schemas**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-27T09:21:51Z
- **Completed:** 2026-03-27T09:27:43Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Extended Settings with 6 new fields for Spotify and Apple Music OAuth configuration
- Created 6 Pydantic schemas covering all service endpoint request/response shapes
- Implemented 10 service helper functions: PKCE, Spotify token ops, Apple Developer Token, health check, and 3 DB operations
- Ensured dialect-agnostic DB operations (works with both PostgreSQL and SQLite)

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend Settings and create service schemas** - `492e925` (feat)
2. **Task 2: Create service helper module** - `31b22bc` (feat)

## Files Created/Modified
- `backend/src/musicmind/config.py` - Added spotify_client_id, spotify_client_secret, spotify_redirect_uri, apple_team_id, apple_key_id, apple_private_key_path
- `backend/src/musicmind/api/services/__init__.py` - Package init for service connection module
- `backend/src/musicmind/api/services/schemas.py` - 6 Pydantic models: ServiceConnectionResponse, ServiceListResponse, SpotifyConnectResponse, AppleMusicConnectRequest, AppleMusicDeveloperTokenResponse, DisconnectResponse
- `backend/src/musicmind/api/services/service.py` - 10 functions: generate_pkce_pair, build_spotify_authorize_url, generate_apple_developer_token, exchange_spotify_code, refresh_spotify_token, fetch_spotify_user_profile, check_apple_music_token, upsert_service_connection, delete_service_connection, get_user_connections
- `backend/pyproject.toml` - Added httpx to runtime dependencies
- `backend/uv.lock` - Updated lockfile with httpx as runtime dep

## Decisions Made
- Promoted httpx from dev-only to runtime dependency since the service module needs it for Spotify token exchange/refresh and Apple Music health checks at runtime
- Used PKCE flow without client_secret for both Spotify token exchange and refresh per Spotify's Nov 2025 policy requiring loopback IP (127.0.0.1) for HTTP
- Implemented dialect-agnostic SELECT-then-INSERT/UPDATE for service connection upsert instead of PostgreSQL-specific ON CONFLICT, ensuring SQLite test compatibility
- Set spotify_redirect_uri default to http://127.0.0.1:8000 instead of http://localhost:8000 because Spotify blocks HTTP on localhost

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Promoted httpx from dev to runtime dependency**
- **Found during:** Task 2 (service helper module creation)
- **Issue:** httpx was only in dev dependencies but service.py imports it for Spotify token exchange, refresh, user profile fetch, and Apple Music health check at runtime
- **Fix:** Added httpx>=0.27 to main dependencies in pyproject.toml
- **Files modified:** backend/pyproject.toml, backend/uv.lock
- **Verification:** uv sync succeeded, all imports work
- **Committed in:** 31b22bc (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** httpx was already a project dependency (in dev), just needed promotion to runtime. No scope creep.

## Issues Encountered
- Pre-existing ruff lint errors in auth/service.py (UP017, E501) -- out of scope, not touched by this plan

## User Setup Required
None - no external service configuration required. Spotify and Apple Music credentials are optional Settings fields that default to None.

## Next Phase Readiness
- Service helper module is complete and ready for Plan 02 (router endpoints)
- Plan 02 will import from musicmind.api.services.service and musicmind.api.services.schemas
- All 10 functions are importable and verified
- 55 existing tests continue to pass

## Self-Check: PASSED

All 5 created files verified present on disk. Both task commit hashes (492e925, 31b22bc) found in git log.

---
*Phase: 03-service-connections*
*Completed: 2026-03-27*
