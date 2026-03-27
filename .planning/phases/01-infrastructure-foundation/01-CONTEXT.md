# Phase 1: Infrastructure Foundation - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

This phase delivers the backend skeleton for MusicMind Web: a FastAPI application with PostgreSQL multi-user database, Alembic migrations, encryption for sensitive credentials, and docker-compose local deployment. No frontend, no UI, no business logic beyond health checks.

Requirements: INFR-01 (multi-user DB), INFR-02 (Alembic migrations), INFR-03 (encryption at rest), INFR-05 (local deployment).

</domain>

<decisions>
## Implementation Decisions

### Project Structure
- **D-01:** Monorepo with `/frontend` (Next.js) and `/backend` (FastAPI + existing engine) in a single repository.
- **D-02:** Claude's discretion on where to place the existing `src/musicmind/` engine code within the monorepo layout. Recommendation: move into `backend/src/musicmind/` for clean separation.
- **D-03:** Claude's discretion on root-level package management. Each directory manages its own tooling (uv for backend, npm for frontend). No monorepo orchestrator needed at this scale.

### Data Migration
- **D-04:** Fresh start with PostgreSQL. No migration of existing SQLite data. The webapp will re-fetch everything through its own flows.
- **D-05:** Claude's discretion on schema redesign scope. Recommendation: add `user_id` foreign keys AND `service_source` columns from the start to avoid a second migration when Spotify arrives in Phase 3. Design for multi-user + multi-service from day one.

### Claude's Discretion
- Docker scope: Claude decides what goes in docker-compose (minimum: PostgreSQL; optionally the FastAPI backend).
- Encryption approach: Claude picks the library and scope for encrypting API keys and OAuth tokens at rest. Existing config is plain JSON with file permissions only — needs proper encryption.
- FastAPI project layout within backend/.
- Alembic configuration and initial migration structure.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing Codebase
- `src/musicmind/db/schema.py` -- 9 existing SQLAlchemy Core tables (single-user, no user_id). Reference for column types and JSON usage patterns.
- `src/musicmind/db/manager.py` -- Existing DatabaseManager lifecycle (aiosqlite + metadata.create_all). Shows current connection pattern to replace.
- `src/musicmind/db/queries.py` -- QueryExecutor with all database operations. Every method needs user_id scoping in the new schema.
- `src/musicmind/config.py` -- Current MusicMindConfig Pydantic model. Shows what config fields exist (team_id, key_id, private_key_path, music_user_token, storefront).
- `src/musicmind/server.py` -- FastMCP server entry point with lifespan context pattern. Shows how shared state is initialized and passed.
- `pyproject.toml` -- Current dependencies and build configuration.

### Research
- `.planning/research/STACK.md` -- Recommended stack: FastAPI 0.135.x, asyncpg, SQLAlchemy Core, Alembic, PostgreSQL.
- `.planning/research/ARCHITECTURE.md` -- Component boundaries and build order.
- `.planning/research/PITFALLS.md` -- Pitfall 8 (SQLite multi-user migration), Pitfall 7 (BYOK key security).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/musicmind/db/schema.py` -- 9 table definitions using SQLAlchemy Core MetaData pattern. Can be adapted directly for PostgreSQL with user_id additions.
- `src/musicmind/config.py` -- Pydantic BaseModel config pattern (`MusicMindConfig`). Can inform the new webapp config structure.
- `src/musicmind/models.py` -- Pydantic response models for Apple Music API. Reusable as-is.

### Established Patterns
- SQLAlchemy Core (no ORM) -- entire codebase uses Core mode. Maintain this pattern.
- Async everywhere -- httpx, aiosqlite. New code must be async (asyncpg, async FastAPI endpoints).
- Pydantic BaseModel for validation -- all tool inputs, config, API responses.
- JSON columns for arrays/dicts -- `sa.JSON` used extensively for genre lists, vector data, etc.

### Integration Points
- The FastAPI backend will eventually wrap the existing engine modules (Phase 5+). Phase 1 just sets up the skeleton and database -- no engine integration yet.
- New `users` table and `service_connections` table needed (not in existing schema).
- Encryption layer wraps around config storage and token persistence.

</code_context>

<specifics>
## Specific Ideas

No specific requirements -- open to standard approaches for backend infrastructure.

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope.

</deferred>

---

*Phase: 01-infrastructure-foundation*
*Context gathered: 2026-03-26*
