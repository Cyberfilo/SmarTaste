# Project Research Summary

**Project:** MusicMind Web
**Domain:** Multi-service music discovery webapp with AI chat (Spotify + Apple Music + Claude)
**Researched:** 2026-03-26
**Confidence:** HIGH

## Executive Summary

MusicMind Web is a transformation of an existing single-user MCP server (Apple Music + Claude) into a multi-user web application with Spotify support, a dashboard, and a conversational AI interface. The existing Python engine (taste profiling, 7-dimension scoring, discovery strategies, mood filtering) is sophisticated and battle-tested -- the recommended approach is to wrap it behind a FastAPI backend with user-scoped data access, not rewrite it. The frontend is a Next.js 16 React application using SSE-streamed Claude chat as the primary differentiator. The architecture is a standard two-tier webapp (React SPA + Python API) with the unique wrinkle of per-user BYOK Claude API keys powering an agentic tool-use loop against 15-20 engine functions.

The biggest risk is not technical complexity but Spotify API degradation. Spotify's February 2026 API changes gutted development mode: audio features are gone, batch endpoints are removed, ISRC fields may be stripped, and the app is capped at 5 authorized users. This invalidates PROJECT.md's assumption that Spotify audio features are the primary data source. The project must treat Apple Music as the primary service and Spotify as an enrichment layer. Librosa-based audio extraction becomes the primary source for audio features, not a fallback. Cross-service genre normalization is a harder problem than expected due to fundamentally different taxonomy structures (Spotify: 6,000+ micro-genres on artists; Apple Music: 200 hierarchical genres on tracks).

The mitigation strategy is phase ordering. Build the foundation (auth, database, engine wrapper) first, validate every Spotify endpoint with live API calls before writing integration code, and defer cross-service features until the single-service experience is solid. The Claude chat integration should come after the dashboard works because the chat's tool calls invoke the same engine functions the dashboard validates. This ordering catches pitfalls early and delivers user-visible value at each phase boundary.

## Key Findings

### Recommended Stack

The stack is split between a Next.js 16 frontend and a FastAPI Python backend, connected via REST and SSE. This split is driven by the existing Python engine (which cannot be rewritten in JavaScript without losing months of work) and the need for a modern React-based chat and dashboard UI. All recommendations are verified against current stable versions as of March 2026.

**Core technologies:**
- **Next.js 16.2 + React 19.2:** Current stable frontend framework. App Router, React Server Components, Turbopack (default, stable), React Compiler (auto-memoization). Starting on 15 would require immediate migration.
- **Tailwind CSS 4 + shadcn/ui:** Zero-dependency component system. shadcn copies components into source code (no lock-in). The standard for Next.js dashboards in 2026.
- **Vercel AI SDK 6 + @ai-sdk/anthropic 3.x:** Handles streaming chat UI, tool-use rendering, Server Actions integration. The reference implementation for Claude chat in Next.js.
- **FastAPI 0.135.x + uvicorn:** Async-native Python API framework. Native Pydantic v2, SSE support, automatic OpenAPI docs. Matches the existing codebase's async patterns.
- **SSE via sse-starlette:** SSE over WebSockets for Claude token streaming. Simpler, stateless, auto-reconnect, scales to 100K+ connections. Claude chat is server-to-client only -- SSE is purpose-built.
- **PostgreSQL + asyncpg + SQLAlchemy Core + Alembic:** Multi-user database replacing SQLite. asyncpg is the fastest async PostgreSQL driver. Alembic provides migration management (the current `create_all` approach cannot survive schema evolution with user data).
- **Zustand 5 + TanStack Query 5:** Client state (UI/auth) and server state (API data) respectively. Clean separation, minimal boilerplate.
- **httpx (direct, not Spotipy):** Async Spotify API client matching the existing Apple Music httpx pattern. Spotipy is synchronous-only and would be the sole sync dependency in an async codebase.
- **Anthropic Python SDK (>=0.79):** BYOK per-request client instantiation. `AsyncAnthropic(api_key=user_key)` per chat request.

**Critical version notes:**
- Spotify OAuth now requires PKCE (mandatory since November 2025). Implicit grant is dead.
- Auth.js v5 handles sessions but NOT the music service OAuth flows -- those are custom.
- Recharts 2.x for dashboard visualizations (genre radar, mood charts, taste evolution).

