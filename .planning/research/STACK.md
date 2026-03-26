# Technology Stack

**Project:** MusicMind Web
**Researched:** 2026-03-26
**Overall Confidence:** HIGH -- all recommendations verified via multiple sources, current versions confirmed on PyPI/npm

## Recommended Stack

### Frontend Framework

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Next.js | 16.2 | Full-stack React framework | Current stable (March 2026). App Router with React Server Components, Turbopack default bundler (2-5x faster builds), React Compiler stable (auto-memoization). Next.js 15 is already behind -- 16 shipped Oct 2025 and 16.2 is current. | HIGH |
| React | 19.2 | UI library (ships with Next.js 16) | Bundled with Next.js 16. View Transitions, Activity component, useEffectEvent(). No separate install needed. | HIGH |
| TypeScript | 5.x | Type safety | Ships with create-next-app. Non-negotiable for a project this size. | HIGH |

### Frontend UI & Styling

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| shadcn/ui | latest | Component library | Not a package -- components are copied into your source code. Built on Radix UI primitives + Tailwind CSS. Zero dependency lock-in, full ownership of components. The standard for Next.js dashboards in 2026. | HIGH |
| Tailwind CSS | 4.x | Styling | CSS-first configuration (no JS config file needed), 5x faster full builds, 100x faster incremental. Ships with automatic project scanning. Pairs natively with shadcn/ui. | HIGH |
| Recharts | 2.x | Data visualization (taste profiles, genre charts) | Declarative React components wrapping D3. 3.6M weekly downloads. SVG-based, lightweight, good for dashboards with moderate data. Perfect for genre distribution charts, mood radar, temporal taste evolution. | MEDIUM |

### Frontend State & Data

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Zustand | 5.x | Client state (UI state, auth tokens, settings) | ~20M weekly downloads, ~1.2KB. The 2026 default for React client state. Simple store-based model -- no boilerplate. | HIGH |
| TanStack Query | 5.x | Server state (API calls, caching, refetching) | Handles all server-state: caching, background refetch, loading/error states, optimistic updates. Separates server state from client state cleanly. ~5M weekly downloads. | HIGH |

### Frontend AI Chat

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Vercel AI SDK | 6.x | Streaming chat UI, Anthropic provider abstraction | `ai` npm package. AI SDK 6 uses native Server Actions (no /api/chat endpoint needed), end-to-end type safety. `@ai-sdk/anthropic` provider handles streaming, tool_use rendering. Reference implementation for Claude chat in Next.js. The chat UI needs to render tool calls (recommendations, taste queries) inline -- AI SDK 6 handles this. | HIGH |
| @ai-sdk/anthropic | 3.x | Anthropic-specific provider for AI SDK | Latest 3.0.64. Provides structuredOutputMode, streaming tool calls, model switching. Install separately from `ai`. | HIGH |

### Frontend Authentication

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Auth.js (NextAuth v5) | 5.x | Session management, OAuth coordination | Rewrite of NextAuth. JWT sessions by default (no DB needed initially), auto-infers env vars with AUTH_ prefix. Handles session tokens, CSRF. Note: Auth.js handles the _session_ -- the actual Spotify/Apple Music OAuth flows are custom because Auth.js OAuth providers don't cover music-user-token patterns. | HIGH |

### Python Backend API

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| FastAPI | 0.135.x | REST API + SSE streaming endpoints | Current stable (0.135.2, March 2026). Async-native, automatic OpenAPI docs, Pydantic v2 native, SSE support built-in. The existing codebase already uses Pydantic and async patterns -- FastAPI aligns perfectly. Chosen over Litestar because: (1) ecosystem is 10x larger, (2) existing team knows Pydantic patterns, (3) Anthropic/OpenAI/Microsoft run FastAPI in production, (4) every tutorial for "FastAPI + Anthropic + streaming" exists. | HIGH |
| uvicorn | 0.34.x | ASGI server | Standard ASGI server for FastAPI. Production-grade with uvloop on Linux. | HIGH |
| sse-starlette | latest | Server-Sent Events for LLM streaming | SSE beats WebSockets for LLM token streaming: simpler, stateless, auto-reconnect, scales to 100K connections vs WebSocket choking at 12K. Claude responses are server-to-client only -- SSE is purpose-built for this. | HIGH |

### Python Backend -- Claude Integration

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| anthropic | >=0.79 | Anthropic Python SDK (BYOK Claude API) | Official SDK. Async support, streaming, tool_use with @beta_tool decorator and tool_runner. Each user provides their own API key -- the backend creates per-request clients with `anthropic.AsyncAnthropic(api_key=user_key)`. | HIGH |

