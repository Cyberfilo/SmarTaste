# Roadmap: MusicMind Web

## Overview

MusicMind Web transforms an existing single-user MCP recommendation engine into a multi-user web application with Spotify support, a dashboard, and conversational AI music exploration. The roadmap progresses from infrastructure foundation through user accounts and service connections, builds out the dashboard experience (taste profile, stats, recommendations) against a single service first, then layers on multi-service unification, Claude chat integration, and finally polish. This ordering catches Spotify API pitfalls early, validates the engine wrapper before Claude invokes it, and delivers user-visible value at each phase boundary.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Infrastructure Foundation** - Multi-user database, migrations, encryption, deployment skeleton
- [ ] **Phase 2: User Accounts** - Signup, login, sessions, and security
- [x] **Phase 3: Service Connections** - Spotify PKCE OAuth, Apple Music MusicKit JS OAuth, connection management (completed 2026-03-27)
- [ ] **Phase 4: BYOK Claude API Key Management** - Key storage, validation, cost transparency
- [ ] **Phase 5: Taste Profile Dashboard** - Single-service taste profile visualization using existing engine
- [ ] **Phase 6: Listening Stats Dashboard** - Top tracks, artists, genres by time period
- [x] **Phase 7: Recommendation Feed** - Personalized suggestions with explanations, feedback loop, discovery strategies, mood filtering (completed 2026-03-27)
- [ ] **Phase 8: Multi-Service Unification** - Data normalization, genre taxonomy mapping, cross-service deduplication, unified taste profile
- [ ] **Phase 9: Claude Chat Integration** - Streaming chat with tool-use, conversational music exploration, conversation persistence
- [ ] **Phase 10: Detail Views and Responsive Polish** - Scoring breakdown, audio feature visualization, responsive design
- [ ] **Phase 11: UI Design & Frontend Shell** - Comprehensive UI/UX for entire webapp using ui-ux-pro-max skill

## Phase Details

### Phase 1: Infrastructure Foundation
**Goal**: The application has a running backend with multi-user data isolation, schema migration capability, and secure storage for sensitive credentials
**Depends on**: Nothing (first phase)
**Requirements**: INFR-01, INFR-02, INFR-03, INFR-05
**Success Criteria** (what must be TRUE):
  1. FastAPI backend starts and serves a health-check endpoint via docker-compose
  2. PostgreSQL database accepts connections with user-scoped tables (user_id foreign keys on all data tables)
  3. Alembic migration runs successfully on a fresh database and produces the expected schema
  4. A secret value (test API key) can be encrypted at rest and decrypted correctly by the application
**Plans**: 2 plans
Plans:
- [x] 01-01-PLAN.md -- Monorepo structure, backend project scaffold, Settings config, Fernet encryption
- [ ] 01-02-PLAN.md -- Multi-user PostgreSQL schema, Alembic migrations, FastAPI app, docker-compose

### Phase 2: User Accounts
**Goal**: Users can create accounts, log in, stay logged in across browser sessions, and log out securely
**Depends on**: Phase 1
**Requirements**: ACCT-01, ACCT-02, ACCT-03, ACCT-04
**Success Criteria** (what must be TRUE):
  1. User can create an account with email and password and is redirected to the dashboard
  2. User can close the browser, reopen it, and still be logged in (persistent JWT session)
  3. User can click "log out" from any page and is returned to the login screen with session destroyed
  4. Session cookies are httpOnly and CSRF protection rejects forged requests
**Plans**: 2 plans
Plans:
- [ ] 02-01-PLAN.md -- Auth foundation: dependencies, schema (refresh_tokens), Settings, auth service layer, schemas, dependencies
- [x] 02-02-PLAN.md -- Auth endpoints (signup/login/logout/refresh/me), CSRF middleware, integration tests
**UI hint**: yes

### Phase 3: Service Connections
**Goal**: Users can connect and disconnect their Spotify and Apple Music accounts, and the app handles token lifecycle correctly
**Depends on**: Phase 2
**Requirements**: SVCN-01, SVCN-02, SVCN-03, SVCN-04, SVCN-05, SVCN-06
**Success Criteria** (what must be TRUE):
  1. User can initiate Spotify OAuth PKCE flow, authorize, and return to the app with account connected
  2. User can initiate Apple Music MusicKit JS authorization and return to the app with account connected
  3. User can view a settings page showing which services are connected with their status (connected, expired, not connected)
  4. User can disconnect a service and the connection status updates immediately
  5. Spotify access tokens refresh silently in the background without user intervention
