# Phase 2: User Accounts - Research

**Researched:** 2026-03-26
**Domain:** Authentication, session management, CSRF protection (FastAPI + PostgreSQL)
**Confidence:** HIGH

## Summary

Phase 2 implements user account management on top of the existing FastAPI backend with PostgreSQL. The codebase already has a `users` table with `id`, `email`, `password_hash`, `display_name`, `created_at`, and `updated_at` columns. The phase adds JWT-based authentication with httpOnly cookies, bcrypt password hashing, refresh token rotation with database-backed revocation, and double-submit CSRF protection.

The technology choices are well-established: PyJWT 2.12.1 for JWT encoding/decoding (actively maintained, python-jose is abandoned), the `bcrypt` library directly (passlib is unmaintained and broken on Python 3.13+), and `starlette-csrf` 3.0.0 for the double-submit cookie CSRF pattern. All three integrate cleanly with the existing FastAPI + SQLAlchemy Core + asyncpg stack.

**Primary recommendation:** Use PyJWT + bcrypt (direct) + starlette-csrf. Add a `refresh_tokens` table for revocation support. Build auth as a self-contained `backend/src/musicmind/auth/` package with router, service, dependencies, and schemas modules.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** JWT access tokens delivered in httpOnly secure cookies (not localStorage). This prevents XSS from accessing tokens.
- **D-02:** Access token lifetime: 30 minutes. Refresh token lifetime: 7 days. Refresh tokens stored in database for revocation capability.
- **D-03:** On login, set both access and refresh tokens as httpOnly, secure, SameSite=Lax cookies.
- **D-04:** On logout, clear both cookies and invalidate refresh token in database.
- **D-05:** Password hashing via bcrypt. Industry standard, sufficient for friend-group scale.
- **D-06:** Minimum password length: 8 characters. No other complexity rules (friend group, not enterprise).
- **D-07:** Passwords stored as bcrypt hashes in the `users` table `password_hash` column.
- **D-08:** Double-submit cookie pattern. Backend generates a CSRF token, sets it as a non-httpOnly cookie (readable by JS). Frontend sends it back in X-CSRF-Token header. Backend validates match.
- **D-09:** CSRF validation on all state-changing endpoints (POST, PUT, DELETE). GET endpoints exempt.
- **D-10:** JWT contains: user_id, email, issued_at, expires_at. Signed with a secret key from Settings.
- **D-11:** Refresh flow: when access token expires, frontend calls /auth/refresh with refresh cookie. Backend validates refresh token against database, issues new access token.
- **D-12:** On browser close + reopen, refresh cookie persists (7-day expiry), user stays logged in.
- **D-13:** Email + password signup. No email verification in v1 (friend group -- trust the users).
- **D-14:** Duplicate email check returns generic error ("Account creation failed") to prevent email enumeration.

### Claude's Discretion
- Exact API endpoint paths (recommend: /api/auth/signup, /api/auth/login, /api/auth/logout, /api/auth/refresh, /api/auth/me)
- JWT library choice (recommend: PyJWT -- see research below)
- Whether to add a minimal login/signup frontend page or backend-only API in this phase
- Test fixture patterns for authenticated requests

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ACCT-01 | User can create an account with email and password | PyJWT for token generation, bcrypt for password hashing, SQLAlchemy Core insert to users table, Pydantic schemas for request validation |
| ACCT-02 | User can log in and stay logged in across browser sessions (JWT) | PyJWT HS256 access tokens (30min) + refresh tokens (7d) in httpOnly cookies, refresh_tokens DB table for persistence and revocation |
| ACCT-03 | User can log out from any page | Cookie deletion via Response.delete_cookie(), refresh token invalidation in DB |
| ACCT-04 | User session is secure (httpOnly cookies, CSRF protection) | starlette-csrf 3.0.0 middleware for double-submit pattern, httpOnly+Secure+SameSite=Lax cookie flags |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

These directives from CLAUDE.md constrain implementation choices:

