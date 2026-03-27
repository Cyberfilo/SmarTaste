# Phase 3: Service Connections - Research

**Researched:** 2026-03-26
**Domain:** OAuth flows (Spotify PKCE, Apple Music MusicKit JS), token lifecycle, FastAPI service API
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Backend-initiated PKCE flow. Backend generates code_verifier + code_challenge, stores in session, redirects user to Spotify authorize URL.
- **D-02:** Callback lands on backend endpoint (`/api/services/spotify/callback`). Backend exchanges authorization code for tokens using the stored code_verifier.
- **D-03:** Access token + refresh token encrypted via Fernet EncryptionService and stored in `service_connections` table.
- **D-04:** Scopes requested: `user-read-private user-read-email user-library-read user-read-recently-played user-top-read`.
- **D-05:** Spotify Developer app must be registered with redirect URI matching the callback endpoint. 5-user dev mode cap applies.
- **D-06:** Frontend-initiated Apple Music flow. MusicKit JS runs in the browser, user authorizes via Apple's UI, JavaScript receives the Music User Token.
- **D-07:** Frontend POSTs the Music User Token to backend endpoint (`/api/services/apple-music/connect`). Backend validates and stores encrypted.
- **D-08:** Apple Developer Token (ES256 JWT) generated server-side using existing auth pattern from the MCP codebase. Sent to frontend for MusicKit JS initialization.
- **D-09:** No refresh mechanism exists for Apple Music User Tokens. Token health checked before API calls; expired tokens trigger re-auth prompt.
- **D-10:** API-only endpoints in this phase. No frontend UI.
- **D-11:** Endpoints: GET /api/services, POST /api/services/spotify/connect, GET /api/services/spotify/callback, POST /api/services/apple-music/connect, DELETE /api/services/{service}.
- **D-12:** Connection status: "connected" (valid tokens), "expired" (token health check failed), "not_connected" (no record).
- **D-13:** Disconnect deletes the service_connections row and revokes Spotify token if applicable.
- **D-14:** Spotify tokens refreshed on-demand before API calls, not via background job.
- **D-15:** Spotify refresh: if access token expired, use refresh_token to get new access token. Update encrypted tokens in DB. If refresh fails, mark as "expired".
- **D-16:** Apple Music health check: lightweight API call (`/v1/me/storefront`) to test token validity. If 401, mark as "expired".
- **D-17:** All tokens encrypted at rest using Fernet EncryptionService (Phase 1). Decrypted only when needed.

### Claude's Discretion

- Exact Spotify PKCE implementation (secrets module for code_verifier, hashlib for S256 challenge)
- Whether to use httpx directly for Spotify token exchange or create a SpotifyOAuth helper class
- Apple Developer Token generation approach (reuse from existing MCP codebase vs new implementation)
- Test strategy for OAuth flows (mock Spotify API responses, test token encryption round-trip)
- Alembic migration if any schema changes needed beyond existing service_connections table

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SVCN-01 | User can connect their Spotify account via OAuth PKCE flow | Spotify PKCE flow documented; backend-initiated pattern confirmed viable; token exchange via httpx to `https://accounts.spotify.com/api/token` |
| SVCN-02 | User can connect their Apple Music account via MusicKit JS OAuth flow | MusicKit JS v3 `authorize()` pattern confirmed; developer token generation reuses `src/musicmind/auth.py` `AuthManager._generate_developer_token()` |
| SVCN-03 | User can disconnect a connected service | DELETE endpoint deletes `service_connections` row; Spotify has no revocation API — delete row only, note limitation |
| SVCN-04 | User can see which services are connected and their connection status | GET /api/services returns list with status derived from `token_expires_at` vs `utcnow()` and health-check result |
| SVCN-05 | User is prompted to re-authenticate when Apple Music token expires | Health check via `/v1/me/storefront`; 401 sets status to "expired" in DB; client reads status and surfaces re-auth prompt |
| SVCN-06 | Spotify access tokens are automatically refreshed using stored refresh token | On-demand refresh using stored encrypted `refresh_token_encrypted`; POST to `https://accounts.spotify.com/api/token` with `grant_type=refresh_token`, `refresh_token`, `client_id` |
</phase_requirements>

