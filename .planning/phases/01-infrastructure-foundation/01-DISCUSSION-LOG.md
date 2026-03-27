# Phase 1: Infrastructure Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-26
**Phase:** 1-Infrastructure Foundation
**Areas discussed:** Project structure, Data migration

---

## Project Structure

### How should the codebase be organized?

| Option | Description | Selected |
|--------|-------------|----------|
| Monorepo (Recommended) | Single repo with /frontend (Next.js) and /backend (FastAPI + existing engine). Simpler git, shared tooling, atomic commits across stack. | ✓ |
| Separate repos | Two repos: one for frontend, one for backend. Independent deployments and CI, but more coordination overhead. | |
| You decide | Claude picks the best structure based on the project needs | |

**User's choice:** Monorepo (Recommended)
**Notes:** None

### Where does existing engine code live?

| Option | Description | Selected |
|--------|-------------|----------|
| Move into backend/ | Restructure: backend/src/musicmind/ with FastAPI alongside the engine | |
| Keep as-is, wrap it | Keep src/musicmind/ where it is, add backend/ and frontend/ at root level around it | |
| You decide | Claude picks the cleanest layout | ✓ |

**User's choice:** You decide
**Notes:** Claude's discretion

### How should the project root be managed?

| Option | Description | Selected |
|--------|-------------|----------|
| Package per directory | backend/ uses uv/pyproject.toml, frontend/ uses npm/package.json -- no root package manager | |
| Turborepo/Nx | Root-level monorepo orchestration for build/test commands | |
| You decide | Claude picks based on project complexity | ✓ |

**User's choice:** You decide
**Notes:** Claude's discretion

---

## Data Migration

### What happens to existing SQLite data?

| Option | Description | Selected |
|--------|-------------|----------|
| Fresh start (Recommended) | Start clean with PostgreSQL. Re-fetch everything through the webapp. Avoids migration complexity for single-user data. | ✓ |
| Migration script | Write a one-time script to port your existing data into the new multi-user PostgreSQL schema | |
| Keep SQLite as fallback | New webapp uses PostgreSQL, but old MCP can still use existing SQLite if you want | |

**User's choice:** Fresh start (Recommended)
**Notes:** None

### How much should the schema change?

| Option | Description | Selected |
|--------|-------------|----------|
| Add user_id only | Keep existing table structures, just add user_id foreign key + index to each | |
| Redesign for multi-svc | Take the opportunity to add service_source columns, normalize for Spotify + Apple Music from the start | |
| You decide | Claude picks the cleanest approach for the multi-user + multi-service future | ✓ |

**User's choice:** You decide
**Notes:** Claude's discretion

---

## Claude's Discretion

- Engine code placement in monorepo layout
- Root-level package management approach
- Schema redesign scope (user_id + service_source vs user_id only)
- Docker scope (PostgreSQL only or full stack)
- Encryption library and scope

## Deferred Ideas

None -- discussion stayed within phase scope.
