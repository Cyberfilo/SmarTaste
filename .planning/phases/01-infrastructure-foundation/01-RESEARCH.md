# Phase 1: Infrastructure Foundation - Research

**Researched:** 2026-03-26
**Domain:** FastAPI backend, PostgreSQL multi-user database, Alembic migrations, encryption at rest, Docker Compose local deployment
**Confidence:** HIGH

## Summary

Phase 1 delivers the backend skeleton for MusicMind Web: a FastAPI application running via docker-compose alongside a PostgreSQL database, with Alembic managing schema migrations and Fernet symmetric encryption protecting sensitive credentials at rest. No frontend, no business logic beyond a health check endpoint.

The existing codebase provides a strong foundation -- 9 SQLAlchemy Core table definitions, a Pydantic-based config model, and an async-everywhere pattern. The migration path is well-defined: replace `aiosqlite` with `asyncpg`, add `user_id` foreign keys on all data tables, introduce two new tables (`users` and `service_connections`), and replace `metadata.create_all()` with Alembic. The `cryptography` library is already a project dependency (used for JWT signing), so Fernet encryption requires no new top-level dependency.

The primary technical risks are (1) SQLite-specific patterns in `queries.py` that must be converted to PostgreSQL-compatible patterns (`INSERT OR REPLACE` does not exist in PostgreSQL), and (2) the N+1 upsert pattern in multiple QueryExecutor methods that should be replaced with PostgreSQL `ON CONFLICT DO UPDATE` during the schema redesign.

**Primary recommendation:** Build a clean `backend/` directory with FastAPI + asyncpg + Alembic, adapt the existing schema.py with user_id columns and service_source columns, and validate the entire stack through docker-compose with PostgreSQL 16.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Monorepo with `/frontend` (Next.js) and `/backend` (FastAPI + existing engine) in a single repository.
- **D-04:** Fresh start with PostgreSQL. No migration of existing SQLite data. The webapp will re-fetch everything through its own flows.

### Claude's Discretion
- **D-02:** Where to place the existing `src/musicmind/` engine code within the monorepo layout. Recommendation from context: move into `backend/src/musicmind/` for clean separation.
- **D-03:** Root-level package management. Each directory manages its own tooling (uv for backend, npm for frontend). No monorepo orchestrator needed at this scale.
- **D-05:** Schema redesign scope. Recommendation: add `user_id` foreign keys AND `service_source` columns from the start to avoid a second migration when Spotify arrives in Phase 3.
- Docker scope: what goes in docker-compose (minimum: PostgreSQL; optionally the FastAPI backend).
- Encryption approach: library and scope for encrypting API keys and OAuth tokens at rest.
- FastAPI project layout within backend/.
- Alembic configuration and initial migration structure.

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INFR-01 | Multi-user database with user-scoped data isolation | Schema redesign pattern (user_id FK on all tables), new users/service_connections tables, PostgreSQL + asyncpg driver, connection pooling configuration |
| INFR-02 | Database migrations via Alembic for schema evolution | Alembic async template with asyncpg, auto-generation from SQLAlchemy Core metadata, initial migration creating full schema |
| INFR-03 | API key and OAuth token encryption at rest | Fernet symmetric encryption via cryptography library (already a dependency), key management via environment variables, encrypt/decrypt utility module |
| INFR-05 | Local-first deployment (runs via docker-compose or similar) | docker-compose.yml with PostgreSQL 16 + health checks, FastAPI via uvicorn, environment variable configuration |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

The following directives from CLAUDE.md constrain this phase's implementation:

- **Package manager:** uv (Astral) for all Python dependency management
- **Code style:** Ruff v0.15.7, line length 100, target py311, rules E/F/I/N/W/UP
- **Type annotations:** `from __future__ import annotations` in every file, `X | None` syntax
- **SQLAlchemy:** Core mode only, no ORM. `sa.Table()` definitions, queries via Core expressions
- **Async everywhere:** All I/O operations must be async (asyncpg for DB, httpx for HTTP)
- **Logging:** stderr only, never stdout. Use `logger.info()` with %-formatting
- **Pydantic:** BaseModel for all validation, config, and API models
- **Naming:** snake_case.py for modules, PascalCase for classes, UPPER_SNAKE_CASE for constants
- **Testing:** pytest + pytest-asyncio with `asyncio_mode = "auto"`
- **Error handling:** RuntimeError for initialization, ValueError for missing config, FileNotFoundError for missing files
- **Comments:** Every .py file has a top-level docstring
- **GSD Workflow:** Do not make direct repo edits outside a GSD workflow unless explicitly asked

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.135.2 | REST API framework | Current stable. Async-native, Pydantic v2 native, automatic OpenAPI. Already aligned with project's Pydantic patterns. |
| uvicorn | 0.42.0 | ASGI server | Standard ASGI server for FastAPI. uvloop on Linux for production performance. |
| asyncpg | 0.31.0 | PostgreSQL async driver | Purpose-built for asyncio + PostgreSQL. C-level binary protocol. Replaces aiosqlite. |
| SQLAlchemy | >=2.0 (existing) | Schema definition + query building | Already in project. `create_async_engine` with asyncpg backend. Core mode maintained. |
| Alembic | 1.18.4 | Database migrations | SQLAlchemy's official migration tool. Async template supports asyncpg natively. |
| PostgreSQL | 16 | Multi-user relational database | Concurrent write support, native JSON, full asyncpg compatibility. Run via Docker. |
| cryptography | >=42.0 (existing) | Fernet encryption at rest | Already a project dependency (used for JWT). Fernet provides AES-128 + HMAC-SHA256. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | >=2.0 (existing) | Config validation, API schemas | All request/response models, settings |
| pydantic-settings | >=2.0 | Environment variable loading | Backend configuration from .env / environment |
| python-dotenv | >=1.0 | Load .env files for local dev | Used by pydantic-settings for local development |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Fernet (cryptography) | AWS KMS / Vault | Overkill for local-first friend-group app. Fernet is simpler, zero infrastructure. |
| asyncpg | psycopg3 (async) | psycopg3 is newer but asyncpg has deeper SQLAlchemy integration and better benchmarks. |
| PostgreSQL 16 | PostgreSQL 17 | PG 17 is newer but PG 16 has broader Docker image testing and documentation. Either works. |
| pydantic-settings | raw os.environ | pydantic-settings provides type validation and .env file support cleanly. |

**Installation (new backend dependencies):**
```bash
cd backend
uv add fastapi "uvicorn[standard]" asyncpg alembic pydantic-settings python-dotenv
```

**Version verification (confirmed 2026-03-26):**
| Package | Verified Version | Source |
|---------|-----------------|--------|
| fastapi | 0.135.2 | `pip3 index versions fastapi` |
| uvicorn | 0.42.0 | `pip3 index versions uvicorn` |
| asyncpg | 0.31.0 | `pip3 index versions asyncpg` |
| alembic | 1.18.4 | PyPI + official docs |
| cryptography | 46.0.6 | PyPI (2026-03-25 release) |

## Architecture Patterns

### Recommended Project Structure

```
musicmind-web/                      # Repository root (monorepo)
├── backend/
│   ├── pyproject.toml              # Backend Python dependencies (uv)
│   ├── uv.lock
│   ├── alembic.ini                 # Alembic config pointing to migrations/
│   ├── alembic/
│   │   ├── env.py                  # Async engine config for asyncpg
│   │   ├── script.py.mako          # Migration template
│   │   └── versions/
│   │       └── 001_initial_schema.py  # First migration
│   ├── src/
│   │   └── musicmind/
│   │       ├── __init__.py
│   │       ├── app.py              # FastAPI app factory + lifespan
│   │       ├── config.py           # Pydantic Settings (env vars)
│   │       ├── security/
│   │       │   ├── __init__.py
│   │       │   └── encryption.py   # Fernet encrypt/decrypt utilities
│   │       ├── db/
│   │       │   ├── __init__.py
│   │       │   ├── schema.py       # All SQLAlchemy Core tables (adapted)
│   │       │   ├── engine.py       # create_async_engine + session factory
│   │       │   └── queries.py      # QueryExecutor (adapted for PG + user_id)
│   │       ├── api/
│   │       │   ├── __init__.py
│   │       │   ├── health.py       # GET /health endpoint
│   │       │   └── router.py       # API router aggregation
│   │       ├── engine/             # Existing engine modules (moved)
│   │       ├── client.py           # Existing Apple Music client (moved)
│   │       ├── auth.py             # Existing JWT auth (moved)
│   │       └── models.py           # Existing Pydantic models (moved)
│   └── tests/
│       ├── conftest.py             # Fixtures: test DB, async engine
│       ├── test_health.py          # Health endpoint test
│       ├── test_schema.py          # Migration + schema validation
│       └── test_encryption.py      # Fernet encrypt/decrypt round-trip
├── frontend/                       # Empty for now (Phase 2+)
├── docker-compose.yml              # PostgreSQL + backend services
├── .env.example                    # Template for required env vars
└── CLAUDE.md                       # Updated project instructions
```