---

## Summary

Phase 3 delivers the OAuth connection layer for Spotify and Apple Music on top of the existing FastAPI backend. All schema infrastructure (`service_connections` table, `EncryptionService`, `get_current_user` dependency) already exists from Phases 1 and 2. This phase adds one new router module, a settings extension, and a helper class for PKCE mechanics.

The Spotify flow is backend-initiated PKCE: the backend generates `code_verifier` + `code_challenge` using Python stdlib (`secrets`, `hashlib`, `base64`), stores the verifier in Starlette's `SessionMiddleware` (already available via starlette 1.0.0 dependency), and redirects the user to Spotify's authorization URL. The callback exchanges the code for tokens via httpx to `https://accounts.spotify.com/api/token`. Tokens are Fernet-encrypted and written to `service_connections`. Refresh is on-demand using `grant_type=refresh_token` with `client_id` (no `client_secret` for PKCE).

The Apple Music flow is frontend-initiated: a backend endpoint at `/api/services/apple-music/developer-token` returns a freshly generated ES256 JWT (reusing `AuthManager` from `src/musicmind/auth.py` ported to the backend). The frontend loads MusicKit JS v3, calls `MusicKit.configure({developerToken})` then `music.authorize()`, and POSTs the resulting Music User Token to `/api/services/apple-music/connect`. The backend validates (non-empty string), encrypts, and stores it. No refresh exists; a health-check endpoint calls Apple Music `/v1/me/storefront` and marks expired on 401.

**Primary recommendation:** Build `backend/src/musicmind/api/services/` as a self-contained module mirroring the auth module structure (router.py, schemas.py, service.py). Starlette `SessionMiddleware` covers PKCE verifier storage without a Redis dependency.

---

## Standard Stack

### Core (all already in backend/pyproject.toml)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.135.2 | Router for service endpoints | Already used; consistent with auth module pattern |
| httpx | 0.28.1 | Async HTTP client for Spotify token exchange and Apple Music health check | Already in project; used in MCP codebase for Apple Music API |
| cryptography (Fernet) | >=42.0 | Token encryption/decryption at rest | Phase 1 `EncryptionService` — already established |
| pyjwt[crypto] | >=2.12.1 | ES256 JWT for Apple Developer Token | Already in backend; used in existing `src/musicmind/auth.py` |
| SQLAlchemy Core | >=2.0 | service_connections CRUD queries | Established pattern from Phase 1+2 |
| starlette SessionMiddleware | 1.0.0 (ships with FastAPI) | Server-side PKCE state storage across redirect | Already available as starlette transitive dep |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| secrets (stdlib) | Python 3.14 | Generate cryptographically random code_verifier | PKCE step 1 |
| hashlib (stdlib) | Python 3.14 | SHA-256 hash for S256 code_challenge | PKCE step 1 |
| base64 (stdlib) | Python 3.14 | URL-safe base64 encoding for challenge and state | PKCE step 1 |
| datetime (stdlib) | Python 3.14 | token_expires_at calculation from `expires_in` | Token storage |

### No New Dependencies Required

No new packages need to be added to `pyproject.toml`. All required libraries are already installed. `SessionMiddleware` is part of starlette 1.0.0 which ships with FastAPI.

**Version verification (checked 2026-03-26):**
- fastapi: 0.135.2 (confirmed via `uv run python -c "import fastapi; print(fastapi.__version__)"`)
- httpx: 0.28.1 (confirmed via `uv run python -c "import httpx; print(httpx.__version__)"`)
- starlette: 1.0.0 (confirmed via `uv run python -c "import starlette; print(starlette.__version__)"`)

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| starlette SessionMiddleware | Redis + signed state token in URL | Redis adds infrastructure dependency; SessionMiddleware is zero-cost and already available |
| httpx directly | Spotipy | Spotipy is sync-only; httpx matches existing async codebase pattern |
| Port AuthManager from MCP | New implementation | MCP `AuthManager._generate_developer_token()` is already tested and correct; port is 20 lines |

---

## Architecture Patterns