- **`from __future__ import annotations`** at the top of every module
- **`X | None`** instead of `Optional[X]` for type hints
- **Ruff** for linting/formatting: line-length 100, select rules E/F/I/N/W/UP, target py311
- **snake_case.py** for all Python modules, `test_` prefix for test files
- **PascalCase** for classes, **snake_case** for functions/variables
- **UPPER_SNAKE_CASE** for constants
- **Pydantic BaseModel** with `Field(description=...)` for request/response schemas
- **SQLAlchemy Core** (no ORM) for all database operations
- **Async everywhere** for I/O operations
- **Type hints** on all functions including `-> None`
- **server_default only** (no mutable `default=`) for PostgreSQL column defaults
- **Keyword-only arguments** after `*` for optional config parameters
- **`pytest-asyncio`** with `asyncio_mode = "auto"` for async tests
- **Never use `print()`** or write to stdout
- **No `.env` files in git** -- env vars loaded via MUSICMIND_ prefix
- **Every `.py` file** has a top-level docstring
- **Logging to stderr only** via `logging.getLogger(__name__)`

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyJWT | 2.12.1 | JWT encode/decode for access + refresh tokens | Actively maintained (March 2026 release), FastAPI community moved away from abandoned python-jose. HS256 signing is all we need. |
| bcrypt | 5.0.0 | Password hashing and verification | Direct library -- passlib is unmaintained since 2020 and broken on Python 3.13+. bcrypt 5.0.0 is current, works with Python 3.14. |
| starlette-csrf | 3.0.0 | Double-submit cookie CSRF middleware | Made by same author as FastAPI-Users (frankie567). Implements exactly the pattern in D-08. Works as Starlette middleware, plugs into FastAPI. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| uuid (stdlib) | Python 3.14 | UUID7 generation for user IDs | Use `uuid.uuid7()` for time-ordered user IDs. Available natively in Python 3.14.2 (confirmed in venv). |
| itsdangerous | 2.2.0 | Transitive dependency of starlette-csrf | Automatically installed. Not used directly. |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| PyJWT | python-jose | python-jose is unmaintained (last meaningful release years ago). FastAPI community explicitly moved to PyJWT. No reason to use jose. |
| bcrypt (direct) | passlib[bcrypt] | passlib last released 2020, broken on Python 3.13+, causes ValueError with bcrypt 5.0.0. Direct bcrypt is simpler and maintained. |
| bcrypt (direct) | pwdlib | pwdlib is newer (Oct 2025) and has a cleaner API, but still beta. bcrypt is proven and the user decision D-05 specifies bcrypt. |
| starlette-csrf | fastapi-csrf-protect | fastapi-csrf-protect works but starlette-csrf is simpler, has fewer config options to get wrong, and is from a well-known FastAPI ecosystem author. |
| starlette-csrf | Hand-rolled CSRF | Never hand-roll CSRF. Timing attacks, token comparison bugs, cookie scope bugs are common. |

**Installation:**
```bash
cd backend && uv add PyJWT bcrypt starlette-csrf
```

**Version verification:** All three versions confirmed via `uv pip install --dry-run` against the venv's Python 3.14.2:
- PyJWT 2.12.1 (released 2026-03-13)
- bcrypt 5.0.0 (current stable)
- starlette-csrf 3.0.0 (current stable)

## Architecture Patterns

### Recommended Project Structure
```
backend/src/musicmind/
  auth/                    # NEW -- auth package
    __init__.py
    router.py              # FastAPI APIRouter with auth endpoints
    service.py             # Business logic: hash, verify, create/validate tokens
    dependencies.py        # FastAPI Depends() for current_user extraction
    schemas.py             # Pydantic request/response models
  api/
    router.py              # Updated to include auth router
  config.py                # Updated with jwt_secret_key, jwt_algorithm
  db/
    schema.py              # Updated with refresh_tokens table
  security/
    encryption.py          # Existing -- used for encrypting refresh tokens at rest
```

### Pattern 1: Auth Service Layer
**What:** A single `AuthService` class encapsulating all auth logic (password hashing, JWT creation, token validation, refresh flow).
**When to use:** Always -- keeps router thin, service testable.
**Example:**
```python
# backend/src/musicmind/auth/service.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from musicmind.config import Settings

# Constants
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_access_token(
    user_id: str,
    email: str,
    *,
    secret_key: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT access token."""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, secret_key, algorithm=JWT_ALGORITHM)


def create_refresh_token(
    user_id: str,
    *,
    secret_key: str,
    expires_delta: timedelta | None = None,
) -> tuple[str, str]:
    """Create a refresh token. Returns (jwt_token, token_id) for DB storage."""
    token_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    payload = {
        "sub": user_id,
        "jti": token_id,
        "iat": now,
        "exp": expire,
        "type": "refresh",
    }
    token = jwt.encode(payload, secret_key, algorithm=JWT_ALGORITHM)
    return token, token_id
```

