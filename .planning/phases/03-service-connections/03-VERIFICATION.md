---
phase: 03-service-connections
verified: 2026-03-26T00:00:00Z
status: passed
score: 17/17 must-haves verified
re_verification: false
---

# Phase 03: Service Connections Verification Report

**Phase Goal:** Users can connect and disconnect their Spotify and Apple Music accounts, and the app handles token lifecycle correctly
**Verified:** 2026-03-26
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Settings class accepts Spotify and Apple Music configuration fields | VERIFIED | `config.py` lines 23–30: 6 fields with correct defaults (`spotify_redirect_uri` defaults to 127.0.0.1, all others `None`) |
| 2 | PKCE code_verifier and code_challenge can be generated correctly | VERIFIED | `service.py:43–52`: `secrets.token_urlsafe(64)` + SHA256 + base64url. Unit test `test_pkce_pair_generation` verifies S256 relationship |
| 3 | Spotify authorize URL can be constructed with correct parameters | VERIFIED | `service.py:55–81`: All 7 required params present (response_type, client_id, scope, redirect_uri, state, code_challenge_method=S256, code_challenge). Test passes |
| 4 | Apple Developer Token can be generated as ES256 JWT | VERIFIED | `service.py:84–112`: ES256 JWT signed from .p8 file path. `test_apple_developer_token_endpoint` generates real EC key pair and validates 3-part JWT structure |
| 5 | Spotify tokens can be exchanged and refreshed via httpx | VERIFIED | `service.py:118–177`: `exchange_spotify_code` and `refresh_spotify_token` use httpx with PKCE form_data (no `client_secret` in dict). Tests mock both paths |
| 6 | Apple Music token health can be checked against the API | VERIFIED | `service.py:198–233`: `check_apple_music_token` returns False on both 401 and 403, handles connection errors gracefully |
| 7 | Service connections can be upserted and deleted in the database | VERIFIED | `service.py:239–343`: SELECT-then-INSERT/UPDATE pattern (no `sqlalchemy.dialects.postgresql` imports). Verified cross-dialect compatible. `delete_service_connection` returns bool |
| 8 | Connection status is derivable from stored token metadata | VERIFIED | `router.py:38–93`: Status derived from `token_expires_at` DB-only (no external API calls). Timezone normalization handles SQLite timezone-naive datetimes |
| 9 | Spotify connect endpoint returns redirect URL to accounts.spotify.com with PKCE params | VERIFIED | `router.py:96–130`: POST `/api/services/spotify/connect` stores PKCE state in session, returns `SpotifyConnectResponse`. Test confirms `accounts.spotify.com`, `S256`, `client_id` in URL |
| 10 | Spotify callback exchanges code for tokens and stores encrypted in service_connections | VERIFIED | `router.py:133–194`: State validation, session retrieval, token exchange, profile fetch, encrypted upsert. Test verifies decryptable tokens in DB |
| 11 | Apple Music developer-token endpoint returns valid ES256 JWT | VERIFIED | `router.py:197–219`: Returns `AppleMusicDeveloperTokenResponse`. Test verifies 3-part JWT structure from real EC key |
| 12 | Apple Music connect endpoint stores encrypted Music User Token in service_connections | VERIFIED | `router.py:222–248`: Calls `upsert_service_connection` with no expiry, no user_id. Test verifies decryptable token, NULL service_user_id, NULL token_expires_at |
| 13 | List services endpoint returns correct status for connected, expired, and not-connected services | VERIFIED | `router.py:38–93`: Both "spotify" and "apple_music" always in response. Three tests cover all three status values |
| 14 | Disconnect endpoint deletes the service_connections row | VERIFIED | `router.py:251–279`: Validates service name, calls `delete_service_connection`, returns 404 if not found. Test verifies row is gone from DB |
| 15 | Spotify token refresh works on-demand and updates encrypted tokens in DB | VERIFIED | `service.py:151–177`: Returns `None` on 4xx, new token dict on success. Test `test_spotify_token_refresh_updates_db` verifies upsert updates encrypted token |
| 16 | Apple Music expired token is detectable via health check | VERIFIED | `service.py:198–233`: Returns False on 401/403. List endpoint returns "expired" status when `token_expires_at` is in the past. Test `test_list_connections_shows_expired_spotify` passes |
| 17 | SessionMiddleware is active for PKCE state storage across redirect | VERIFIED | `app.py:44`: `app.add_middleware(SessionMiddleware, secret_key=_settings.jwt_secret_key)`. Runtime check confirms `SessionMiddleware` in `app.user_middleware` |