### Recommended Project Structure

```
backend/src/musicmind/
├── api/
│   ├── router.py              # Add: include_router(services_router)
│   └── services/
│       ├── __init__.py
│       ├── router.py          # All 5 service endpoints
│       ├── schemas.py         # Request/response Pydantic models
│       └── service.py         # SpotifyOAuth helper, Apple Music token helper, DB ops
├── config.py                  # Add: spotify_client_id, spotify_client_secret,
│                              #      spotify_redirect_uri, apple_team_id,
│                              #      apple_key_id, apple_private_key_path
└── app.py                     # Add: SessionMiddleware
```

### Pattern 1: Spotify PKCE — Code Verifier + Challenge Generation

**What:** Python stdlib generates the PKCE pair. The verifier is stored server-side in the Starlette session before the redirect.
**When to use:** On POST /api/services/spotify/connect initiation.

```python
# Source: https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow
import base64
import hashlib
import secrets

def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for Spotify PKCE flow."""
    code_verifier = secrets.token_urlsafe(64)  # 86-char URL-safe string
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge
```

### Pattern 2: Spotify PKCE — Storing State in Session

**What:** Starlette `SessionMiddleware` provides `request.session` dict. Store `code_verifier` and a random `state` before redirect.
**When to use:** On the connect initiation endpoint. Read back on the callback endpoint.

```python
# Source: Starlette SessionMiddleware docs
# In router.py connect endpoint:
code_verifier, code_challenge = generate_pkce_pair()
state = secrets.token_urlsafe(16)
request.session["spotify_code_verifier"] = code_verifier
request.session["spotify_state"] = state
# Then redirect to Spotify authorize URL
```

**Critical requirement:** `SessionMiddleware` must be added to the FastAPI app in `app.py`. Use `jwt_secret_key` as the session secret to avoid adding a new config field:

```python
# In app.py
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(SessionMiddleware, secret_key=_settings.jwt_secret_key)
```

### Pattern 3: Spotify Token Exchange (httpx)

**What:** Async POST to Spotify token endpoint exchanging authorization code for tokens.
**When to use:** In the OAuth callback endpoint.

```python
# Source: https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow
async with httpx.AsyncClient() as client:
    resp = await client.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": settings.spotify_redirect_uri,
            "client_id": settings.spotify_client_id,
            "code_verifier": code_verifier,
        },
    )
    resp.raise_for_status()
    token_data = resp.json()
    # token_data: {access_token, token_type, expires_in, refresh_token, scope}
```

### Pattern 4: Spotify Token Refresh (on-demand)

**What:** When `token_expires_at` is in the past, refresh before use. For PKCE, `client_id` is required, `client_secret` is NOT included.
**When to use:** As a helper called before any Spotify API call (Phase 4+).

```python
# Source: https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens
async def refresh_spotify_token(
    user_id: str, engine, encryption: EncryptionService, settings: Settings
) -> str | None:
    """Refresh Spotify access token. Returns new access_token or None on failure."""
    # 1. Load encrypted refresh_token from DB
    # 2. Decrypt
    # 3. POST to token endpoint
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": decrypted_refresh_token,
                "client_id": settings.spotify_client_id,
                # NO client_secret for PKCE refresh
            },
        )
    # 4. On 4xx: mark connection as "expired", return None
    # 5. On success: re-encrypt and update DB, return new access_token
```

### Pattern 5: Apple Developer Token Generation (reusing MCP auth.py)

**What:** Port `AuthManager._generate_developer_token()` into the backend service layer. The logic is 15 lines of PyJWT + ES256.
**When to use:** In `GET /api/services/apple-music/developer-token` endpoint.

```python
# Source: src/musicmind/auth.py (existing MCP codebase)
import time
import jwt

def generate_apple_developer_token(
    team_id: str, key_id: str, private_key: str
) -> str:
    """Generate ES256-signed JWT for MusicKit JS initialization."""
    now = int(time.time())
    TOKEN_EXPIRY = 15_777_000  # 6 months (Apple's maximum)
    payload = {"iss": team_id, "iat": now, "exp": now + TOKEN_EXPIRY}
    headers = {"alg": "ES256", "kid": key_id}
    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
```