### Pattern 2: FastAPI Dependency for Current User
**What:** A `Depends()` callable that extracts and validates the JWT from the cookie, returning the current user.
**When to use:** On every protected endpoint.
**Example:**
```python
# backend/src/musicmind/auth/dependencies.py
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
import jwt

from musicmind.config import Settings


async def get_current_user(request: Request) -> dict:
    """Extract current user from access token cookie.

    Returns dict with user_id and email.
    Raises 401 if token is missing, expired, or invalid.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    settings: Settings = request.app.state.settings
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
        )
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )
        return {"user_id": payload["sub"], "email": payload["email"]}
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
```

### Pattern 3: Cookie-Based Token Setting
**What:** Setting JWT tokens as httpOnly cookies on Response objects.
**When to use:** In login, signup, and refresh endpoints.
**Example:**
```python
# In router.py endpoint
from fastapi import Response

def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
) -> None:
    """Set access and refresh tokens as httpOnly cookies."""
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,       # Requires HTTPS in production
        samesite="lax",
        max_age=30 * 60,   # 30 minutes
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,  # 7 days
        path="/api/auth/refresh",    # Restrict to refresh endpoint only
    )
```

### Pattern 4: Refresh Token Database Table
**What:** A `refresh_tokens` table storing token IDs for revocation.
**When to use:** Required by D-02 and D-04.
**Schema addition:**
```python
# Added to backend/src/musicmind/db/schema.py
refresh_tokens = sa.Table(
    "refresh_tokens",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),           # JWT jti claim
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "expires_at",
        sa.DateTime(timezone=True),
        nullable=False,
    ),
    sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)
```

### Anti-Patterns to Avoid
- **Storing JWTs in localStorage:** Decision D-01 explicitly forbids this. httpOnly cookies only.
- **Using passlib for bcrypt:** passlib is unmaintained and broken on Python 3.13+. Use bcrypt directly.
- **Skipping CSRF on cookie auth:** Cookies are sent automatically by the browser. Without CSRF protection, any page can submit requests on behalf of the user.
- **Access token in refresh cookie path:** The refresh cookie should be scoped to `/api/auth/refresh` only, not `/`. This limits exposure.
- **JWT containing sensitive data:** The JWT payload is base64-encoded (not encrypted). Only include user_id and email, never passwords or secrets.
- **Comparing CSRF tokens with `==`:** Use `hmac.compare_digest()` for constant-time comparison. starlette-csrf handles this internally.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CSRF protection | Custom middleware comparing tokens | starlette-csrf 3.0.0 | Timing attacks in string comparison, cookie scope bugs, forgetting to exempt safe methods |
| Password hashing | Custom bcrypt wrapper with salt management | bcrypt library directly | Salt is embedded in the hash by bcrypt. Hand-rolling salt management is a common source of bugs. |
| JWT validation | Manual base64 decode + signature check | PyJWT `jwt.decode()` | Clock skew, algorithm confusion attacks, key type validation are all handled by PyJWT |
| Cookie security flags | Manual header construction | FastAPI `Response.set_cookie()` | Missing flags, encoding issues, path scoping errors |

**Key insight:** Authentication is the one area where "simple enough to hand-roll" is always wrong. Every layer (hashing, signing, CSRF, cookie flags) has non-obvious security pitfalls that libraries handle correctly.

## Common Pitfalls

### Pitfall 1: bcrypt bytes vs. strings
**What goes wrong:** bcrypt functions require bytes, not strings. Passing a string directly causes TypeError.
**Why it happens:** Python 3 strings are unicode, bcrypt operates on bytes.
**How to avoid:** Always `.encode()` before hashing and `.decode()` after: `bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()`.
**Warning signs:** TypeError in password hash/verify functions.

### Pitfall 2: JWT "alg" confusion
**What goes wrong:** If `algorithms` parameter is not specified in `jwt.decode()`, an attacker can change the algorithm header to "none" and bypass signature verification.
**Why it happens:** JWT spec allows algorithm to be specified in the token header itself.
**How to avoid:** Always pass `algorithms=["HS256"]` explicitly to `jwt.decode()`. Never trust the token's own algorithm claim.
**Warning signs:** Missing `algorithms` parameter in any `jwt.decode()` call.

