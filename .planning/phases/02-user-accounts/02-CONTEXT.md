# Phase 2: User Accounts - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning
**Source:** Auto-mode (recommended defaults selected)

<domain>
## Phase Boundary

This phase delivers user account management: signup with email/password, login with persistent JWT sessions, logout from any page, and CSRF protection. Builds on Phase 1's FastAPI backend, PostgreSQL `users` table, and Fernet encryption.

Requirements: ACCT-01 (signup), ACCT-02 (persistent login), ACCT-03 (logout), ACCT-04 (session security).

</domain>

<decisions>
## Implementation Decisions

### Auth Strategy
- **D-01:** JWT access tokens delivered in httpOnly secure cookies (not localStorage). This prevents XSS from accessing tokens.
- **D-02:** Access token lifetime: 30 minutes. Refresh token lifetime: 7 days. Refresh tokens stored in database for revocation capability.
- **D-03:** On login, set both access and refresh tokens as httpOnly, secure, SameSite=Lax cookies.
- **D-04:** On logout, clear both cookies and invalidate refresh token in database.

### Password Handling
- **D-05:** Password hashing via bcrypt (passlib[bcrypt]). Industry standard, sufficient for friend-group scale.
- **D-06:** Minimum password length: 8 characters. No other complexity rules (friend group, not enterprise).
- **D-07:** Passwords stored as bcrypt hashes in the `users` table `password_hash` column.

### CSRF Protection
- **D-08:** Double-submit cookie pattern. Backend generates a CSRF token, sets it as a non-httpOnly cookie (readable by JS). Frontend sends it back in X-CSRF-Token header. Backend validates match.
- **D-09:** CSRF validation on all state-changing endpoints (POST, PUT, DELETE). GET endpoints exempt.

### Session Persistence
- **D-10:** JWT contains: user_id, email, issued_at, expires_at. Signed with a secret key from Settings.
- **D-11:** Refresh flow: when access token expires, frontend calls /auth/refresh with refresh cookie. Backend validates refresh token against database, issues new access token.
- **D-12:** On browser close + reopen, refresh cookie persists (7-day expiry), user stays logged in.

### Signup Flow
- **D-13:** Email + password signup. No email verification in v1 (friend group — trust the users).
- **D-14:** Duplicate email check returns generic error ("Account creation failed") to prevent email enumeration.

### Claude's Discretion
- Exact API endpoint paths (recommend: /api/auth/signup, /api/auth/login, /api/auth/logout, /api/auth/refresh, /api/auth/me)
- JWT library choice (recommend: python-jose or PyJWT — both already in ecosystem)
- Whether to add a minimal login/signup frontend page or backend-only API in this phase
- Test fixture patterns for authenticated requests

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 1 Outputs
- `backend/src/musicmind/app.py` — FastAPI app factory with lifespan. New auth routes mount here.
- `backend/src/musicmind/config.py` — Settings class. Needs jwt_secret_key and jwt_algorithm fields added.
- `backend/src/musicmind/db/schema.py` — Has `users` table with id, email, password_hash, created_at, updated_at. Auth writes to this table.
- `backend/src/musicmind/db/engine.py` — Async engine factory. Auth endpoints need database access.
- `backend/src/musicmind/security/encryption.py` — Fernet EncryptionService. May be used for refresh token storage.
- `backend/src/musicmind/api/router.py` — Main router. Auth router gets included here.

### Research (to be created)
- `.planning/research/STACK.md` — Recommended JWT + auth libraries from project research.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/src/musicmind/db/schema.py` — `users` table already has id, email, password_hash, created_at, updated_at columns. Ready for auth.
- `backend/src/musicmind/security/encryption.py` — EncryptionService for encrypting refresh tokens at rest.
- `backend/src/musicmind/config.py` — Settings with env var loading. Add jwt_secret_key here.
- `backend/src/musicmind/api/router.py` — Main API router to include auth sub-router.
- `backend/tests/conftest.py` — Existing test fixtures with async event loop and test client.

### Established Patterns
- FastAPI dependency injection for request context
- SQLAlchemy Core (no ORM) for database operations
- Pydantic BaseModel for request/response schemas
- Async everywhere (asyncpg, async endpoints)

### Integration Points
- Auth router mounts on the main FastAPI app via router.py
- Auth middleware/dependency extracts user from JWT cookie on protected endpoints
- Users table in schema.py is the auth data store

</code_context>

<specifics>
## Specific Ideas

No specific requirements — auto-mode selected standard auth patterns.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 02-user-accounts*
*Context gathered: 2026-03-27 via auto-mode*