### Python Backend -- Spotify API

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| httpx | >=0.27 | Async HTTP client for Spotify Web API | **Use httpx directly, NOT Spotipy.** Spotipy is synchronous (uses `requests`), has no async support, and would be the only sync dependency in an otherwise fully-async codebase. The existing MusicMind codebase already wraps Apple Music API with httpx -- apply the same pattern to Spotify. Build a `SpotifyClient` class mirroring the existing `AppleMusicClient`. Direct httpx also means full control over token refresh, rate limiting, and error handling consistent with the Apple Music client. | HIGH |

**Critical note on Spotipy:** Spotipy (v2.26.0) is the most popular Spotify Python library but it is synchronous-only. Wrapping it in `asyncio.to_thread()` is a hack that defeats the purpose of async. Since the codebase already has a battle-tested async httpx client pattern from Apple Music, replicate it for Spotify. The Spotify Web API is simple REST -- no library needed.

### Python Backend -- Apple Music API

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| (existing) httpx + pyjwt[crypto] | >=0.27 / >=2.8 | Apple Music API client + ES256 JWT | Already built and production-tested in `src/musicmind/client.py` and `src/musicmind/auth.py`. No changes needed to the client itself -- just needs to be called per-user instead of from global config. | HIGH |

### Database

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| PostgreSQL | 16 or 17 | Multi-user relational database | SQLite cannot handle concurrent writes from multiple users. PostgreSQL is the only serious choice for multi-user Python webapps. The existing schema uses SQLAlchemy Core -- migration to PostgreSQL requires changing the connection string and handling a few type differences (INTEGER -> BIGINT, TEXT stays TEXT, no BLOB needed). | HIGH |
| asyncpg | 0.31.x | Async PostgreSQL driver | Purpose-built for asyncio + PostgreSQL. C-level binary protocol implementation. Fastest async PG driver available. Replaces aiosqlite. | HIGH |
| SQLAlchemy | >=2.0 | Schema definition + query building (Core mode) | Already used in the existing codebase (Core only, no ORM). `create_async_engine` with asyncpg backend is drop-in. Existing queries in `src/musicmind/db/queries.py` should mostly work unchanged -- SQLAlchemy abstracts dialect differences. | HIGH |
| Alembic | 1.18.x | Database migrations | SQLAlchemy's official migration tool. Auto-generates migrations from model changes. Essential for multi-user DB where you cannot just `metadata.create_all()` and lose data. The current codebase uses `create_all` -- Alembic replaces that pattern. | HIGH |

### Authentication & Security

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| passlib[bcrypt] | latest | Password hashing for user accounts | Industry standard bcrypt hashing. Users need local accounts (email/password) separate from music service OAuth. | HIGH |
| python-jose[cryptography] | latest | JWT token creation/validation for API auth | FastAPI standard for JWT-based authentication. Generates access/refresh tokens for API sessions. | MEDIUM |
| httpx-oauth | latest | OAuth 2.0 PKCE helper | Lightweight OAuth 2.0 client that works with httpx. Handles PKCE code verifier/challenge for Spotify OAuth (required since Nov 2025 -- Spotify deprecated implicit grant). | MEDIUM |

### Infrastructure & Dev Tools

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| uv | latest | Python package management | Already used in the existing project. Fast, reliable, single lockfile. | HIGH |
| Docker Compose | latest | Local development (PostgreSQL) | Run PostgreSQL locally without system install. Single `docker compose up` for the database. | HIGH |
| Ruff | >=0.15 | Linting + formatting | Already used in the existing project. | HIGH |
| pytest + pytest-asyncio | >=8.0 / >=0.23 | Testing | Already configured in the existing project. | HIGH |

## Architecture Decisions

### SSE over WebSockets for Claude Chat Streaming

**Decision:** Use Server-Sent Events (SSE) via `sse-starlette`, not WebSockets.

**Rationale:** Claude chat is fundamentally server-to-client streaming (user sends prompt, server streams tokens back). SSE is purpose-built for this pattern:
- Runs over standard HTTP (works with all proxies/CDNs)
- Automatic reconnection on the client
- Stateless -- no connection pool management
- Scales to 100K+ concurrent connections (WebSockets struggle at ~12K)
- Every LLM streaming tutorial in 2025-2026 uses SSE, not WebSocket

WebSockets add complexity (bidirectional protocol, connection state, heartbeats) for zero benefit in a unidirectional streaming scenario.

### Custom Spotify Client over Spotipy

**Decision:** Build async Spotify API client with httpx, do not use Spotipy.

**Rationale:**
- Spotipy is synchronous-only (uses `requests` internally)
- The existing codebase is 100% async with httpx
- MusicMind already has a battle-tested `AppleMusicClient` pattern with httpx -- replicate it
- Direct httpx gives full control over token lifecycle, retry logic, rate limiting
- Spotify Web API is simple REST with well-documented endpoints -- no wrapper library needed
- Avoids the only sync dependency in an async codebase