The private key is read from disk at `settings.apple_private_key_path`. The key should be read once at request time (or cached with a short TTL), not stored in memory.

### Pattern 6: Apple Music Health Check

**What:** Lightweight API call to detect expired Music User Token. Returns 401 when token is invalid.
**When to use:** On GET /api/services (for Apple Music status), or before Apple Music API calls in later phases.

```python
# Source: Apple Music API docs, existing src/musicmind/client.py patterns
async def check_apple_music_token(
    music_user_token: str, developer_token: str
) -> bool:
    """Return True if token is valid, False if expired (401)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.music.apple.com/v1/me/storefront",
            headers={
                "Authorization": f"Bearer {developer_token}",
                "Music-User-Token": music_user_token,
            },
        )
    return resp.status_code != 401
```

### Pattern 7: service_connections DB Operations

The `service_connections` table has a `UniqueConstraint("user_id", "service")`. Use `INSERT ... ON CONFLICT DO UPDATE` (PostgreSQL upsert) for connect operations to handle reconnects cleanly.

```python
# Source: SQLAlchemy Core docs, existing auth/router.py patterns
from sqlalchemy.dialects.postgresql import insert as pg_insert

stmt = pg_insert(service_connections).values(
    user_id=user_id,
    service="spotify",
    access_token_encrypted=encryption.encrypt(access_token),
    refresh_token_encrypted=encryption.encrypt(refresh_token),
    token_expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
    service_user_id=spotify_user_id,
    connected_at=datetime.now(UTC),
).on_conflict_do_update(
    constraint="uq_user_service",
    set_={
        "access_token_encrypted": ...,
        "refresh_token_encrypted": ...,
        "token_expires_at": ...,
        "service_user_id": ...,
        "connected_at": ...,
    },
)
```

### Pattern 8: Settings Extension

Add these fields to `backend/src/musicmind/config.py` Settings class. All have `None` defaults since they're optional (not all deployments need Spotify):

```python
# Spotify OAuth
spotify_client_id: str | None = None
spotify_client_secret: str | None = None
spotify_redirect_uri: str = "http://127.0.0.1:8000/api/services/spotify/callback"

# Apple Music (for developer token generation on backend)
apple_team_id: str | None = None
apple_key_id: str | None = None
apple_private_key_path: str | None = None
```

Note: `spotify_redirect_uri` defaults to `127.0.0.1` (not `localhost`) — required by Spotify's November 2025 HTTPS rules (loopback IPs are the only HTTP exception).

### Anti-Patterns to Avoid

- **Storing code_verifier in the database:** It's a one-time value valid only for the duration of the OAuth flow. Session is the right scope.
- **Using `localhost` in redirect URI:** Spotify requires `https://` for all redirect URIs except loopback IPs (`127.0.0.1`). `http://localhost` is not accepted.
- **Including `client_secret` in PKCE token refresh:** PKCE refresh uses only `client_id` + `refresh_token`. Including `client_secret` causes a 400 error.
- **Trying to revoke Spotify tokens server-side:** Spotify has no token revocation API. Disconnect = delete the local DB row only.
- **Implementing Apple Music auth as server-side redirect:** MusicKit JS `authorize()` is browser-only. There is no server-side OAuth redirect for Apple Music. The frontend initiates the flow.
- **Exposing the Apple .p8 private key to the frontend:** The developer token is generated server-side from the private key. Only the JWT (not the key) is sent to the frontend.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| PKCE code_verifier generation | Custom random string logic | `secrets.token_urlsafe(64)` | stdlib is cryptographically secure; custom implementations risk weak entropy |
| SHA-256 challenge | Manual bytes manipulation | `hashlib.sha256(...).digest()` + `base64.urlsafe_b64encode` | One-liner, correct spec implementation |
| ES256 JWT for Apple | Custom JWT signing | `jwt.encode(payload, private_key, algorithm="ES256", headers=headers)` (pyjwt) | Already proven in MCP codebase |
| Session state storage | Custom encrypted cookie or DB table | `starlette.middleware.sessions.SessionMiddleware` | Ships with FastAPI, zero-config, single request lifecycle |
| Spotify HTTP client | Spotipy wrapper | `httpx.AsyncClient` directly | Spotipy is sync-only; httpx already in project |
| Token encryption | Custom AES | `EncryptionService` from Phase 1 | Already built, tested, and battle-hardened |
| UPSERT for service_connections | INSERT + SELECT + UPDATE | `pg_insert().on_conflict_do_update()` | Table has `uq_user_service` constraint; upsert handles reconnects atomically |