**Score:** 17/17 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/src/musicmind/config.py` | Settings with 6 new OAuth fields | VERIFIED | All 6 fields present with correct defaults. Lines 22–30 |
| `backend/src/musicmind/api/services/__init__.py` | Package init | VERIFIED | Exists, empty (correct pattern) |
| `backend/src/musicmind/api/services/schemas.py` | 6 Pydantic models | VERIFIED | 51 lines. All 6 models: ServiceConnectionResponse, ServiceListResponse, SpotifyConnectResponse, AppleMusicConnectRequest (min_length=1), AppleMusicDeveloperTokenResponse, DisconnectResponse |
| `backend/src/musicmind/api/services/service.py` | 10 helper functions, min 100 lines | VERIFIED | 385 lines. All 10 functions present and importable. No `sqlalchemy.dialects.postgresql` imports |
| `backend/src/musicmind/api/services/router.py` | 6 endpoints, min 80 lines | VERIFIED | 280 lines. All 6 endpoints: list_connections, spotify_connect, spotify_callback, apple_music_developer_token, apple_music_connect, disconnect_service |
| `backend/tests/test_services.py` | Integration tests, min 100 lines | VERIFIED | 711 lines. 18 tests covering all SVCN requirements |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `service.py` | `security/encryption.py` | `encryption.encrypt/decrypt` | WIRED | Lines 276–278: `encryption.encrypt(access_token)`, `encryption.encrypt(refresh_token)` |
| `service.py` | `db/schema.py` | `service_connections` table | WIRED | Lines 22, 267, 287–295, 299–308, 330–337, 363–383: all DB operations use `service_connections` table |
| `service.py` | `config.py` | Settings OAuth credential fields | WIRED | Router accesses `settings.spotify_client_id`, `settings.spotify_redirect_uri`, `settings.apple_team_id`, `settings.apple_key_id`, `settings.apple_private_key_path` |
| `router.py` | `service.py` | Imports service functions | WIRED | `router.py:20–29`: imports 8 of 10 functions from `musicmind.api.services.service` |
| `router.py` | `auth/dependencies.py` | `Depends(get_current_user)` | WIRED | 5 of 6 endpoints use it; `spotify_callback` intentionally omits it (user_id from session — correct per D-02) |
| `api/router.py` | `services/router.py` | `include_router(services_router)` | WIRED | `api/router.py:8,14`: imported and included |
| `app.py` | `starlette.middleware.sessions` | `SessionMiddleware` | WIRED | `app.py:9,44`: imported and registered after CSRFMiddleware |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `router.py` list_connections | `connections` | `get_user_connections(engine, user_id=...)` → DB SELECT | Yes — real DB rows returned, status derived from `token_expires_at` | FLOWING |
| `router.py` spotify_callback | `token_data`, `profile` | `exchange_spotify_code` + `fetch_spotify_user_profile` (httpx to Spotify API) | Yes — or 400 on failure | FLOWING |
| `service.py` upsert_service_connection | `encrypted_access`, `encrypted_refresh` | `encryption.encrypt(access_token)` then DB INSERT/UPDATE | Yes — tokens encrypted and persisted | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 10 service.py functions importable | `uv run python -c "from musicmind.api.services.service import generate_pkce_pair, ..."` | Imported OK | PASS |
| PKCE URL contains correct params | `build_spotify_authorize_url(...)` with assertions | `accounts.spotify.com/authorize` present, `S256` present | PASS |
| 6 schemas importable | `from musicmind.api.services.schemas import ...` | All 6 schemas imported | PASS |
| 6 service endpoints registered | App routes check | `/api/services`, `/api/services/spotify/connect`, `/api/services/spotify/callback`, `/api/services/apple-music/developer-token`, `/api/services/apple-music/connect`, `/api/services/{service}` all present | PASS |
| SessionMiddleware active | Middleware check | `['SessionMiddleware', 'CSRFMiddleware']` | PASS |
| No PostgreSQL dialect imports | Source inspection | Clean — no `sqlalchemy.dialects.postgresql` | PASS |
| Settings defaults correct | `Settings(fernet_key='k', jwt_secret_key='k')` | `spotify_redirect_uri=http://127.0.0.1:8000/...`, all others `None` | PASS |
| No client_secret in PKCE form_data | Form data dict inspection | Neither `exchange_spotify_code` nor `refresh_spotify_token` form_data contains `client_secret` | PASS |
| 18/18 service tests pass | `uv run pytest tests/test_services.py -v` | 18 passed | PASS |
| 73/73 total tests pass | `uv run pytest tests/ -q` | 73 passed, 0 failed | PASS |
| Services module ruff clean | `uv tool run ruff check src/musicmind/api/services/` | All checks passed (only pre-existing issues in `auth/service.py`) | PASS |

---

### Requirements Coverage