### Pitfall 3: Refresh token not scoped to refresh endpoint
**What goes wrong:** If the refresh token cookie has `path="/"`, it is sent with every request, increasing exposure surface.
**Why it happens:** Default cookie path is `/`.
**How to avoid:** Set `path="/api/auth/refresh"` on the refresh token cookie.
**Warning signs:** Refresh token appearing in non-refresh request cookies.

### Pitfall 4: CSRF cookie must NOT be httpOnly
**What goes wrong:** If the CSRF cookie is httpOnly, JavaScript cannot read it to send in the X-CSRF-Token header.
**Why it happens:** Defaulting all cookies to httpOnly for "security".
**How to avoid:** The CSRF cookie is the one cookie that MUST be readable by JavaScript. starlette-csrf handles this correctly by default.
**Warning signs:** CSRF validation always failing because frontend cannot read the token.

### Pitfall 5: Email enumeration via signup/login responses
**What goes wrong:** Different error messages for "email exists" vs "wrong password" reveal whether an email is registered.
**Why it happens:** Natural instinct to give helpful error messages.
**How to avoid:** Decision D-14 requires generic "Account creation failed" / "Invalid credentials" messages. Same response shape for all auth failures.
**Warning signs:** Different HTTP status codes or error messages for different failure modes.

### Pitfall 6: Forgetting to add jwt_secret_key to Settings
**What goes wrong:** The JWT secret defaults to something weak or crashes at startup.
**Why it happens:** Settings class needs updating but the new field is overlooked.
**How to avoid:** Add `jwt_secret_key: str` to Settings with no default (forces explicit configuration via MUSICMIND_JWT_SECRET_KEY env var).
**Warning signs:** App starts with a hardcoded or empty JWT secret.

### Pitfall 7: secure=True cookies in local development
**What goes wrong:** Cookies are not sent over plain HTTP in local dev, making auth appear broken.
**Why it happens:** `secure=True` requires HTTPS. Local dev uses HTTP.
**How to avoid:** Make `secure` flag configurable based on `settings.debug`. When `debug=True`, set `secure=False`. Add a comment explaining this is development-only.
**Warning signs:** Auth works in no environment, cookies silently not set.

### Pitfall 8: Missing Alembic migration for refresh_tokens table
**What goes wrong:** New table defined in schema.py but not created in database.
**Why it happens:** Alembic autogenerate only runs when explicitly invoked.
**How to avoid:** Create migration 002 adding the refresh_tokens table. Test with `alembic upgrade head`.
**Warning signs:** "relation refresh_tokens does not exist" errors at runtime.

## Code Examples

Verified patterns from the existing codebase and official library documentation:

### Setting Config for JWT
```python
# backend/src/musicmind/config.py (additions)
class Settings(BaseSettings):
    # ... existing fields ...
    jwt_secret_key: str  # No default -- MUST be set via MUSICMIND_JWT_SECRET_KEY
    jwt_algorithm: str = "HS256"
```

### Signup Endpoint Pattern
```python
# backend/src/musicmind/auth/router.py
@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    request: Request,
    response: Response,
    body: SignupRequest,
) -> dict:
    """Create a new user account."""
    engine = request.app.state.engine
    settings = request.app.state.settings

    password_hash = hash_password(body.password)
    user_id = str(uuid.uuid7())

    async with engine.begin() as conn:
        # Check for duplicate email
        existing = await conn.execute(
            sa.select(users.c.id).where(users.c.email == body.email)
        )
        if existing.first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Account creation failed",  # Generic per D-14
            )

        await conn.execute(
            users.insert().values(
                id=user_id,
                email=body.email,
                password_hash=password_hash,
                display_name=body.display_name or body.email.split("@")[0],
            )
        )

    # Issue tokens
    access_token = create_access_token(user_id, body.email, secret_key=settings.jwt_secret_key)
    refresh_token, token_id = create_refresh_token(user_id, secret_key=settings.jwt_secret_key)

    # Store refresh token in DB
    async with engine.begin() as conn:
        await conn.execute(
            refresh_tokens.insert().values(
                id=token_id,
                user_id=user_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
        )

    set_auth_cookies(response, access_token, refresh_token)
    return {"user_id": user_id, "email": body.email}
```