**Plans**: 2 plans
Plans:
- [x] 03-01-PLAN.md -- Settings extension, service schemas, and service helper module (PKCE, token ops, DB ops)
- [x] 03-02-PLAN.md -- Service router endpoints, SessionMiddleware, and integration tests
**UI hint**: yes

### Phase 4: BYOK Claude API Key Management
**Goal**: Users can securely store and manage their Anthropic API key for AI-powered features
**Depends on**: Phase 2
**Requirements**: BYOK-01, BYOK-02, BYOK-03, BYOK-04
**Success Criteria** (what must be TRUE):
  1. User can enter their Anthropic API key in settings and it is stored encrypted server-side
  2. User can click "validate" and see confirmation that the key works (or a clear error if it does not)
  3. User can update their key to a new one or remove it entirely
  4. User can see estimated cost-per-message before using the chat feature
**Plans**: 2 plans
Plans:
- [x] 04-01-PLAN.md -- Database table (user_api_keys), Pydantic schemas, service helpers, anthropic SDK dependency
- [x] 04-02-PLAN.md -- Claude BYOK router endpoints, api_router wiring, integration tests
**UI hint**: yes

### Phase 5: Taste Profile Dashboard
**Goal**: Users can see a visual representation of their music taste built from a single connected service
**Depends on**: Phase 3
**Requirements**: TAST-01, TAST-02, TAST-03, TAST-04
**Success Criteria** (what must be TRUE):
  1. User with one connected service can view their top genres with regional specificity (e.g., "Italian Hip-Hop/Rap" not just "Hip-Hop")
  2. User can view their top artists ranked by affinity score
  3. User can view their audio trait preferences as a visual display (energy, danceability, valence, acousticness)
  4. The taste profile builds successfully from either Spotify-only or Apple Music-only data
**Plans**: 2 plans
Plans:
- [x] 05-01-PLAN.md -- Alembic migration (service_source on snapshots), engine port, Pydantic schemas, Spotify/Apple Music data fetchers
- [ ] 05-02-PLAN.md -- TasteService pipeline, taste router endpoints, API wiring, integration tests
**UI hint**: yes

### Phase 6: Listening Stats Dashboard
**Goal**: Users can view their listening statistics broken down by time period
**Depends on**: Phase 3
**Requirements**: STAT-01, STAT-02, STAT-03, STAT-04
**Success Criteria** (what must be TRUE):
  1. User can view their top tracks for last month, last 6 months, and all time
  2. User can view their top artists for each time period
  3. User can view their top genres for each time period
  4. Stats work correctly whether the user has one or both services connected
**Plans**: 2 plans
Plans:
- [x] 06-01-PLAN.md -- Stats schemas, time-range-parameterized fetchers, StatsService orchestration
- [x] 06-02-PLAN.md -- Stats router endpoints, API wiring, integration tests (STAT-01 through STAT-04)
**UI hint**: yes

### Phase 7: Recommendation Feed
**Goal**: Users receive personalized music recommendations with explanations and can provide feedback that improves future suggestions
**Depends on**: Phase 5
**Requirements**: RECO-01, RECO-02, RECO-03, RECO-04, RECO-05, RECO-06
**Success Criteria** (what must be TRUE):
  1. User can view a feed of recommended songs they have not heard before
  2. Each recommendation shows a natural-language explanation of why it was suggested
  3. User can give thumbs up or thumbs down on any recommendation and see the feedback registered
  4. After providing feedback, subsequent recommendation batches reflect adjusted scoring weights
  5. User can select a discovery strategy (similar artist, genre adjacent, editorial mining, chart filter) and see recommendations change accordingly
**Plans**: 2 plans
Plans:
- [x] 07-01-PLAN.md -- Engine port (scorer, weights, mood, similarity), numpy dependency, Pydantic schemas, discovery fetch functions
- [x] 07-02-PLAN.md -- RecommendationService pipeline, router endpoints, API wiring, integration tests (RECO-01 through RECO-06)
**UI hint**: yes

### Phase 8: Multi-Service Unification
**Goal**: Users with both services connected see a single unified view of their music taste with data correctly merged across services
**Depends on**: Phase 5, Phase 6
**Requirements**: MSVC-01, MSVC-02, MSVC-03, MSVC-04, TAST-05
**Success Criteria** (what must be TRUE):
  1. Data from both Spotify and Apple Music is normalized to the same internal representation (shared schema)
  2. The same song appearing in both services is deduplicated via ISRC (with fuzzy title+artist fallback)
  3. Genre taxonomies from both services are mapped to a shared canonical representation
  4. User with both services connected can view a unified taste profile that merges data from both
  5. Recommendations draw from both services' catalogs transparently