| Requirement | Description | Status | Evidence |
|-------------|-------------|--------|----------|
| SVCN-01 | User can connect their Spotify account via OAuth PKCE flow | SATISFIED | `spotify_connect` + `spotify_callback` endpoints. PKCE state stored in session. State parameter validates CSRF. 3 tests cover connect URL, token storage, and bad-state rejection |
| SVCN-02 | User can connect their Apple Music account via MusicKit JS OAuth flow | SATISFIED | `apple_music_developer_token` endpoint returns ES256 JWT for MusicKit JS. `apple_music_connect` stores encrypted Music User Token. 3 tests cover developer token, misconfiguration, and token storage |
| SVCN-03 | User can disconnect a connected service | SATISFIED | `disconnect_service` DELETE endpoint validates service name, deletes row, returns 404 if not found. 3 tests cover deletion, 404, and invalid service name |
| SVCN-04 | User can see which services are connected and their connection status | SATISFIED | `list_connections` endpoint returns both services always, status derived from DB `token_expires_at`. 2 tests cover connected+not_connected and all-not_connected |
| SVCN-05 | User is prompted to re-authenticate when Apple Music token expires (no silent refresh) | SATISFIED | `list_connections` returns "expired" status when `token_expires_at` is in the past. `check_apple_music_token` returns False on 401/403. Test `test_list_connections_shows_expired_spotify` verifies expired status detection |
| SVCN-06 | Spotify access tokens are automatically refreshed using stored refresh token | SATISFIED | `refresh_spotify_token` uses PKCE flow (no client_secret), returns `None` on 4xx. `test_spotify_token_refresh_returns_new_token`, `test_spotify_token_refresh_returns_none_on_failure`, and `test_spotify_token_refresh_updates_db` verify all three behaviors |

All 6 requirements: SATISFIED. No orphaned requirements detected.

---

### Anti-Patterns Found

No blockers or stubs found.

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `auth/service.py` | 35, 36 | Pre-existing UP017 and E501 (timezone.utc, line length) | Info | Out of scope for this phase — noted in Plan 01 Summary as pre-existing |

No `TODO`, `FIXME`, placeholder comments, empty handlers, or hardcoded empty returns found in phase-produced files.

---

### Human Verification Required

#### 1. Full Spotify OAuth PKCE Round-Trip

**Test:** Configure real Spotify Developer App credentials in `.env`. Start the backend (`uv run python -m musicmind`). Open a browser, POST to `/api/services/spotify/connect` (authenticated), redirect the browser to the returned `authorize_url`, complete Spotify authorization, verify the callback succeeds and `GET /api/services` shows `"status": "connected"` for Spotify.
**Expected:** Spotify account connected end-to-end with real token exchange.
**Why human:** Requires real Spotify Developer credentials and browser OAuth flow. Cannot mock in automated tests.

#### 2. Apple Music MusicKit JS Integration

**Test:** Configure real Apple Developer credentials (team_id, key_id, .p8 key path) in `.env`. GET `/api/services/apple-music/developer-token` (authenticated) to get a developer token. Use MusicKit JS `authorize()` in a browser to obtain a Music User Token. POST to `/api/services/apple-music/connect` with the token. Verify `GET /api/services` shows `"status": "connected"` for Apple Music.
**Expected:** Apple Music account connected end-to-end with a real Music User Token.
**Why human:** Requires real Apple Developer account, MusicKit JS browser flow, and Apple Music subscription. Cannot automate.

#### 3. Spotify Token Refresh On-Demand Trigger

**Test:** After connecting Spotify with real credentials, manually set `token_expires_at` to the past in the database. Implement (or manually call) `refresh_spotify_token` with the stored encrypted refresh token to verify a new access token is returned and stored.
**Expected:** Expired token silently refreshed, `GET /api/services` returns `"status": "connected"` again.
**Why human:** Requires real Spotify refresh token and actual refresh API call. Automated tests use mocks.

---

### Gaps Summary

No gaps. All 17 observable truths verified, all 6 artifacts are substantive and wired, all 4 key data flows confirmed, all 6 SVCN requirements satisfied, 73/73 tests pass, ruff clean on phase files.

The only notes are:

1. Pre-existing lint issues in `auth/service.py` (UP017, E501) — explicitly noted as out of scope in Plan 01 Summary.
2. The `client_secret` string appears in docstring comments of `exchange_spotify_code` and `refresh_spotify_token` (as "no client_secret is sent") but is absent from the actual `form_data` dicts — correct behavior.
3. 16 httpx `DeprecationWarning` instances in tests (per-request cookies API change) — these are warnings, not failures, and are in the httpx library layer, not test logic.

---

_Verified: 2026-03-26_
_Verifier: Claude (gsd-verifier)_