**Key insight:** Every non-trivial building block already exists in this project. The main work is wiring them together in a new router module, not building new utilities.

---

## Common Pitfalls

### Pitfall 1: `localhost` in Spotify Redirect URI
**What goes wrong:** OAuth flow returns `INVALID_CLIENT: Redirect URI mismatch` with no obvious cause.
**Why it happens:** Spotify's November 2025 update requires HTTPS for all redirect URIs. The only HTTP exception is loopback IPs (`127.0.0.1`). `http://localhost` is not a loopback IP per Spotify's matcher.
**How to avoid:** Use `http://127.0.0.1:8000/api/services/spotify/callback` in both `Settings.spotify_redirect_uri` and the Spotify Developer Dashboard registration. These must match character-for-character.
**Warning signs:** OAuth completes user authorization but redirect fails with error parameter in URL.

### Pitfall 2: PKCE Verifier Lost Across Redirect
**What goes wrong:** Callback receives `invalid_grant` from Spotify token exchange.
**Why it happens:** Session not persisted across the OAuth redirect, or `SessionMiddleware` not added to app.
**How to avoid:** Add `SessionMiddleware` to `app.py` before the OAuth endpoint is hit. Verify `request.session["spotify_code_verifier"]` is populated in the callback handler before attempting token exchange.
**Warning signs:** `request.session` is empty in callback despite being set in the connect handler.

### Pitfall 3: `client_secret` in PKCE Refresh Request
**What goes wrong:** Token refresh returns HTTP 400 `invalid_request`.
**Why it happens:** Standard OAuth refresh requires `client_secret`; PKCE refresh requires only `client_id`. Adding both causes Spotify's server to reject the request.
**How to avoid:** PKCE refresh body: `{grant_type, refresh_token, client_id}`. No `client_secret`. No Authorization header.
**Warning signs:** Refresh works in staging (where you tested with client_secret) but fails in PKCE production flow.