### Pattern 1: FastAPI App Factory with Async Lifespan

**What:** Create the FastAPI app with an async lifespan context manager that initializes the async database engine and connection pool on startup, and disposes it on shutdown.

**When to use:** Every FastAPI app that needs database connections.

**Example:**
```python
# Source: FastAPI official docs + SQLAlchemy async docs
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from musicmind.config import Settings

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and tear down application resources."""
    settings = Settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
        echo=False,
    )
    app.state.engine = engine
    app.state.settings = settings
    yield
    await engine.dispose()

app = FastAPI(title="MusicMind", lifespan=lifespan)
```

### Pattern 2: Pydantic Settings for Configuration

**What:** Use pydantic-settings to load configuration from environment variables with type validation and defaults.

**When to use:** All backend configuration that varies between environments.

**Example:**
```python
# Source: pydantic-settings official docs
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql+asyncpg://musicmind:musicmind@localhost:5432/musicmind"
    fernet_key: str  # No default -- MUST be set
    debug: bool = False
    log_level: str = "INFO"

    model_config = {"env_prefix": "MUSICMIND_", "env_file": ".env"}
```

### Pattern 3: Alembic Async Configuration

**What:** Configure Alembic to use asyncpg via the async template.

**When to use:** Initial Alembic setup for this project.

**Example:**
```python
# alembic/env.py (from Alembic async template)
import asyncio
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool
from alembic import context
from musicmind.db.schema import metadata as target_metadata

config = context.config

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### Pattern 4: Fernet Encryption Utility

**What:** A focused module that encrypts and decrypts sensitive strings (API keys, OAuth tokens) using Fernet symmetric encryption.

**When to use:** Storing or retrieving any user secret from the database.

**Example:**
```python
# Source: cryptography official docs
from cryptography.fernet import Fernet, InvalidToken

class EncryptionService:
    """Encrypts and decrypts sensitive values using Fernet."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string. Returns base64-encoded ciphertext."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a ciphertext string. Returns plaintext."""
        return self._fernet.decrypt(ciphertext.encode()).decode()

    @staticmethod
    def generate_key() -> str:
        """Generate a new Fernet key. Store this securely."""
        return Fernet.generate_key().decode()
```

### Pattern 5: PostgreSQL-Compatible Upsert

**What:** Replace SQLite `INSERT OR REPLACE` with PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`.

**When to use:** All existing upsert methods in QueryExecutor.

**Example:**
```python
# Source: SQLAlchemy Core docs for PostgreSQL dialect
from sqlalchemy.dialects.postgresql import insert as pg_insert

async def upsert_song_metadata(self, user_id: str, songs: list[dict]) -> int:
    if not songs:
        return 0
    async with self._engine.begin() as conn:
        for song in songs:
            song["user_id"] = user_id
            stmt = pg_insert(song_metadata_cache).values(**song)
            stmt = stmt.on_conflict_do_update(
                index_elements=["catalog_id", "user_id"],
                set_={k: stmt.excluded[k] for k in song if k not in ("catalog_id", "user_id")},
            )
            await conn.execute(stmt)
    return len(songs)
```

### Anti-Patterns to Avoid