### Expected Features

**Must have (table stakes):**
- T1: Service connection OAuth (Spotify PKCE + Apple Music MusicKit JS) -- zero-value product without this
- T2: Recommendation feed with natural-language explanations (core value proposition)
- T3: Taste profile overview (top genres, artists, audio traits -- the "aha moment")
- T4: Feedback loop (thumbs up/down driving adaptive weights)
- T5: Claude chat interface with streaming and tool-use (primary differentiator)
- T6: BYOK API key management (no subsidized AI costs)
- T7: Multi-service unified view (if both services connected)
- T8: Basic listening stats (top tracks/artists/genres by time period)

**Should have (differentiators):**
- D1: Conversational music exploration via Claude with 15-20 engine tools -- the killer feature
- D2: Recommendation transparency (full 7-dimension scoring breakdown per song)
- D6: Discovery strategy picker (4 existing strategies surfaced as selectable modes)
- D7: Mood-contextual recommendations (existing mood engine surfaced as quick-select chips)
- D5: Natural language taste editing via Claude ("I want less mainstream pop")

**Defer to v2+:**
- D3: Taste evolution timeline (requires historical profile snapshots -- build storage now, UI later)
- D4: Cross-service taste reconciliation (only valuable after genre normalization is solid)
- D8: Audio feature per-track visualization (low effort but low priority)
- A3: Playlist generation/sync (write access complexity, moderate value)

**Explicitly do not build:**
- In-app music playback (DRM, licensing, scoped out)
- Social features (moderation burden, scoped out)
- Admin panel (not needed at friend-group scale)
- Custom algorithm tuning UI with sliders (natural language via Claude is superior)
- Mobile native app (responsive web is sufficient)

### Architecture Approach

The architecture is a three-layer system: React SPA frontend communicating via REST + SSE with a FastAPI backend that wraps the existing MusicMind engine behind user-scoped access. The engine modules (profile, scorer, discovery, mood, weights) are preserved intact and wrapped with a thin `MusicMindEngine` class that adds user scoping and returns structured data instead of markdown. A Multi-Service Adapter provides a unified interface over Spotify and Apple Music clients using a Python Protocol, with ISRC-based deduplication and fuzzy fallback matching. The Claude Orchestrator manages the agentic tool-use loop with per-request API key instantiation, SSE streaming to the frontend, and a Tool Registry mapping 15-20 engine functions to Claude tool definitions.

**Major components:**
1. **FastAPI Backend + Auth Module** -- HTTP routing, JWT sessions, OAuth flow management for Spotify (PKCE) and Apple Music (MusicKit JS token relay)
2. **Multi-Service Adapter** -- Protocol-based abstraction over Apple Music (existing httpx client, adapted) and Spotify (new httpx client, same pattern). Fan-out queries, ISRC deduplication, genre normalization
3. **MusicMind Engine Wrapper** -- User-scoped layer over existing engine modules. Returns structured dicts, not markdown. Called by both dashboard endpoints and Claude tool calls
4. **Claude Orchestrator + Tool Registry** -- Agentic loop with streaming. Per-user BYOK key, tool definitions derived from engine capabilities, SSE event stream to frontend
5. **Database Layer** -- PostgreSQL via asyncpg + SQLAlchemy Core. user_id on every table, Alembic migrations, encrypted token/key storage. New tables: users, service_connections, chat_conversations
6. **React SPA (Next.js 16)** -- Dashboard (taste profile, recommendation feed, stats), Chat (streaming messages, tool-call indicators, inline recommendation cards), Settings (BYOK key, service connections)

### Critical Pitfalls

1. **Spotify Audio Features API is dead for new apps.** The `/v1/audio-features` endpoint returns 403 for apps created after November 2024. PROJECT.md's assumption of "Spotify audio features as primary" is invalidated. Elevate librosa to primary audio extraction for all services, or accept metadata-only (Tier 1) scoring for Spotify tracks. Verify this with a live API call on day one.

2. **Spotify February 2026 API gutting.** Batch track fetching removed (500 tracks = 500 API calls), search limited to 10 results, artist top tracks gone, browse/new-releases gone, `external_ids` (ISRC) possibly removed from dev mode, 5-user cap, Premium required for developer. Design the Spotify client against the February 2026 API surface, not older documentation. Verify every endpoint before writing integration code.