### PostgreSQL over SQLite for Multi-User

**Decision:** Migrate from SQLite to PostgreSQL.

**Rationale:**
- SQLite has a single-writer lock -- concurrent user writes will fail or queue
- PostgreSQL handles concurrent connections natively
- asyncpg provides high-performance async access
- SQLAlchemy Core abstracts most dialect differences -- existing queries need minimal changes
- Alembic provides proper migration management (the current `create_all` approach cannot survive schema evolution with real user data)

### Next.js 16 over Next.js 15

**Decision:** Use Next.js 16.2 (current stable), not Next.js 15.

**Rationale:**
- Next.js 16 shipped October 2025 and is the current stable line
- React 19.2 with View Transitions and React Compiler (stable)
- Turbopack is default and stable (was beta in 15)
- Layout deduplication reduces network transfer
- Starting a new project on 15 would require immediate migration

### FastAPI over Litestar

**Decision:** Use FastAPI, not Litestar.

**Rationale:**
- Litestar has better raw throughput (msgspec vs Pydantic serialization) but this project is I/O bound (API calls, DB queries, LLM streaming) -- serialization speed is irrelevant
- FastAPI ecosystem is 10x larger: more tutorials, more StackOverflow answers, more production battle-testing
- Anthropic, OpenAI, Microsoft, Netflix run FastAPI in production
- Existing codebase uses Pydantic everywhere -- FastAPI is native Pydantic v2
- Every "FastAPI + Claude streaming" tutorial exists; Litestar equivalents do not

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Frontend framework | Next.js 16 | Remix, SvelteKit | Next.js dominates React ecosystem. Remix lost momentum after Vercel acquisition. SvelteKit is excellent but smaller ecosystem, fewer AI SDK integrations. |
| Python framework | FastAPI | Litestar, Django | Litestar: smaller ecosystem, no Pydantic native. Django: sync-first, heavyweight, overkill for API wrapper. |
| Database | PostgreSQL + asyncpg | SQLite (keep), MySQL | SQLite: single-writer lock kills multi-user. MySQL: no async driver as mature as asyncpg, PostgreSQL is Python ecosystem default. |
| Spotify library | httpx (direct) | Spotipy, spotify.py | Spotipy: sync-only. spotify.py: uses aiohttp (different from httpx already in project). Direct httpx matches existing Apple Music client pattern. |
| State management | Zustand + TanStack Query | Redux Toolkit, Jotai | Redux: too much boilerplate for small team. Jotai: atomic model unnecessary here -- store-based (Zustand) matches dashboard state patterns. |
| Chat streaming | SSE (sse-starlette) | WebSocket | WebSocket: bidirectional overhead for unidirectional streaming. SSE: purpose-built, simpler, scales better for LLM tokens. |
| CSS | Tailwind 4 + shadcn/ui | CSS Modules, Styled Components, MUI | CSS Modules: no design system. Styled Components: runtime cost, out of fashion. MUI: heavy, opinionated, hard to customize. |
| Charts | Recharts | D3 (raw), Chart.js, Nivo | D3: too low-level for dashboard charts. Chart.js: Canvas-based (harder to style with Tailwind). Nivo: heavier than needed. |
| Auth | Auth.js v5 | Clerk, Supabase Auth, custom JWT | Clerk: overkill for friend group, paid. Supabase Auth: ties you to Supabase. Custom JWT: reinventing the wheel. Auth.js is free, mature, Next.js native. |

## Spotify OAuth: Mandatory PKCE Requirement

As of November 27, 2025, Spotify **requires** Authorization Code Flow with PKCE for all new integrations. The implicit grant flow and HTTP redirect URIs are deprecated and no longer functional. All redirect URIs must use HTTPS.

**Implementation approach:**
1. Frontend initiates OAuth with PKCE code_verifier/code_challenge
2. User authorizes on Spotify
3. Spotify redirects back with authorization code
4. Backend exchanges code + code_verifier for access_token + refresh_token
5. Backend stores encrypted refresh_token in PostgreSQL per user
6. Backend refreshes access_token automatically when expired

## Apple Music OAuth: MusicKit JS (Non-Standard)

Apple Music does **not** use standard OAuth. Instead:
1. Backend generates ES256 JWT developer token (existing `auth.py` handles this)
2. Frontend loads MusicKit JS library
3. MusicKit JS `authorize()` triggers Apple popup
4. User approves, MusicKit JS returns a Music User Token
5. Frontend sends token to backend for storage
6. Token lasts ~6 months, no refresh mechanism -- user must re-authorize on expiry

**The existing codebase already has this flow** in `src/musicmind/setup.py`. It needs adaptation from standalone Python HTTP server to Next.js page + FastAPI endpoint, but the pattern is proven.

## Installation