- **`metadata.create_all()` in production:** The existing codebase uses this for SQLite. With Alembic managing migrations, `create_all` must never be called against the production database. Alembic owns the schema lifecycle.
- **INSERT OR REPLACE (SQLite syntax):** Does not exist in PostgreSQL. Use `INSERT ... ON CONFLICT DO UPDATE` via `sqlalchemy.dialects.postgresql.insert`.
- **Shared mutable connection objects:** Each request must get its own connection from the pool. Never store a connection on `app.state` -- store the engine instead.
- **Plaintext secrets in .env committed to git:** The `.env` file containing FERNET_KEY and DATABASE_URL must be in `.gitignore`. Provide `.env.example` with placeholder values.
- **Global QueryExecutor instance without user_id:** Every query method must accept `user_id` as its first parameter. A query without user_id is a data leak.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Symmetric encryption | Custom AES wrapper | `cryptography.fernet.Fernet` | Handles AES + HMAC + IV + base64 encoding. Hand-rolled crypto is a security liability. |
| Database migrations | Manual ALTER TABLE scripts | Alembic auto-generate | Tracks migration history, supports upgrade/downgrade, detects schema drift. |
| Environment config loading | Raw `os.environ.get()` | `pydantic-settings` BaseSettings | Type validation, .env file support, prefix namespacing, defaults. |
| Connection pooling | Custom connection manager | SQLAlchemy `create_async_engine` pools | Built-in pool_size, max_overflow, pool_recycle, health checks. SQLAlchemy manages the pool. |
| Docker PostgreSQL setup | Manual pg install/config | Docker Compose service with health check | Reproducible, isolated, version-pinned, single-command startup. |

**Key insight:** This phase is pure infrastructure plumbing. Every component has a well-tested library solution. Custom code should be limited to gluing these libraries together and defining the schema.

## Common Pitfalls

### Pitfall 1: SQLite-Specific Patterns in Existing QueryExecutor

**What goes wrong:** The existing `queries.py` uses patterns that are SQLite-specific and will fail or behave differently on PostgreSQL.
**Why it happens:** The codebase was built for SQLite. N+1 upsert patterns (check-then-insert loops) and SQLite-specific syntax are embedded throughout.
**How to avoid:** Audit every method in `queries.py`. Replace check-then-insert with PostgreSQL `ON CONFLICT DO UPDATE`. Replace `INTEGER PRIMARY KEY AUTOINCREMENT` pattern awareness with PostgreSQL `SERIAL` or `IDENTITY`. Boolean columns stored as 0/1 in SQLite are native BOOLEAN in PostgreSQL -- verify all boolean handling.
**Warning signs:** Methods that loop over records and do individual SELECT + INSERT/UPDATE for each one (see `upsert_song_metadata`, `upsert_artist`, `upsert_audio_features`, `upsert_classification_labels`, `upsert_play_observation` -- all 5 methods in queries.py follow this anti-pattern).

### Pitfall 2: Missing user_id on Any Data Table

**What goes wrong:** A table without `user_id` becomes a data leak between users. User A sees User B's listening history, cached songs, or taste profiles.
**Why it happens:** The existing schema has zero `user_id` columns. It is easy to miss one table during the migration.
**How to avoid:** Every table that stores user-specific data MUST have a `user_id` column with a foreign key to `users.id` and an index. Tables to audit: `listening_history`, `song_metadata_cache`, `artist_cache`, `taste_profile_snapshots`, `recommendation_feedback`, `audio_features_cache`, `sound_classification_cache`, `play_count_proxy`, `generated_playlists`. That is all 9 existing tables.
**Warning signs:** Any query method that does not accept `user_id` as a parameter.

### Pitfall 3: Fernet Key Not Set in Environment

**What goes wrong:** The application starts without a FERNET_KEY environment variable and either crashes or falls back to some insecure default.
**Why it happens:** Developer forgets to set the key, or .env file is missing.
**How to avoid:** Make `fernet_key` a required field in Settings (no default value). pydantic-settings will raise a validation error at startup if it is missing. Document the key generation command (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) in .env.example.
**Warning signs:** Application fails to start with "field required" error on fernet_key.

### Pitfall 4: Alembic and SQLAlchemy Metadata Drift

**What goes wrong:** Someone modifies `schema.py` but forgets to generate an Alembic migration. The code expects columns that do not exist in the database.
**Why it happens:** Alembic auto-generation must be run manually after schema changes.
**How to avoid:** Add a CI check or test that compares Alembic's current head against `schema.py`'s metadata. Alembic's `--check` flag detects if migrations are needed: `alembic check` returns non-zero if the schema is out of sync.
**Warning signs:** Runtime errors like "column X does not exist" after a code change.