### Pydantic Request/Response Schemas
```python
# backend/src/musicmind/auth/schemas.py
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    """Signup request body."""

    email: str = Field(description="User email address")
    password: str = Field(min_length=8, description="Password (minimum 8 characters)")
    display_name: str | None = Field(default=None, description="Display name (optional)")


class LoginRequest(BaseModel):
    """Login request body."""

    email: str = Field(description="User email address")
    password: str = Field(description="User password")


class UserResponse(BaseModel):
    """User info returned from /me endpoint."""

    user_id: str = Field(description="User ID")
    email: str = Field(description="User email")
    display_name: str = Field(description="Display name")
```

### Test Fixture for Authenticated Requests
```python
# backend/tests/conftest.py (additions)
import uuid

import jwt


@pytest.fixture
def test_user_id() -> str:
    """Generate a test user ID."""
    return str(uuid.uuid4())


@pytest.fixture
def auth_cookies(test_user_id: str) -> dict[str, str]:
    """Create valid auth cookies for testing."""
    secret = "test-secret-key-for-testing-only"
    now = datetime.now(timezone.utc)
    access_token = jwt.encode(
        {
            "sub": test_user_id,
            "email": "test@example.com",
            "iat": now,
            "exp": now + timedelta(minutes=30),
            "type": "access",
        },
        secret,
        algorithm="HS256",
    )
    return {"access_token": access_token}
```

### starlette-csrf Integration
```python
# backend/src/musicmind/app.py (addition)
from starlette_csrf import CSRFMiddleware

app.add_middleware(
    CSRFMiddleware,
    secret=settings.jwt_secret_key,  # Reuse JWT secret or use separate CSRF secret
    sensitive_cookies={"access_token", "refresh_token"},
    cookie_name="csrftoken",
    header_name="x-csrf-token",
)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| passlib[bcrypt] | bcrypt library directly | 2024-2025 (passlib abandoned) | passlib broken on Python 3.13+. Use bcrypt 5.x directly. |
| python-jose | PyJWT | 2024-2025 (jose abandoned) | FastAPI docs and community migrated to PyJWT. |
| UUID4 for user IDs | UUID7 for user IDs | Python 3.14 (2025) | `uuid.uuid7()` is stdlib. Time-ordered, better DB index locality. |
| Custom CSRF middleware | starlette-csrf | 2024 (v3.0.0) | Maintained by FastAPI-Users author. Handles timing-safe comparison, cookie scoping. |

**Deprecated/outdated:**
- **passlib:** Last release 2020. Broken with bcrypt 5.0.0 and Python 3.13+. Do not use.
- **python-jose:** Barely maintained. FastAPI community explicitly recommends PyJWT instead.
- **fastapi-jwt-auth:** Abandoned package. Use PyJWT directly + FastAPI Depends().

## Open Questions

1. **Frontend in this phase or API-only?**
   - What we know: CONTEXT.md lists this as Claude's Discretion. Phase description says "user can create accounts, log in, stay logged in, log out."
   - What's unclear: Whether success criteria require visual pages or just working API endpoints.
   - Recommendation: Build API-only in this phase. Frontend pages belong in a later UI phase. Verify auth works via tests and manual curl/httpx calls.

2. **Secure cookie flag in development**
   - What we know: `secure=True` requires HTTPS. Docker-compose exposes port 8000 over HTTP.
   - What's unclear: Whether there is an HTTPS reverse proxy planned for local dev.
   - Recommendation: Gate `secure` flag on `settings.debug`. When debug=True, cookies use secure=False. Document this clearly.

3. **User ID generation: uuid4 vs uuid7**
   - What we know: The users.id column is `sa.Text`, no UUID format enforced. Python 3.14 has `uuid.uuid7()` natively.
   - What's unclear: Whether existing code or future phases assume any ID format.
   - Recommendation: Use `uuid.uuid7()` for new user IDs. Time-ordered, better for DB indexing, and available natively in the project's Python 3.14.2 runtime.

4. **display_name field on signup**
   - What we know: The `users` table has a `display_name` column (NOT NULL). CONTEXT.md decisions do not mention display_name.
   - What's unclear: Whether signup requires it or can default.
   - Recommendation: Make it optional on signup with default = email username (everything before @). This matches the friend-group context.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | All code | Yes | 3.14.2 | -- |
| Docker | PostgreSQL container | Yes | 29.3.0 | -- |
| PostgreSQL (via Docker) | Data layer | Yes (docker-compose) | 16-alpine | -- |
| uv | Package management | Yes | 0.11.1 | -- |
| pg_isready (CLI) | Health checks | No | -- | Docker healthcheck handles it |

**Missing dependencies with no fallback:**
- None. All required infrastructure is available.

**Missing dependencies with fallback:**
- `pg_isready` CLI not installed locally, but PostgreSQL connectivity is handled by Docker healthcheck and the existing `/health` endpoint.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ with pytest-asyncio 0.23+ |
| Config file | `backend/pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `cd backend && uv run pytest tests/ -x -q` |
| Full suite command | `cd backend && uv run pytest tests/ -v` |

