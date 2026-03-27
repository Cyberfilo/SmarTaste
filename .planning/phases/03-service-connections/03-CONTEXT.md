# Phase 3: Service Connections - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning
**Source:** Auto-mode (recommended defaults selected)

<domain>
## Phase Boundary

This phase delivers Spotify and Apple Music OAuth connection flows, a connection management API, and token lifecycle handling. Users can connect/disconnect services, and the app handles token refresh (Spotify) and re-auth prompts (Apple Music). Builds on Phase 2's user auth and Phase 1's encryption + service_connections table.

Requirements: SVCN-01 (Spotify OAuth), SVCN-02 (Apple Music OAuth), SVCN-03 (disconnect), SVCN-04 (connection status), SVCN-05 (Apple Music re-auth prompt), SVCN-06 (Spotify token refresh).

</domain>

<decisions>
## Implementation Decisions

### Spotify OAuth (PKCE)
- **D-01:** Backend-initiated PKCE flow. Backend generates code_verifier + code_challenge, stores in session, redirects user to Spotify authorize URL.
- **D-02:** Callback lands on backend endpoint (`/api/services/spotify/callback`). Backend exchanges authorization code for tokens using the stored code_verifier.
- **D-03:** Access token + refresh token encrypted via Fernet EncryptionService and stored in `service_connections` table.
- **D-04:** Scopes requested: `user-read-private user-read-email user-library-read user-read-recently-played user-top-read`. These cover library access, listening history, and top items needed for later phases.
- **D-05:** Spotify Developer app must be registered at developer.spotify.com with redirect URI matching the callback endpoint. 5-user dev mode cap applies.

### Apple Music OAuth (MusicKit JS)
- **D-06:** Frontend-initiated flow. MusicKit JS runs in the browser, user authorizes via Apple's UI, JavaScript receives the Music User Token.
- **D-07:** Frontend POSTs the Music User Token to backend endpoint (`/api/services/apple-music/connect`). Backend validates and stores encrypted.
- **D-08:** Apple Developer Token (ES256 JWT) generated server-side using existing auth pattern from the MCP codebase. Sent to frontend for MusicKit JS initialization.
- **D-09:** No refresh mechanism exists for Apple Music User Tokens. Token health checked before API calls; expired tokens trigger re-auth prompt.

### Connection Management
- **D-10:** API-only endpoints in this phase. No frontend UI — that comes in later phases.
- **D-11:** Endpoints: GET /api/services (list connections + status), POST /api/services/spotify/connect (initiate OAuth), GET /api/services/spotify/callback (OAuth callback), POST /api/services/apple-music/connect (store MUT), DELETE /api/services/{service} (disconnect).
- **D-12:** Connection status: "connected" (valid tokens), "expired" (token health check failed), "not_connected" (no record).
- **D-13:** Disconnect deletes the service_connections row and revokes Spotify token if applicable.

### Token Lifecycle
- **D-14:** Spotify tokens refreshed on-demand before API calls, not via background job. Simpler, sufficient for friend-group scale.
- **D-15:** Spotify refresh: if access token expired, use refresh_token to get new access token from Spotify. Update encrypted tokens in DB. If refresh fails, mark connection as "expired".
- **D-16:** Apple Music health check: lightweight API call (e.g., /v1/me/storefront) to test token validity. If 401, mark as "expired" and prompt re-auth.
- **D-17:** All tokens encrypted at rest using Fernet EncryptionService (Phase 1). Decrypted only when needed for API calls.

### Claude's Discretion
- Exact Spotify PKCE implementation (secrets module for code_verifier, hashlib for S256 challenge)
- Whether to use httpx directly for Spotify token exchange or create a SpotifyOAuth helper class
- Apple Developer Token generation approach (reuse from existing MCP codebase vs new implementation)
- Test strategy for OAuth flows (mock Spotify API responses, test token encryption round-trip)
- Alembic migration if any schema changes needed beyond existing service_connections table

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 1 Outputs
- `backend/src/musicmind/db/schema.py` — `service_connections` table already exists with user_id, service, access_token, refresh_token, token_expires_at, service_user_id, service_email columns
- `backend/src/musicmind/security/encryption.py` — Fernet EncryptionService for token encryption
- `backend/src/musicmind/config.py` — Settings class. Needs spotify_client_id, spotify_client_secret, spotify_redirect_uri, apple_team_id, apple_key_id, apple_private_key_path fields added.
- `backend/src/musicmind/app.py` — FastAPI app factory. Service routes mount here.
- `backend/src/musicmind/api/router.py` — Main router. Service router gets included here.

### Phase 2 Outputs
- `backend/src/musicmind/auth/dependencies.py` — `get_current_user` dependency. All service endpoints need authenticated users.

### Existing MCP Codebase (reference for Apple Music patterns)
- `src/musicmind/auth.py` — Existing AuthManager with ES256 JWT developer token generation. Pattern to reuse.
- `src/musicmind/setup.py` — Existing MusicKit JS OAuth flow with local HTTP server. Reference for the MusicKit JS approach.
- `src/musicmind/config.py` — Existing MusicMindConfig with team_id, key_id, private_key_path fields.

### Research
- `.planning/research/PITFALLS.md` — Pitfall 6 (Spotify PKCE traps), Pitfall 10 (MusicKit JS browser-only auth), Pitfall 4 (Apple Music token expiry)
- `.planning/research/STACK.md` — Spotify PKCE is mandatory since Nov 2025

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/src/musicmind/db/schema.py` — `service_connections` table already has all needed columns including encrypted token fields
- `backend/src/musicmind/security/encryption.py` — EncryptionService ready for token encryption
- `backend/src/musicmind/auth/dependencies.py` — `get_current_user` dependency for protecting service endpoints
- `src/musicmind/auth.py` (existing MCP) — ES256 JWT developer token generation pattern to adapt for Apple Music
- `src/musicmind/setup.py` (existing MCP) — MusicKit JS HTML template and OAuth flow reference

### Established Patterns
- FastAPI router pattern (auth/router.py from Phase 2)
- SQLAlchemy Core queries with async engine
- Pydantic schemas for request/response validation
- Encrypted storage via Fernet for sensitive data

### Integration Points
- Services router mounts on main API router
- All endpoints require `get_current_user` dependency
- service_connections table stores per-user service tokens
- Settings class needs new config fields for Spotify + Apple Music credentials

</code_context>

<specifics>
## Specific Ideas

No specific requirements — auto-mode selected standard OAuth patterns.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 03-service-connections*
*Context gathered: 2026-03-27 via auto-mode*