### Pitfall 5: asyncpg Connection Pool Exhaustion

**What goes wrong:** Under concurrent requests, all connections in the pool are in use and new requests hang until timeout.
**Why it happens:** Default pool_size is 5 with max_overflow of 10. If queries hold connections for too long (large fetches, slow operations), the pool fills up.
**How to avoid:** Set reasonable pool parameters: `pool_size=5, max_overflow=10, pool_timeout=30, pool_recycle=1800`. Use `async with engine.connect()` (context manager) to ensure connections are returned. Never hold a connection across multiple await points with external I/O.
**Warning signs:** Requests timing out with "QueuePool limit of size X overflow Y reached" errors.

### Pitfall 6: Composite Primary Keys for User-Scoped Cache Tables

**What goes wrong:** Tables like `song_metadata_cache` currently have `catalog_id` as the sole primary key. With multi-user, the same song (same catalog_id) can be cached by multiple users with different `user_rating`, `date_added_to_library`, etc.
**Why it happens:** The original schema assumed one user. `catalog_id` was globally unique.
**How to avoid:** For tables where per-user data differs (e.g., user_rating, date_added), use a composite primary key of `(catalog_id, user_id)`. For tables with globally identical data (e.g., audio_features_cache -- same audio features regardless of user), keep catalog_id as primary key but add user_id as a non-PK column for tracking who triggered the cache. Decide per-table.
**Warning signs:** Unique constraint violations when two users cache the same song.

## Code Examples

### Docker Compose with PostgreSQL Health Check

```yaml
# docker-compose.yml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: musicmind
      POSTGRES_PASSWORD: musicmind
      POSTGRES_DB: musicmind
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U musicmind -d musicmind"]
      interval: 5s
      timeout: 5s
      retries: 5

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      MUSICMIND_DATABASE_URL: postgresql+asyncpg://musicmind:musicmind@db:5432/musicmind
      MUSICMIND_FERNET_KEY: ${MUSICMIND_FERNET_KEY}
    depends_on:
      db:
        condition: service_healthy

volumes:
  pgdata:
```

### Multi-User Schema (Adapted from Existing)

```python
# Key changes from existing schema.py:
# 1. Every table gains user_id column with FK + index
# 2. New users table
# 3. New service_connections table
# 4. service_source column on data tables for multi-service

import sqlalchemy as sa

metadata = sa.MetaData()

users = sa.Table(
    "users", metadata,
    sa.Column("id", sa.Text, primary_key=True),  # UUID as text
    sa.Column("email", sa.Text, nullable=False, unique=True),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)

service_connections = sa.Table(
    "service_connections", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Text, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("service", sa.Text, nullable=False),  # "apple_music" | "spotify"
    sa.Column("access_token_encrypted", sa.Text, nullable=False),
    sa.Column("refresh_token_encrypted", sa.Text, nullable=True),
    sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("service_user_id", sa.Text, nullable=True),
    sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.UniqueConstraint("user_id", "service", name="uq_user_service"),
)

# Example adapted table (listening_history with user_id + service_source)
listening_history = sa.Table(
    "listening_history", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Text, sa.ForeignKey("users.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("service_source", sa.Text, nullable=False, server_default="apple_music"),
    sa.Column("song_id", sa.Text, nullable=False, index=True),
    sa.Column("song_name", sa.Text, nullable=False),
    sa.Column("artist_name", sa.Text, nullable=False),
    sa.Column("album_name", sa.Text, server_default=""),
    sa.Column("genre_names", sa.JSON, server_default="[]"),
    sa.Column("duration_ms", sa.Integer, nullable=True),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.func.now()),
    sa.Column("position_in_recent", sa.Integer, nullable=True),
    sa.Column("source", sa.Text, nullable=False, server_default="recently_played"),
)
```

### Health Check Endpoint

```python
# Source: FastAPI official patterns
from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()

@router.get("/health")
async def health_check(request: Request) -> dict:
    """Health check verifying database connectivity."""
    engine = request.app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": str(e)}
```

### Encryption Round-Trip Test Pattern