### Pitfall 4: Apple Music Token Storage with Missing `service_email` Column
**What goes wrong:** INSERT to `service_connections` fails.
**Why it happens:** The CONTEXT.md canonical reference mentions `service_email` column, but the actual schema.py (verified) has no such column. The table has: `id, user_id, service, access_token_encrypted, refresh_token_encrypted, token_expires_at, service_user_id, connected_at`.
**How to avoid:** For Apple Music connect, store: `user_id`, `service="apple_music"`, `access_token_encrypted` (the Music User Token), `refresh_token_encrypted=NULL`, `token_expires_at=NULL`, `service_user_id` (can be empty string since Apple doesn't return a user ID in the MUT flow).
**Warning signs:** Runtime `sqlalchemy.exc.CompileError` or `ProgrammingError` on INSERT.

### Pitfall 5: Apple Music Health Check Returns 403 Not 401 for Expired Token
**What goes wrong:** Expired tokens marked as "connected" even after 6-month expiry.
**Why it happens:** Apple's API behavior for expired Music User Tokens may return 403 (Forbidden) in addition to or instead of 401 in some edge cases.
**How to avoid:** Treat both 401 and 403 from `/v1/me/storefront` as "expired" signal. Check `resp.status_code not in (200, 404)` as a broader expiry check, or explicitly handle `{401, 403}`.
**Warning signs:** User reports Apple Music features not working but status shows "connected".

### Pitfall 6: Status Endpoint Triggers Health Check on Every Request (Performance)
**What goes wrong:** `GET /api/services` becomes slow (2+ seconds) because it calls Apple Music API for every request.
**Why it happens:** Proactively checking token health on every status request adds one HTTP round-trip to Apple Music.
**How to avoid:** Status endpoint derives status from DB state only (`token_expires_at` for Spotify, presence of `access_token_encrypted` + in-memory last-check timestamp for Apple). Health check is called explicitly when the app needs to make an Apple Music API call, not on every status poll. The `GET /api/services` endpoint should be fast (DB-only, no external calls).
**Warning signs:** Status endpoint takes > 500ms.

### Pitfall 7: Spotify Revocation Expectation
**What goes wrong:** Plan includes a task to "call Spotify token revocation endpoint" on disconnect.
**Why it happens:** Standard OAuth 2.0 spec includes a revocation endpoint (RFC 7009). Spotify does not implement it.
**How to avoid:** Spotify disconnect = delete `service_connections` row. Do not attempt to call any Spotify revocation API. Document this limitation as a comment in the disconnect handler.
**Warning signs:** Tests fail looking for a 200 from Spotify on revocation call.

---

## Code Examples

### Verified: Spotify Authorization URL Construction

```python
# Source: https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow
import urllib.parse

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_SCOPES = "user-read-private user-read-email user-library-read user-read-recently-played user-top-read"

def build_spotify_authorize_url(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": SPOTIFY_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    return f"{SPOTIFY_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
```

### Verified: Apple Developer Token (ported from MCP src/musicmind/auth.py)

```python
# Source: src/musicmind/auth.py lines 54-71
import time
import jwt as pyjwt
from pathlib import Path

TOKEN_EXPIRY_SECONDS = 15_777_000  # 6 months

def generate_apple_developer_token(
    team_id: str, key_id: str, private_key_path: str
) -> str:
    now = int(time.time())
    payload = {"iss": team_id, "iat": now, "exp": now + TOKEN_EXPIRY_SECONDS}
    headers = {"alg": "ES256", "kid": key_id}
    private_key = Path(private_key_path).expanduser().read_text().strip()
    return pyjwt.encode(payload, private_key, algorithm="ES256", headers=headers)
```

### Verified: service_connections Table Columns (from schema.py)

```python
# Confirmed columns in service_connections (backend/src/musicmind/db/schema.py):
# id (Integer PK autoincrement)
# user_id (Text FK to users.id, indexed)
# service (Text) -- "spotify" or "apple_music"
# access_token_encrypted (Text, NOT NULL)
# refresh_token_encrypted (Text, nullable)  -- NULL for Apple Music
# token_expires_at (DateTime(timezone=True), nullable)  -- NULL for Apple Music
# service_user_id (Text, nullable)
# connected_at (DateTime(timezone=True), server_default=now())
# UniqueConstraint("user_id", "service", name="uq_user_service")
```

### Verified: Established Router Pattern (from auth/router.py)

```python
# Source: backend/src/musicmind/auth/router.py
# Services router follows same pattern:
from fastapi import APIRouter, Depends, Request
from musicmind.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/services", tags=["services"])

@router.get("")
async def list_connections(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    engine = request.app.state.engine
    encryption = request.app.state.encryption
    settings = request.app.state.settings
    # ... query service_connections for current_user["user_id"]
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Spotify implicit grant + client_secret | PKCE — no client_secret sent from frontend | November 27, 2025 | Backend must generate code_verifier; client_secret still needed for authorization code (non-PKCE) but not PKCE |
| `http://localhost` as redirect URI | `http://127.0.0.1` for local dev, `https://` for prod | November 2025 | Dev config change required |
| Spotipy as Python client | httpx directly (async) | Ongoing project decision | No new async wrapper needed |
| Server-side Apple Music OAuth | Frontend-only MusicKit JS | Apple's design (never changed) | Backend only stores and uses the token; never initiates auth |
| Spotify batch track fetch (`/tracks?ids=`) | Individual track fetch or skip (removed in Feb 2026) | February 11, 2026 | Affects Phase 5+ (library sync), NOT Phase 3 (OAuth only) |

**Deprecated/outdated:**
- Spotify implicit grant flow: removed November 2025. Do not implement.
- `http://localhost` redirect URIs in Spotify: blocked since November 2025. Use `127.0.0.1`.
- Spotify token revocation API: never existed. Disconnect = local DB deletion only.

---

## Open Questions

1. **Apple Music `service_user_id` value**
   - What we know: The `service_connections` table has a `service_user_id` column. Spotify's `/v1/me` returns the Spotify user ID, which should be stored here.
   - What's unclear: Apple Music's MusicKit JS `authorize()` returns only the Music User Token string. There is no user ID returned in the frontend flow. A separate call to `/v1/me/storefront` confirms token validity but doesn't return a stable user ID.
   - Recommendation: For Apple Music, store `service_user_id = ""` (empty string) or `NULL`. The column is nullable per schema. Add a code comment explaining the limitation.

2. **SessionMiddleware and CSRF middleware ordering**
   - What we know: `app.py` already adds `CSRFMiddleware`. `SessionMiddleware` must be added. Starlette middleware is processed in reverse registration order.
   - What's unclear: The correct order to add both so CSRF check doesn't interfere with OAuth redirect (which doesn't carry the CSRF token).
   - Recommendation: Add `SessionMiddleware` after `CSRFMiddleware` in `app.py` registration (so it runs first). The OAuth endpoints need CSRF exclusion or use GET (callback) and the state parameter as CSRF protection instead.

3. **Spotify authorization callback: GET vs POST**
   - What we know: D-11 specifies `GET /api/services/spotify/callback`. Spotify sends the authorization code via GET redirect (URL parameters).
   - What's unclear: CSRF middleware may intercept GET requests to sensitive paths.
   - Recommendation: Spotify callback must be GET (Spotify redirects via browser). Rely on the `state` parameter (stored in session and verified in callback) as the CSRF protection mechanism for this endpoint, which is standard OAuth practice. The `state` check is equivalent to CSRF protection here.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11+ | Backend | Yes | 3.14.2 | — |
| uv | Package management | Yes | 0.11.1 | — |
| Docker | PostgreSQL (dev) | Yes | 29.3.0 | — |
| FastAPI | Service endpoints | Yes | 0.135.2 | — |
| httpx | Spotify token exchange | Yes | 0.28.1 | — |
| cryptography (Fernet) | Token encryption | Yes | >=42 | — |
| pyjwt[crypto] | Apple dev token | Yes | >=2.12.1 | — |
| starlette.middleware.sessions | PKCE state storage | Yes | 1.0.0 | — |
| secrets (stdlib) | code_verifier generation | Yes | Python 3.14 | — |
| hashlib (stdlib) | S256 challenge | Yes | Python 3.14 | — |
| Spotify Developer App | OAuth credentials | NOT VERIFIED | — | Cannot test live flow; test with mocks |
| Apple Developer .p8 key | Apple dev token | NOT VERIFIED | — | Existing key from MCP setup may work |

**Missing dependencies with no fallback:**
- Live Spotify Developer App registration (client_id, client_secret, registered redirect URI): cannot be created by code. Must be done manually at developer.spotify.com before integration testing the OAuth flow end-to-end.
- Apple Developer credentials (`team_id`, `key_id`, `.p8` private key): likely already present from the existing MCP setup (`~/.config/musicmind/config.json`). Must be verified before Apple Music connect tests.

**Missing dependencies with fallback:**
- Both service credentials can be mocked in tests (mock httpx responses for Spotify; use a test ES256 key for Apple dev token generation).

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.23.x |
| Config file | `backend/pyproject.toml` `[tool.pytest.ini_options]` — `asyncio_mode = "auto"`, `testpaths = ["tests"]` |
| Quick run command | `uv run pytest tests/test_services.py -x -q` |
| Full suite command | `uv run pytest tests/ -q` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SVCN-01 | Spotify OAuth initiation returns redirect to accounts.spotify.com with correct PKCE params | unit | `uv run pytest tests/test_services.py::test_spotify_connect_redirect -x` | Wave 0 |
| SVCN-01 | Spotify callback exchanges code for tokens and stores encrypted in DB | integration | `uv run pytest tests/test_services.py::test_spotify_callback_stores_tokens -x` | Wave 0 |
| SVCN-02 | Apple Music developer-token endpoint returns valid JWT | unit | `uv run pytest tests/test_services.py::test_apple_developer_token -x` | Wave 0 |
| SVCN-02 | Apple Music connect stores encrypted Music User Token | integration | `uv run pytest tests/test_services.py::test_apple_music_connect_stores_token -x` | Wave 0 |
| SVCN-03 | Disconnect deletes service_connections row | integration | `uv run pytest tests/test_services.py::test_disconnect_removes_connection -x` | Wave 0 |
| SVCN-04 | List connections returns correct status for each service | integration | `uv run pytest tests/test_services.py::test_list_connections_status -x` | Wave 0 |
| SVCN-05 | Apple Music expired token returns "expired" status | unit | `uv run pytest tests/test_services.py::test_apple_music_expired_status -x` | Wave 0 |
| SVCN-06 | Spotify token refresh updates DB with new encrypted token | unit (mocked) | `uv run pytest tests/test_services.py::test_spotify_token_refresh -x` | Wave 0 |

**Manual-only tests:**
- Full end-to-end Spotify OAuth flow with live Spotify account (requires registered app + credentials)
- Apple Music full flow with live Apple ID (requires .p8 key and Apple developer account)

### Testing Strategy for OAuth Flows

The auth tests in Phase 2 (`test_auth.py`) establish the integration test pattern: use `httpx.AsyncClient(transport=ASGITransport(app=app))` with SQLite in-memory DB via `aiosqlite`. Apply the same pattern for services tests:
- Mock `httpx.AsyncClient.post` for Spotify token exchange (return canned token response)
- Mock `httpx.AsyncClient.get` for Apple Music health check (return 200 or 401)
- Use `conftest.py` `auth_cookies` fixture for authenticated requests
- Test PKCE pair generation as pure unit tests (no mocking needed)
- Test Fernet round-trip (encrypt → store → decrypt) as pure unit tests

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_services.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_services.py` — all 8 test cases above; covers SVCN-01 through SVCN-06
- [ ] `tests/test_pkce_helpers.py` — pure unit tests for `generate_pkce_pair()` and `build_spotify_authorize_url()` (optional, can be in test_services.py)

---

## Sources

### Primary (HIGH confidence)
- Spotify PKCE docs (https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow) — PKCE flow, code_verifier spec, token exchange params
- Spotify token refresh docs (https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens) — PKCE refresh requires `client_id`, no `client_secret`
- Existing codebase `src/musicmind/auth.py` — ES256 JWT generation verified in production
- Existing codebase `backend/src/musicmind/db/schema.py` — `service_connections` table columns verified
- Existing codebase `backend/src/musicmind/auth/router.py` — router pattern to replicate
- Starlette SessionMiddleware docs (ships with starlette 1.0.0, confirmed installed)
- Project PITFALLS.md — Pitfall 6 (PKCE/HTTPS), Pitfall 10 (MusicKit JS browser-only), Pitfall 4 (Apple token no refresh)
- Project STACK.md — Spotify PKCE mandatory Nov 2025, httpx over Spotipy recommendation

### Secondary (MEDIUM confidence)
- WebSearch: Spotify has no token revocation endpoint (confirmed via multiple GitHub issues dating to 2015–2026, no official revocation doc exists)
- WebSearch: MusicKit JS v3 `configure()` + `authorize()` pattern — multiple developer blogs + Apple developer forums confirm browser-only flow
- WebSearch: Apple Music `/v1/me/storefront` health check pattern — community-confirmed lightweight endpoint for token validation

### Tertiary (LOW confidence)
- Apple Music 401 vs 403 on expired token: community reports suggest both status codes possible — flagged in Pitfall 5 for conservative handling

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all dependencies already installed and version-verified in the running backend
- Architecture: HIGH — all patterns drawn from official Spotify docs and existing codebase code already in production use
- Pitfalls: HIGH for Pitfalls 1-3 (official docs), MEDIUM for Pitfalls 4-7 (community + reasoning)
- Token refresh PKCE mechanics: HIGH — fetched from official Spotify docs directly

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (Spotify API changes frequently; re-verify redirect URI and refresh token behavior if blocked)