3. **Genre taxonomy mismatch is structural.** Spotify assigns genres to artists (6,000+ micro-genres, flat). Apple Music assigns genres to tracks (200 hierarchical genres). Naive merging produces disjoint vector spaces where cross-service recommendations have zero genre overlap. Build a genre normalization layer mapping both taxonomies to a shared internal representation. Weight Apple Music genre data higher (per-track is more precise than per-artist).

4. **Apple Music user token has no refresh mechanism.** Expires after ~6 months with no programmatic refresh. Build proactive health checking (test with lightweight API call before batches), a frictionless re-auth flow in the UI, and treat disconnection as an expected lifecycle event, not an error.

5. **Claude tool_use agentic loop has strict protocol requirements.** Stop reason must be checked, tool_result blocks must precede text in user messages, tool_use_id must be matched, parallel tool calls must all be executed, and loop must have a hard iteration cap. Use the SDK's `tool_runner` (beta) or follow the exact loop pattern from Anthropic docs. Start with `disable_parallel_tool_use=True` until the loop is stable.

## Implications for Roadmap

Based on research, the project naturally decomposes into 5 phases driven by strict dependency ordering and pitfall avoidance.

### Phase 1: Foundation and Service Authentication

**Rationale:** Everything depends on user accounts, database multi-user schema, and working OAuth flows. The research identified 6 pitfalls that must be addressed before any feature code is written: Spotify PKCE requirements, Apple Music MusicKit JS browser auth, database migration from single-user SQLite, BYOK key encryption, Spotify API endpoint verification, and the 5-user dev mode cap. Building this phase first catches architectural showstoppers before investment in features.

**Delivers:** User signup/login, Spotify OAuth (PKCE), Apple Music OAuth (MusicKit JS), BYOK API key storage (encrypted), PostgreSQL multi-user schema with Alembic migrations, FastAPI skeleton with auth middleware, and a Spotify endpoint verification test suite confirming which API features are actually available in dev mode.

**Addresses:** T1 (Service OAuth), T6 (BYOK Key Management -- storage only, not chat yet)

**Avoids:** Pitfall 1 (Spotify audio features -- verify immediately), Pitfall 2 (February 2026 API changes -- verify all endpoints), Pitfall 4 (Apple Music token expiry -- build re-auth flow from start), Pitfall 6 (Spotify PKCE traps), Pitfall 7 (BYOK key security), Pitfall 8 (SQLite multi-user migration), Pitfall 10 (MusicKit JS browser-only auth)

### Phase 2: Engine Wrapper and Single-Service Dashboard

**Rationale:** With auth and database in place, wrap the existing engine for user-scoped web access and build the core dashboard experience. Start with single-service (Apple Music, since it is the more reliable API and the engine is already built for it). This validates the engine wrapper pattern before adding Spotify complexity.

**Delivers:** Taste profile page, basic listening stats, recommendation feed with explanations, feedback loop (thumbs up/down), discovery strategy picker, mood selector. All working for Apple Music first.

**Addresses:** T3 (Taste Profile), T8 (Basic Stats), T2 (Recommendation Feed), T4 (Feedback Loop), D6 (Discovery Strategy Picker), D7 (Mood Contextual Recs), D2 (Scoring Breakdown)

**Uses:** Next.js 16 frontend, Recharts, Zustand, TanStack Query, FastAPI dashboard router, engine wrapper

**Implements:** Engine Wrapper pattern, Dashboard Router, Token Refresh Middleware

### Phase 3: Spotify Integration and Multi-Service Adapter

**Rationale:** With the dashboard validated against Apple Music, add Spotify as a second service. This phase tackles the hardest data problems: building the Spotify httpx client, the Multi-Service Adapter with Protocol-based abstraction, genre normalization layer, and ISRC/fuzzy cross-service matching. Isolating this in its own phase contains the blast radius of Spotify API limitations.

**Delivers:** Spotify data flowing through the same dashboard, unified multi-service taste profile, cross-service deduplication, genre normalization.

**Addresses:** T7 (Multi-Service Unified View), partially D4 (Cross-Service Reconciliation -- data layer only)

**Avoids:** Pitfall 3 (Genre taxonomy mismatch -- build normalization layer), Pitfall 9 (ISRC matching failures -- multi-strategy fallback), Pitfall 12 (5-user limit -- verify and plan), Pitfall 14 (Rate limit stacking -- per-service rate limiters)