```python
# Test that encrypt/decrypt is symmetric and works with real API key patterns
from musicmind.security.encryption import EncryptionService

def test_encrypt_decrypt_roundtrip():
    key = EncryptionService.generate_key()
    svc = EncryptionService(key)

    api_key = "sk-ant-api03-test-key-1234567890abcdef"
    encrypted = svc.encrypt(api_key)

    assert encrypted != api_key  # Not plaintext
    assert svc.decrypt(encrypted) == api_key  # Round-trip works
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `metadata.create_all()` | Alembic migrations | Standard since SQLAlchemy + Alembic matured | Schema can evolve without data loss |
| `aiosqlite` driver | `asyncpg` driver | Project decision (fresh PostgreSQL) | Concurrent writes, native JSON, better pool management |
| Plain JSON config file | Pydantic Settings + env vars | FastAPI standard pattern | Type-safe config, environment-aware, .env support |
| File permissions (chmod 600) | Fernet encryption at rest | Security requirement for multi-user | Database compromise does not expose secrets |
| N+1 upsert loops | PostgreSQL ON CONFLICT DO UPDATE | PostgreSQL dialect support in SQLAlchemy | Single-statement upsert, dramatically faster for bulk operations |

**Deprecated/outdated:**
- `aiosqlite`: Still maintained but replaced by asyncpg for this project's multi-user needs
- `INSERT OR REPLACE`: SQLite-specific syntax, does not exist in PostgreSQL
- `metadata.create_all()`: Replaced by Alembic for production schema management

## Open Questions

1. **Composite vs simple primary keys for cache tables**
   - What we know: `song_metadata_cache` currently uses `catalog_id` as PK. With multi-user, the same catalog_id can have different per-user data (user_rating, date_added_to_library).
   - What's unclear: Should cache tables use `(catalog_id, user_id)` composite PK, or should globally-shared data (like audio_features_cache) remain user-independent?
   - Recommendation: Split into user-scoped data (composite PK) and global cache data (catalog_id PK, no user_id). Audio features, sound classifications are per-song, not per-user. Song metadata cache user-specific fields (user_rating, date_added_to_library) should move to a separate `user_song_data` table.

2. **Docker scope: backend containerized or not?**
   - What we know: PostgreSQL must run in Docker. The backend could run either containerized or directly via `uv run`.
   - What's unclear: Whether to containerize the backend in Phase 1 or keep it as a local `uv run uvicorn` process.
   - Recommendation: Include a backend Dockerfile but make it optional. Primary dev flow: `docker compose up db` for PostgreSQL, `uv run uvicorn` for backend. Full containerization via `docker compose up` as an alternative. This gives faster iteration during development.

3. **Existing engine code placement timing**
   - What we know: D-02 says move engine into `backend/src/musicmind/`. Phase 1 does not integrate the engine.
   - What's unclear: Should the monorepo restructure happen in Phase 1 or be deferred to when engine integration is needed?
   - Recommendation: Set up the monorepo structure in Phase 1 (create `backend/` and `frontend/` directories, move existing code into `backend/src/musicmind/`). This avoids a disruptive restructure mid-phase later. However, do not modify engine code -- just relocate it.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker | PostgreSQL container | Yes | 29.3.0 | -- |
| Docker Compose | Service orchestration | Yes | 5.1.0 | -- |
| Python | Backend runtime | Yes | 3.14.2 | -- |
| uv | Package management | Yes | 0.11.1 | -- |
| PostgreSQL (via Docker) | Database | Yes (Docker) | 16-alpine (to pull) | -- |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None.

**Note on Python 3.14:** The system Python is 3.14.2. The project's pyproject.toml requires `>=3.11`. Python 3.14 is compatible. However, asyncpg 0.31.0 wheel availability for Python 3.14 should be verified during dependency installation. If compilation is needed, it will require a C compiler (Xcode CLT on macOS, which is typically present).

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest >= 8.0 + pytest-asyncio >= 0.23 |
| Config file | `backend/pyproject.toml` `[tool.pytest.ini_options]` (to be created based on existing pattern) |
| Quick run command | `cd backend && uv run pytest tests/ -x -q` |
| Full suite command | `cd backend && uv run pytest tests/ -v` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INFR-01 | PostgreSQL accepts connections with user-scoped tables | integration | `uv run pytest tests/test_schema.py -x` | Wave 0 |
| INFR-01 | user_id foreign keys on all data tables | unit | `uv run pytest tests/test_schema.py::test_user_id_on_all_tables -x` | Wave 0 |
| INFR-02 | Alembic migration runs on fresh DB and produces expected schema | integration | `uv run pytest tests/test_migration.py -x` | Wave 0 |
| INFR-03 | Encrypt then decrypt produces original value | unit | `uv run pytest tests/test_encryption.py::test_roundtrip -x` | Wave 0 |
| INFR-03 | Encrypted value differs from plaintext | unit | `uv run pytest tests/test_encryption.py::test_not_plaintext -x` | Wave 0 |
| INFR-05 | FastAPI health endpoint returns healthy with DB | integration | `uv run pytest tests/test_health.py -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `cd backend && uv run pytest tests/ -x -q`
- **Per wave merge:** `cd backend && uv run pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `backend/tests/conftest.py` -- shared fixtures (test database URL, async engine, test client)
- [ ] `backend/tests/test_health.py` -- covers INFR-05
- [ ] `backend/tests/test_schema.py` -- covers INFR-01, INFR-02
- [ ] `backend/tests/test_migration.py` -- covers INFR-02
- [ ] `backend/tests/test_encryption.py` -- covers INFR-03
- [ ] Backend pyproject.toml with pytest configuration
- [ ] Framework install: `cd backend && uv add --dev pytest pytest-asyncio httpx` (httpx for FastAPI TestClient async)

**Testing approach for database tests:** Use a dedicated test PostgreSQL database (either a separate Docker container or a `musicmind_test` database on the same container). The conftest.py fixture should create the test database, run Alembic migrations, yield the engine, and drop the database after tests. Alternatively, use `testcontainers-python` for ephemeral PostgreSQL in tests, but this adds a dependency. The simpler approach: require the dev to have PostgreSQL running (via docker-compose) and use a `MUSICMIND_TEST_DATABASE_URL` environment variable.

## Sources

### Primary (HIGH confidence)
- [FastAPI on PyPI](https://pypi.org/project/fastapi/) -- version 0.135.2 confirmed
- [asyncpg on PyPI](https://pypi.org/project/asyncpg/) -- version 0.31.0 confirmed
- [Alembic official documentation](https://alembic.sqlalchemy.org/en/latest/) -- version 1.18.4, async cookbook
- [Alembic cookbook: async engines](https://alembic.sqlalchemy.org/en/latest/cookbook.html) -- async template pattern
- [cryptography Fernet docs](https://cryptography.io/en/latest/fernet/) -- API reference, security properties
- [uvicorn on PyPI](https://pypi.org/project/uvicorn/) -- version 0.42.0 confirmed
- Existing codebase: `src/musicmind/db/schema.py` -- 9 table definitions to adapt
- Existing codebase: `src/musicmind/db/queries.py` -- 23 query methods needing user_id + PG adaptation
- Existing codebase: `src/musicmind/config.py` -- Pydantic config pattern to evolve into Settings

### Secondary (MEDIUM confidence)
- [FastAPI + asyncpg + SQLAlchemy best practices](https://leapcell.io/blog/building-high-performance-async-apis-with-fastapi-sqlalchemy-2-0-and-asyncpg) -- pool configuration guidance
- [FastAPI + PostgreSQL Docker production setup](https://noqta.tn/en/tutorials/fastapi-docker-production-api-2026) -- docker-compose health check pattern
- [Fernet encryption best practices](https://www.secvalley.com/insights/fernet-encryption-guide/) -- key management guidance
- STACK.md research -- verified stack recommendations

### Tertiary (LOW confidence)
- None. All critical findings verified against official documentation or PyPI.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all versions verified on PyPI 2026-03-26, all libraries well-established
- Architecture: HIGH -- patterns from official FastAPI + SQLAlchemy + Alembic documentation, existing codebase provides clear migration path
- Pitfalls: HIGH -- confirmed by inspecting existing queries.py (SQLite-specific patterns visible in code) and Pitfalls research document

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (stable ecosystem, no fast-moving components)