**Plans**: 2 plans
Plans:
- [ ] 08-01-PLAN.md -- Genre mapping module (Spotify->canonical), track deduplication (ISRC + fuzzy), unit tests
- [ ] 08-02-PLAN.md -- Unified TasteService and RecommendationService modes, integration tests (MSVC-01 through MSVC-04, TAST-05)

### Phase 9: Claude Chat Integration
**Goal**: Users can have a streaming conversational experience with Claude that has full access to their music data and the recommendation engine
**Depends on**: Phase 4, Phase 7
**Requirements**: CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05, CHAT-06, CHAT-07, CHAT-08, CHAT-09, CHAT-10
**Success Criteria** (what must be TRUE):
  1. User can open the chat interface, send a message, and see Claude's response stream in token-by-token
  2. Claude can call MusicMind engine tools (taste profile, discovery, scoring) and the user sees activity indicators during tool execution
  3. User can ask Claude to find music matching a natural language description and receive relevant recommendations
  4. User can have a multi-turn conversation with context preserved, and conversation history persists across browser sessions
  5. Errors (expired API key, rate limit, insufficient balance) display user-friendly messages instead of crashing
**Plans**: 3 plans
Plans:
- [x] 09-01-PLAN.md -- Database table (chat_conversations), Alembic migration, Pydantic schemas, tool registry (8 curated tools)
- [x] 09-02-PLAN.md -- ChatService agentic loop, SSE streaming, tool execution, context window, conversation persistence
- [x] 09-03-PLAN.md -- Chat router endpoints (message SSE, conversation CRUD), API wiring, integration tests
**UI hint**: yes

### Phase 10: Detail Views and Responsive Polish
**Goal**: Users can access detailed scoring breakdowns, per-track audio visualizations, and the full experience works well on mobile browsers
**Depends on**: Phase 7, Phase 9
**Requirements**: RECO-07, RECO-08, INFR-04
**Success Criteria** (what must be TRUE):
  1. User can view the full 7-dimension scoring breakdown for any recommendation (genre, audio, novelty, freshness, diversity, artist, staleness)
  2. User can view a per-track audio feature radar chart (energy, danceability, valence, acousticness, tempo, instrumentalness)
  3. All pages render correctly and are usable on both desktop and mobile screen sizes
**Plans**: 1 plan
Plans:
- [x] 10-01-PLAN.md -- Scoring breakdown endpoint (RECO-07), audio features endpoint (RECO-08), integration tests
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10

Note: Phase 4 depends only on Phase 2 (not Phase 3). Phase 6 depends only on Phase 3 (not Phase 5). Phases 5 and 6 could execute in parallel if desired. Phases 4 and 3 could also overlap since Phase 4 only needs user accounts.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Infrastructure Foundation | 0/2 | Planning complete | - |
| 2. User Accounts | 0/2 | Planning complete | - |
| 3. Service Connections | 2/2 | Complete   | 2026-03-27 |
| 4. BYOK Claude API Key Management | 0/2 | Planning complete | - |
| 5. Taste Profile Dashboard | 1/2 | In Progress|  |
| 6. Listening Stats Dashboard | 0/2 | Planning complete | - |
| 7. Recommendation Feed | 2/2 | Complete   | 2026-03-27 |
| 8. Multi-Service Unification | 0/2 | Planning complete | - |
| 9. Claude Chat Integration | 0/3 | Planning complete | - |
| 10. Detail Views and Responsive Polish | 1/1 | Complete | 2026-03-27 |
| 11. UI Design & Frontend Shell | 0/0 | Not started | - |

### Phase 11: UI Design & Frontend Shell

**Goal:** Comprehensive UI/UX design and frontend implementation for the entire webapp — login/signup pages, dashboard layout, settings page, chat interface, service connection UI — using the ui-ux-pro-max skill for high-quality, distinctive frontend design that avoids generic AI aesthetics
**Depends on:** Phase 3 (needs auth + service connections working for real page interactions)
**Requirements**: INFR-04, plus visual implementations for all user-facing features from Phases 2-10
**Success Criteria** (what must be TRUE):
  1. Next.js 16 frontend exists with responsive layout, navigation, and routing for all major pages
  2. Login/signup pages work end-to-end with the backend auth API
  3. Dashboard page displays placeholder sections for taste profile, stats, recommendations, and chat
  4. Settings page shows service connections with connect/disconnect flows and BYOK API key management
  5. All pages render correctly on desktop and mobile (INFR-04)
**Plans**: TBD
**UI hint**: yes
**Skill note**: Use `/ui-ux-pro-max` skill during discuss-phase and execution for design decisions, color palettes, typography, component styling