### Phase 4: Claude Chat Integration

**Rationale:** The chat depends on the engine wrapper (validated in Phase 2) and ideally on multi-service data (Phase 3). Building chat after the dashboard means the engine functions are proven correct before Claude invokes them through the agentic loop. The chat is the primary differentiator but also the most complex feature -- isolating it lets the team focus entirely on getting the tool-use loop, streaming, and conversation management right.

**Delivers:** Streaming Claude chat with tool-use, conversational music exploration, natural language taste queries, inline recommendation cards in chat, conversation history persistence, cost transparency per message.

**Addresses:** T5 (Claude Chat), D1 (Conversational Exploration), D5 (Natural Language Taste Editing)

**Avoids:** Pitfall 5 (Agentic loop misimplementation -- use SDK tool_runner or exact loop pattern), Pitfall 11 (Tool definition bloat -- curate 8-10 tools per context, not all 30), Pitfall 13 (Unbounded conversation history -- windowing + summarization)

### Phase 5: Polish and Differentiation

**Rationale:** With all core functionality working, this phase adds the features that create delight without architectural risk. Each item is independently shippable.

**Delivers:** Full scoring breakdown UI, audio feature per-track visualization, taste evolution timeline, cross-service reconciliation narrative, refined chat UX.

**Addresses:** D2 (Scoring Breakdown polish), D8 (Audio Feature Visualization), D3 (Taste Evolution Timeline), D4 (Cross-Service Reconciliation UI), D5 refinement

### Phase Ordering Rationale

- **Foundation first** because every component reads/writes user-scoped data and needs working OAuth. The research identified 8 of 14 pitfalls that impact Phase 1 -- catching them early prevents cascading rework.
- **Single-service dashboard before multi-service** because the Apple Music engine already works and Spotify's API limitations are severe. Proving the engine wrapper with Apple Music data gives a known-good baseline before introducing Spotify's constraints.
- **Multi-service before chat** because Claude's tool calls invoke the same engine functions the dashboard uses. If cross-service data is broken, Claude will surface broken data to users in a harder-to-debug context.
- **Chat last among core features** because it is the highest-complexity integration (agentic loop, streaming, per-user API keys, tool definitions) and benefits most from a stable foundation. The research identified 3 pitfalls specific to chat implementation.
- **Polish as a separate phase** because every differentiator item (D2, D3, D8) is independently shippable and none are blocking the core experience.

### Research Flags

Phases likely needing `/gsd:research-phase` during planning:
- **Phase 1:** Spotify API endpoint verification -- the February 2026 changes are severe and need live testing to confirm exactly what is available in dev mode. Also Apple Music MusicKit JS v3 integration for web auth.
- **Phase 3:** Genre normalization strategy. The structural taxonomy mismatch between services needs a concrete mapping approach. ISRC availability in Spotify dev mode needs verification.
- **Phase 4:** Claude agentic loop implementation. While Anthropic docs are good, the specific pattern of streaming SSE + tool-use + conversation persistence is complex enough to warrant detailed research during planning.

Phases with standard, well-documented patterns (skip research):
- **Phase 2:** Dashboard + engine wrapper. Standard Next.js + FastAPI REST API pattern. Recharts, TanStack Query, Zustand all have extensive documentation.
- **Phase 5:** All items are standard UI work on top of existing data. No novel integration patterns.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified against PyPI/npm on 2026-03-26. Every recommendation is the current stable release. No speculative choices. |
| Features | MEDIUM-HIGH | Table stakes well-established from competitor analysis (stats.fm, Spotify Wrapped, Obscurify). Claude chat differentiator is novel -- no direct competitor reference for UX patterns. |
| Architecture | HIGH | Multi-service adapter pattern is standard. Claude tool-use loop is well-documented by Anthropic. Engine wrapper preserves existing code. Main uncertainty is SQLite vs PostgreSQL timing (research disagrees -- STACK says PostgreSQL from start, ARCHITECTURE says SQLite-first is fine for 5 users). |
| Pitfalls | HIGH | Critical pitfalls verified with official Spotify and Anthropic documentation. The Spotify API degradation is the most consequential finding -- confirmed by multiple official sources and community reports. |