### Phase Requirements --> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ACCT-01 | Signup creates user, returns tokens in cookies | integration | `cd backend && uv run pytest tests/test_auth.py::test_signup_creates_user -x` | No -- Wave 0 |
| ACCT-01 | Signup with duplicate email returns generic error | integration | `cd backend && uv run pytest tests/test_auth.py::test_signup_duplicate_email -x` | No -- Wave 0 |
| ACCT-01 | Signup with short password is rejected | unit | `cd backend && uv run pytest tests/test_auth.py::test_signup_password_validation -x` | No -- Wave 0 |
| ACCT-02 | Login returns access + refresh cookies | integration | `cd backend && uv run pytest tests/test_auth.py::test_login_sets_cookies -x` | No -- Wave 0 |
| ACCT-02 | Refresh endpoint issues new access token | integration | `cd backend && uv run pytest tests/test_auth.py::test_refresh_token_flow -x` | No -- Wave 0 |
| ACCT-02 | Expired access token returns 401 | unit | `cd backend && uv run pytest tests/test_auth.py::test_expired_token_rejected -x` | No -- Wave 0 |
| ACCT-03 | Logout clears cookies and revokes refresh token | integration | `cd backend && uv run pytest tests/test_auth.py::test_logout_clears_session -x` | No -- Wave 0 |
| ACCT-03 | Revoked refresh token cannot be reused | integration | `cd backend && uv run pytest tests/test_auth.py::test_revoked_refresh_rejected -x` | No -- Wave 0 |
| ACCT-04 | Access token cookie is httpOnly | integration | `cd backend && uv run pytest tests/test_auth.py::test_cookie_security_flags -x` | No -- Wave 0 |
| ACCT-04 | CSRF token is set and validated on POST | integration | `cd backend && uv run pytest tests/test_auth.py::test_csrf_protection -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `cd backend && uv run pytest tests/ -x -q`
- **Per wave merge:** `cd backend && uv run pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `backend/tests/test_auth.py` -- covers ACCT-01 through ACCT-04 (signup, login, logout, refresh, security)
- [ ] `backend/tests/test_auth_service.py` -- unit tests for password hashing, token creation/validation
- [ ] Update `backend/tests/conftest.py` -- add auth fixtures (test user, auth cookies, app with JWT config)
- [ ] Install test dependencies: `cd backend && uv add PyJWT bcrypt starlette-csrf`

## Sources

### Primary (HIGH confidence)
- PyJWT PyPI page -- version 2.12.1 confirmed, actively maintained (March 2026 release)
- bcrypt PyPI page -- version 5.0.0 confirmed, current stable
- starlette-csrf PyPI page -- version 3.0.0 confirmed, by FastAPI-Users author (frankie567)
- Python 3.14.2 stdlib -- `uuid.uuid7()` confirmed available
- Existing codebase files -- all architecture patterns derived from actual Phase 1 code

### Secondary (MEDIUM confidence)
- FastAPI GitHub discussions #9587, #11345 -- community consensus on PyJWT over python-jose
- FastAPI GitHub discussion #11773 -- community consensus on passlib being abandoned
- starlette-csrf GitHub -- double-submit cookie pattern implementation details
- Web search -- bcrypt direct usage patterns, refresh token database storage patterns

### Tertiary (LOW confidence)
- None -- all findings verified against primary or secondary sources.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all package versions verified against PyPI, compatibility with Python 3.14 confirmed via dry-run install
- Architecture: HIGH -- patterns derived from existing Phase 1 code (app.py, router.py, conftest.py, schema.py) and established FastAPI conventions
- Pitfalls: HIGH -- drawn from well-documented security issues in JWT/cookie/CSRF handling, verified against multiple sources

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (stable domain, 30-day validity)