### Frontend (Next.js)

```bash
# Initialize project
npx create-next-app@latest musicmind-web --typescript --tailwind --app --src-dir

# Core dependencies
npm install ai @ai-sdk/anthropic next-auth@beta @auth/core
npm install zustand @tanstack/react-query recharts

# UI (shadcn/ui is added via CLI, not npm)
npx shadcn@latest init
npx shadcn@latest add button card dialog input tabs chart
```

### Backend (Python)

```bash
# Add to existing pyproject.toml
uv add fastapi uvicorn[standard] sse-starlette
uv add asyncpg alembic
uv add anthropic
uv add passlib[bcrypt] python-jose[cryptography]

# Remove (replaced by asyncpg + PostgreSQL)
# aiosqlite -- only needed if keeping SQLite as dev fallback
```

### Infrastructure

```bash
# docker-compose.yml for local PostgreSQL
docker compose up -d postgres
```

## Key Version Summary

| Package | Version | Source | Verified |
|---------|---------|--------|----------|
| Next.js | 16.2.x | nextjs.org/blog/next-16-2 | 2026-03-26 |
| React | 19.2 | Ships with Next.js 16 | 2026-03-26 |
| Tailwind CSS | 4.x | tailwindcss.com | 2026-03-26 |
| AI SDK | 6.x | npmjs.com/package/ai | 2026-03-26 |
| @ai-sdk/anthropic | 3.0.x | npmjs.com/package/@ai-sdk/anthropic | 2026-03-26 |
| Auth.js | 5.x (beta) | authjs.dev | 2026-03-26 |
| FastAPI | 0.135.x | pypi.org/project/fastapi | 2026-03-26 |
| anthropic (Python SDK) | >=0.79 | pypi.org/project/anthropic | 2026-03-26 |
| asyncpg | 0.31.x | pypi.org/project/asyncpg | 2026-03-26 |
| SQLAlchemy | >=2.0 | Already in project | 2026-03-26 |
| Alembic | 1.18.x | pypi.org/project/alembic | 2026-03-26 |
| Recharts | 2.x | npmjs.com/package/recharts | 2026-03-26 |
| Zustand | 5.x | npmjs.com/package/zustand | 2026-03-26 |
| TanStack Query | 5.x | npmjs.com/package/@tanstack/react-query | 2026-03-26 |
| httpx | >=0.27 | Already in project | 2026-03-26 |

## Sources

- [Next.js 16 announcement](https://nextjs.org/blog/next-16) -- HIGH confidence
- [Next.js 16.2 release](https://nextjs.org/blog/next-16-2) -- HIGH confidence
- [FastAPI on PyPI](https://pypi.org/project/fastapi/) -- HIGH confidence, version 0.135.2
- [Anthropic Python SDK on PyPI](https://pypi.org/project/anthropic/) -- HIGH confidence
- [Anthropic Python SDK GitHub](https://github.com/anthropics/anthropic-sdk-python) -- HIGH confidence
- [AI SDK 6 announcement](https://vercel.com/blog/ai-sdk-6) -- HIGH confidence
- [@ai-sdk/anthropic on npm](https://www.npmjs.com/package/@ai-sdk/anthropic) -- HIGH confidence
- [Spotify OAuth PKCE migration](https://developer.spotify.com/blog/2025-02-12-increasing-the-security-requirements-for-integrating-with-spotify) -- HIGH confidence
- [Spotify PKCE deadline November 2025](https://developer.spotify.com/blog/2025-10-14-reminder-oauth-migration-27-nov-2025) -- HIGH confidence
- [Auth.js v5 documentation](https://authjs.dev/getting-started/migrating-to-v5) -- HIGH confidence
- [asyncpg on PyPI](https://pypi.org/project/asyncpg/) -- HIGH confidence, version 0.31.0
- [Alembic on PyPI](https://pypi.org/project/alembic/) -- HIGH confidence, version 1.18.4
- [Tailwind CSS v4 announcement](https://tailwindcss.com/blog/tailwindcss-v4) -- HIGH confidence
- [SSE vs WebSocket for LLM streaming](https://medium.com/@rameshkannanyt0078/fastapi-real-time-api-websockets-vs-sse-vs-long-polling-2026-guide-ce1029e4432e) -- MEDIUM confidence
- [Zustand as 2026 React default](https://www.pkgpulse.com/blog/react-state-management-2026) -- MEDIUM confidence
- [Spotipy on PyPI](https://pypi.org/project/spotipy/) -- HIGH confidence, version 2.26.0 (sync-only confirmed)
- [Recharts GitHub](https://github.com/recharts/recharts) -- HIGH confidence
- [shadcn/ui for Next.js](https://ui.shadcn.com/docs/installation/next) -- HIGH confidence

---

*Stack research: 2026-03-26*