**Overall confidence:** HIGH

### Gaps to Address

- **Spotify `external_ids` (ISRC) availability in dev mode:** The February 2026 migration guide lists this field as removed, but it needs verification with a live API call. If ISRC is gone, cross-service matching falls back to fuzzy title+artist matching, which is significantly less reliable. Verify in Phase 1 before designing the Phase 3 data model.

- **Spotify 30-second preview URL availability:** Also reported as removed in dev mode. If previews are gone, librosa audio extraction cannot work for Spotify tracks at all, leaving metadata-only scoring as the only option. Verify in Phase 1.

- **Apple Music user token actual lifetime:** Documented as "approximately 6 months" but reports vary (some say days after account events). There is no way to know the exact lifetime programmatically. Build for the worst case (re-auth flow always ready).

- **SQLite vs PostgreSQL for 5 users:** STACK.md recommends PostgreSQL from day one. ARCHITECTURE.md suggests SQLite with WAL mode is fine for 5 users. Recommendation: use PostgreSQL from the start. The deployment complexity is minimal with Docker Compose, and it eliminates the "migrate under load" risk entirely. SQLite as a dev-only fallback is acceptable.

- **Extended Spotify Quota Mode eligibility:** If the friend group exceeds 5 users, extended quota requires a formal application with "established, scalable, and impactful use cases." A personal project may not qualify. No mitigation exists except keeping the group at 5 or fewer.

- **Anthropic SDK `tool_runner` (beta) stability:** Recommended for simplifying the agentic loop, but it is in beta. If it breaks, the manual loop pattern is the fallback. Both patterns are documented.

## Sources

### Primary (HIGH confidence)
- [Spotify Web API Feb 2026 Migration Guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) -- API endpoint removals, dev mode restrictions
- [Spotify API Changes Nov 2024](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api) -- Audio features deprecation
- [Spotify OAuth PKCE Migration](https://developer.spotify.com/blog/2025-02-12-increasing-the-security-requirements-for-integrating-with-spotify) -- PKCE mandatory since Nov 2025
- [Anthropic Tool Use Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) -- Agentic loop protocol
- [Anthropic Streaming Docs](https://platform.claude.com/docs/en/build-with-claude/streaming) -- SSE streaming pattern
- [Next.js 16.2 Release](https://nextjs.org/blog/next-16-2) -- Current stable frontend framework
- [FastAPI on PyPI](https://pypi.org/project/fastapi/) -- Version 0.135.2 confirmed
- [AI SDK 6 Announcement](https://vercel.com/blog/ai-sdk-6) -- Chat streaming + tool-use rendering
- [Auth.js v5 Docs](https://authjs.dev/getting-started/migrating-to-v5) -- Session management
- [asyncpg on PyPI](https://pypi.org/project/asyncpg/) -- Version 0.31.0 confirmed
- [Apple Developer Forums: token expiry](https://developer.apple.com/forums/thread/654814) -- MUT lifetime issues
- [MusicKit JS v3 Docs](https://js-cdn.music.apple.com/musickit/v3/docs/index.html) -- Browser-only auth flow

### Secondary (MEDIUM confidence)
- [SSE vs WebSocket for LLM streaming](https://medium.com/@rameshkannanyt0078/fastapi-real-time-api-websockets-vs-sse-vs-long-polling-2026-guide-ce1029e4432e) -- SSE scaling advantages
- [Zustand as 2026 React default](https://www.pkgpulse.com/blog/react-state-management-2026) -- State management choice
- [BYOK patterns from JetBrains, GitHub Copilot, Vercel](various) -- Key management UX
- [Multi-tenancy with FastAPI + SQLAlchemy](https://mergeboard.com/blog/6-multitenancy-fastapi-sqlalchemy-postgresql/) -- Row-level user isolation
- [ISRC matching accuracy](https://tunarc.com/guides/isrc-matching-explained) -- Cross-service matching reliability
- [Music recommendation explainability research](https://onlinelibrary.wiley.com/doi/full/10.1002/aaai.12056) -- Transparency value

### Tertiary (LOW confidence)
- bijou.fm timeline features -- inferred from marketing copy, not verified hands-on
- 75% user preference for advanced personalization -- claimed in discovery article, no primary source

---
*Research completed: 2026-03-26*
*Ready for roadmap: yes*
