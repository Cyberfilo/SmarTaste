# SmarTaste — Complete Project Documentation

> Last updated: 2026-03-31 | Version: 0.1.0 | Status: Production (alpha)

## What Is SmarTaste?

SmarTaste is a hybrid dashboard + AI chat webapp for music discovery. Users connect their Spotify and/or Apple Music accounts, bring their own Claude or OpenAI API key, and get a unified taste profile with personalized recommendations — plus a conversational AI interface for deeper musical exploration.

Built for a small group of friends, not a public product. The core value: genuinely good music recommendations powered by language-aware scoring, real audio analysis (via Deezer + ReccoBeats), and actual listening data across services — not just "people who liked X also liked Y."

**Live at:** https://music.menghi.dev
**Repository:** https://github.com/Cyberfilo/SmarTaste
**License:** MIT (Copyright 2026 Filippo Mattia Menghi)

---

## Features

### Dashboard
- Genre donut chart, top artists with affinity bars, audio traits radar, release year bar chart
- Summary cards: songs analyzed, listening hours, familiarity score, connected service
- All data from the taste profile API — zero extra API calls

### Taste Profile
- Visualizes your musical DNA: top genres (with regional specificity — "Italian Hip-Hop/Rap" stays distinct from generic "Hip-Hop/Rap"), favorite artists with featuring detection, audio trait preferences
- Featuring artist parsing: "Artist A feat. Artist B" → primary (1.0 weight) + featuring (0.3 weight)
- Familiarity score (Shannon entropy) measuring how diverse vs. focused your taste is
- Audio centroid computed from enriched features for audio similarity scoring
- Profiles cached for 24 hours, refreshable on demand

### Smart Recommendations (Suggested tab)
- 4 discovery strategies: similar artist crawl, genre adjacent exploration, editorial mining, chart filtering
- Language-first scoring: language/region 45%, audio similarity 32%, genre 13%, artist 10%
- Regional genre detection: "Italian Hip-Hop/Rap" → Italian tracks score 1.0, generic 0.2, wrong-region 0.0
- Audio weight redistributed proportionally when features unavailable (no neutral 0.5 drag)
- Mood filtering (workout, chill, focus, party, sad, driving)
- Auto-detects Apple Music storefront (US/IT/GB/etc.) for regional discovery

### Audio Enrichment Pipeline
- Runs automatically when a user connects a service or visits the app
- Stage 1: Deezer — search by title+artist → 30s preview MP3 + BPM (free, no auth)
- Stage 2: ReccoBeats — upload preview → 9 audio features: acousticness, danceability, energy, instrumentalness, liveness, loudness, speechiness, tempo, valence (free, no auth)
- Stage 3: SoundStat — Spotify ID → complete features + key/scale (paid, 0.01 EUR/track, optional)
- ISRC → Spotify ID resolution via MusicBrainz (permanently cached)
- Batch processing: 5 tracks/batch, 1s delay, gc.collect() between batches (memory-safe)
- Per-field provenance tracking (feature_source JSON: {"tempo": "deezer", "energy": "reccobeats"})

### Playlists
- Browse real playlists from connected Apple Music and Spotify
- Click-through to track list with artwork, artist, album, genre badges
- Per-playlist recommendations: builds mini taste profile from playlist songs only, re-scores candidates
- Scrollable track list (no truncation)

### Listening Timeline
- Chronological view of songs with month grouping and artwork
- Date type labels: "added" (dateAdded), "release" (releaseDate fallback), "unknown"
- Critical for Apple Music given the 50-song recently-played API limit

### Claude/OpenAI Chat (BYOK)
- Conversational recommendations via Claude or OpenAI — users bring their own API keys
- Claude has tool access to your taste profile, library, and recommendation engine
- Ask things like "something like early Radiohead but more electronic"
- SSE streaming for real-time responses
- Conversation history with auto-generated titles

### Multi-Service Support
- Connect Spotify and/or Apple Music independently — app works with just one
- When both connected: unified taste profile with ISRC-based deduplication and cross-service genre normalization
- Each service's strengths leveraged (Spotify's top tracks API, Apple Music's library metadata)
- Library sync runs on every page load (background, non-blocking, 10 tracks per visit)

### Admin Panel
- Dev/User toggle in header (admin users only, `is_admin` on users table)
- Live Logs tab: SSE stream of all backend log output in real-time, color-coded by level
- Enrichment tab: per-user progress bars (enriched/total songs), live counter in tab bar
- System tab: version, user count, total songs/enriched, SoundStat API key status

### Settings
- Service connection/disconnection (Spotify OAuth PKCE, Apple Music MusicKit JS)
- BYOK API key management for Claude and OpenAI (encrypted at rest with Fernet)
- Model selection (Claude or OpenAI)
- Spotify users need to reconnect after upgrade (new playlist-read-private scope)

---

## Hosting & Deployment

### Production Stack

| Component | Platform | URL/Config |
|-----------|----------|------------|
| Frontend | Vercel | https://music.menghi.dev |
| Backend | Railway | Docker container (Python 3.14) |
| Database | Railway | PostgreSQL 16 (provisioned by Railway) |
| Domain | Custom | music.menghi.dev (Vercel DNS) |

### How It Works
- **Frontend** auto-deploys from GitHub via Vercel. `vercel.json` sets `rootDirectory: frontend`. Vercel auto-detects Next.js.
- **Backend** auto-deploys from GitHub via Railway. `railway.toml` configures Dockerfile builder with `/health` healthcheck.
- **Database migrations** run automatically on every Railway deploy via `alembic upgrade head` in the Dockerfile entrypoint.
- **Secrets** (Fernet key, JWT secret) are auto-generated on first deploy if not provided as env vars.

### Local Development
```
docker compose up db -d          # PostgreSQL 16
cd backend && uv sync --dev      # Python deps
uv run alembic upgrade head      # Migrations
uv run uvicorn musicmind.app:app --reload --port 8000

cd frontend && npm install       # Node deps
npm run dev                      # Next.js on :3000
```

Sandbox mode (`MUSICMIND_SANDBOX=true`) pre-fills required secrets with dev defaults.

---

## Accounts & Authentication

### User Accounts
- Email + password registration (bcrypt hashed, 12 rounds)
- JWT access tokens (30 min, HS256) stored in httpOnly cookies
- Refresh tokens (7 days) stored in DB, revocable
- SameSite=lax cookies provide CSRF protection when proxied through Next.js

### Auth Flow
1. `POST /api/auth/signup` — creates user, returns JWT cookies
2. `POST /api/auth/login` — verifies password, returns JWT cookies
3. On 401, frontend auto-calls `POST /api/auth/refresh` to renew
4. `POST /api/auth/logout` — clears cookies, revokes refresh token

### Service Connections
- **Spotify:** OAuth 2.0 PKCE flow. User redirected to Spotify, callback stores encrypted access + refresh tokens in `service_connections` table.
- **Apple Music:** MusicKit JS flow. Frontend loads Apple's MusicKit JS, user authorizes, Music User Token sent to backend and stored encrypted.
- All tokens encrypted at rest with Fernet symmetric cipher.

### BYOK (Bring Your Own Key)
- Users store their Claude (Anthropic) or OpenAI API keys via Settings
- Keys encrypted with Fernet before storage in `user_api_keys` table
- Keys validated against the provider API before saving
- No shared API key — each user pays for their own AI usage

---

## Database

### Engine
- **PostgreSQL 16** (Alpine) via Docker or Railway
- **SQLAlchemy Core** (no ORM) for all queries — async via asyncpg
- **Alembic** for schema migrations (8 migrations to date)
- Connection: `postgresql+asyncpg://` (asyncpg driver required)

### Tables

| Table | Purpose | Primary Key |
|-------|---------|-------------|
| `users` | User accounts | `id` (Text, UUID) |
| `service_connections` | Encrypted OAuth tokens (Spotify, Apple Music) | `user_id` + `service` |
| `user_api_keys` | BYOK Claude/OpenAI keys (encrypted) | `user_id` + `service` |
| `refresh_tokens` | JWT refresh tokens (revocable) | `id` (Text, UUID) |
| `song_metadata_cache` | Cached song metadata from both services | `id` (auto-increment) |
| `listening_history` | Recently played songs | `id` (auto-increment) |
| `artist_cache` | Cached artist metadata (genres, top songs) | `user_id` + `artist_id` |
| `taste_profile_snapshots` | Cached taste profiles (24h TTL) | `user_id` + `service` + `created_at` |
| `recommendation_feedback` | Thumbs up/down on recommendations | `user_id` + `catalog_id` + `timestamp` |
| `audio_features_cache` | Cached audio features (tempo, energy, etc.) | `user_id` + `catalog_id` |
| `chat_conversations` | Chat sessions with messages JSON | `id` (Text, UUID) |
| `chat_messages` | Normalized chat messages | `id` (Text, UUID) |

All user-scoped tables have `user_id` foreign keys with CASCADE delete. Multi-tenant by design.

### Dead Tables (exist in schema, never populated)
- `sound_classification_cache` — macOS SoundAnalysis labels (not implemented)
- `bandit_arms` — Thompson Sampling exploration (not wired in)
- `kg_artists`, `kg_relationships` — Knowledge graph (not implemented)
- `generated_playlists` — Playlist feature (not implemented)
- `lastfm_tags_cache` — Last.fm enrichment (not integrated)
- `audio_embeddings` — 128-dim audio embeddings (extractor not wired in)
- `play_count_proxy` — Approximate play counts (not implemented)

---

## Libraries & Dependencies

### Backend (Python 3.11+, managed by uv)

| Library | Version | Purpose |
|---------|---------|---------|
| FastAPI | >=0.135 | Web framework |
| SQLAlchemy | >=2.0 | Query builder (Core, no ORM) |
| asyncpg | >=0.31 | PostgreSQL async driver |
| Alembic | >=1.18 | Database migrations |
| Pydantic | >=2.0 | Data validation + settings |
| pydantic-settings | >=2.0 | Environment variable loading |
| PyJWT | >=2.12.1 | JWT token generation/verification |
| bcrypt | >=5.0.0 | Password hashing |
| cryptography | >=42.0 | Fernet symmetric encryption |
| anthropic | >=0.40 | Claude API SDK (BYOK) |
| openai | >=1.60 | OpenAI API SDK (BYOK) |
| httpx | >=0.27 | Async HTTP client (Spotify/Apple APIs) |
| numpy | >=1.26 | Vector math for scoring |
| slowapi | >=0.1.9 | Rate limiting |
| greenlet | >=3.0 | SQLAlchemy async support |

### Frontend (Node, managed by npm)

| Library | Version | Purpose |
|---------|---------|---------|
| Next.js | 16.2.1 | React framework |
| React | 19.2.4 | UI library |
| TypeScript | ^5 | Type safety |
| Tailwind CSS | ^4 | Styling (PostCSS) |
| shadcn/ui | 4.1.1 | Accessible component primitives |
| TanStack Query | ^5.95.2 | Server state management |
| Zustand | ^5.0.12 | Client state management |
| Recharts | ^3.8.1 | Data visualization |
| Sonner | ^2.0.7 | Toast notifications |
| Lucide React | ^1.7.0 | Icons |

### Unused Frontend Dependencies
- `next-themes` — installed but dark mode is hardcoded (no theme toggle)

---

## External Services & APIs

| Service | Purpose | Auth Method |
|---------|---------|-------------|
| Spotify Web API | Library, top tracks/artists, OAuth | OAuth 2.0 PKCE (client_id + client_secret) |
| Apple Music API | Library songs, recently played | ES256 JWT developer token + Music User Token |
| Anthropic API | Claude chat (BYOK) | User-provided API key |
| OpenAI API | Chat alternative (BYOK) | User-provided API key |
| Vercel | Frontend hosting | GitHub integration |
| Railway | Backend + PostgreSQL hosting | GitHub integration |

### Rate Limits
- **Apple Music:** ~20 req/sec (undocumented), handled with exponential backoff (3 retries, 1s base)
- **Spotify:** ~100 req/sec but with monthly quotas. Dev mode limited to 5 authorized users.
- **Backend rate limiting (slowapi):** Auth endpoints 5/min, chat 20/min, recommendations 30/min

---

## Domains

| Domain | Points To | Purpose |
|--------|-----------|---------|
| `music.menghi.dev` | Vercel | Production frontend |
| `live.menghi.dev` | Local dev tunnel | Development (in allowedDevOrigins) |
| `localhost:3000` | Next.js dev server | Local frontend |
| `localhost:8000` | Uvicorn dev server | Local backend |

CORS allows: `localhost:3000`, `127.0.0.1:3000`, `music.menghi.dev`, plus any extra from `MUSICMIND_CORS_ORIGINS` env var.

---

## How Every File Interacts

### Data Flow

```
User Browser
    ↓ HTTPS
Vercel (Next.js 16 — SSR + static)
    ↓ /api/* proxied via next.config.ts rewrites
Railway (FastAPI backend)
    ↓
┌─────────────────────────────────────────────────┐
│  API Layer (routers → services → fetch)         │
│  ├── auth/     JWT signup/login/refresh         │
│  ├── services/ Spotify OAuth, Apple MusicKit    │
│  ├── taste/    Profile building pipeline        │
│  ├── stats/    Top tracks/artists/genres        │
│  ├── recommendations/ Discovery + scoring       │
│  ├── chat/     Claude/OpenAI SSE streaming      │
│  ├── claude/   BYOK key management              │
│  ├── openai/   BYOK key management              │
│  ├── tracks/   Audio feature detail views       │
│  └── session/  Playback session context         │
│                                                 │
│  Engine Layer (pure algorithms, no I/O)         │
│  ├── scorer.py     7-dimension weighted scoring │
│  ├── profile.py    Taste profile builder        │
│  ├── genres.py     Cross-service normalization  │
│  ├── dedup.py      ISRC + fuzzy deduplication   │
│  ├── similarity.py Cosine/Jaccard similarity    │
│  ├── mood.py       Categorical mood filtering   │
│  ├── weights.py    Adaptive weight optimizer    │
│  └── session.py    Sequential session model     │
│                                                 │
│  Data Layer                                     │
│  ├── db/schema.py  SQLAlchemy table definitions │
│  ├── db/engine.py  Async engine factory         │
│  └── security/     Fernet encryption service    │
└─────────────────────────────────────────────────┘
    ↓
PostgreSQL 16 (Railway)
    ↓
External APIs: Spotify, Apple Music, Anthropic, OpenAI
```

### Frontend File Map

```
frontend/src/
├── app/
│   ├── layout.tsx              Root layout (providers, fonts)
│   ├── page.tsx                Landing → redirects to /dashboard or /login
│   ├── (auth)/
│   │   ├── layout.tsx          Auth layout (centered card)
│   │   ├── login/page.tsx      Login form → POST /api/auth/login
│   │   └── signup/page.tsx     Signup form → POST /api/auth/signup
│   └── (app)/
│       ├── layout.tsx          App shell (sidebar nav, user menu, auth guard)
│       ├── dashboard/
│       │   ├── page.tsx        Overview (summary cards + tab navigation)
│       │   ├── taste/page.tsx  Taste Profile (genres, artists, audio traits)
│       │   ├── stats/page.tsx  Listening Stats (top tracks/artists/genres by period)
│       │   └── recommendations/page.tsx  Recommendation feed
│       ├── chat/page.tsx       Claude/OpenAI chat interface
│       └── settings/page.tsx   Service connections, BYOK keys, model selector
├── components/
│   ├── ui/                     12 shadcn/ui primitives (button, card, dialog, etc.)
│   ├── chat/                   Chat interface, message bubbles, conversation sidebar
│   ├── dashboard/              Stats tables, taste profile panels, period selector
│   ├── recommendations/        Recommendation cards, strategy selector, score breakdown
│   └── settings/               Service connectors, key managers, model selector
├── hooks/                      TanStack Query hooks (taste, stats, recommendations, etc.)
├── stores/                     Zustand auth store (login/logout/refresh)
├── lib/
│   ├── api.ts                  Central API client (apiFetch with auth refresh)
│   ├── sse.ts                  POST-based SSE streaming for chat
│   └── utils.ts                cn() helper for Tailwind class merging
├── types/api.ts                TypeScript interfaces for all API responses
└── providers/query-provider.tsx TanStack Query client setup
```

### Backend File Map

```
backend/src/musicmind/
├── __init__.py                 Package root (__version__)
├── app.py                      FastAPI factory (lifespan, CORS, middleware)
├── config.py                   Pydantic settings (env vars with MUSICMIND_ prefix)
├── api/
│   ├── router.py               Aggregates all sub-routers under /api
│   ├── rate_limit.py           slowapi limiter configuration
│   ├── health.py               GET /health endpoint
│   ├── auth/                   JWT auth (signup, login, logout, refresh, me)
│   ├── services/               Spotify OAuth PKCE + Apple Music MusicKit
│   ├── taste/
│   │   ├── router.py           Profile, genres, artists, audio-traits endpoints
│   │   ├── service.py          Profile build pipeline (cache check → fetch → compute)
│   │   └── fetch.py            Spotify/Apple Music library fetching + cache storage
│   ├── stats/
│   │   ├── router.py           Top tracks/artists/genres by period
│   │   ├── service.py          Stats computation pipeline
│   │   └── fetch.py            Period filtering + ranking algorithms
│   ├── recommendations/
│   │   ├── router.py           GET recommendations, POST feedback, GET breakdown
│   │   ├── service.py          Discovery → scoring → ranking pipeline
│   │   ├── fetch.py            4 discovery strategies (Spotify + Apple Music)
│   │   └── schemas.py          Request/response models
│   ├── chat/
│   │   ├── router.py           POST message (SSE), GET/DELETE conversations
│   │   ├── service.py          LLM orchestration + tool execution
│   │   ├── tools.py            Tool definitions for Claude/OpenAI
│   │   ├── tool_converter.py   Format conversion between providers
│   │   ├── system_prompt.py    Dynamic system prompt with user context
│   │   └── providers/          Claude + OpenAI streaming implementations
│   ├── claude/                 BYOK key management (store, validate, delete)
│   ├── openai/                 BYOK key management (store, validate, delete)
│   ├── tracks/                 Audio feature detail endpoint
│   └── session/                Playback session context
├── engine/
│   ├── scorer.py               7-dimension candidate scoring + MMR ranking
│   ├── profile.py              Taste profile builder (genres, artists, audio, years)
│   ├── genres.py               150+ canonical genre mappings, cross-service normalization
│   ├── dedup.py                ISRC + fuzzy track deduplication
│   ├── similarity.py           Cosine, Jaccard, L2 similarity functions
│   ├── mood.py                 8 mood profiles with audio trait targets
│   ├── weights.py              Coordinate descent weight optimizer
│   ├── session.py              Sequential session model (exponential averaging)
│   ├── models.py               Typed dataclasses (Candidate, ScoreBreakdown, etc.)
│   ├── audio/
│   │   ├── cache.py            Audio features DB read/write
│   │   ├── models.py           ExtractedFeatures dataclass
│   │   └── extractor.py        ⚠️ DEAD CODE — Essentia extraction, never called
│   ├── bandit.py               ⚠️ DEAD CODE — Thompson Sampling, never called
│   ├── clap_mood.py            ⚠️ DEAD CODE — CLAP mood embeddings, never called
│   ├── lastfm.py               ⚠️ DEAD CODE — Last.fm tag enrichment, never called
│   └── knowledge_graph/        ⚠️ DEAD CODE — MusicBrainz graph, never called
├── auth/                       JWT logic, bcrypt, dependencies
├── db/
│   ├── schema.py               SQLAlchemy Core table definitions (15+ tables)
│   └── engine.py               Async engine factory
└── security/
    └── encryption.py           Fernet cipher service
```

---

## Recommendation Engine — How It Works

### Scoring (4 Dimensions, Language-First)

| Dimension | Weight | How It Works |
|-----------|--------|-------------|
| **Language/Region** | 0.45 | Detects regional prefixes from genre names ("Italian" from "Italian Hip-Hop/Rap"). Italian tracks score 1.0, generic tracks 0.2, wrong-region 0.0. Most important signal for regional listeners. |
| **Audio similarity** | 0.32 | Energy, tempo, danceability, valence proximity from enriched features (Deezer + ReccoBeats). When features unavailable, weight redistributed to other dimensions (no neutral drag). |
| **Genre match** | 0.13 | Cosine similarity between song genre vector and user genre vector. Regional genres get full weight (1.0), parent genres get 0.3. |
| **Artist affinity** | 0.10 | Known artist score from library + play count. Featuring artists detected at 0.3 weight. Penalized if artist in wrong genre. |

Diversity penalty and staleness cooldown applied as minor subtractive penalties on top.

### Discovery Strategies

1. **similar_artist** — Crawl similar artists, fetch top songs, filter by regional genre overlap
2. **genre_adjacent** — Search using user's top genre names on user's storefront, filter by regional match
3. **editorial** — Mine editorial/best-of playlists, filter by regional genre overlap
4. **chart** — Filter charts by genre overlap with user profile
5. **all** — Run all 4 in parallel, merge and deduplicate candidates

All strategies use auto-detected storefront (e.g., "it" for Italian Apple Music users) and prefer exact regional genre matches over parent-only matches.

### Per-Playlist Recommendations
Builds a mini taste profile from the playlist's songs only (not the overall user profile), then re-scores candidates against that local profile. A "chill Italian" playlist generates chill Italian recommendations.

### Adaptive Weights
After 10+ feedback records (thumbs up/down), coordinate descent optimizer adjusts dimension weights to minimize prediction error. Weights bounded to prevent any dimension from dominating.

---

## Known Bugs & Issues

### Active Bugs

| Bug | Location | Impact | Status |
|-----|----------|--------|--------|
| **Pre-existing CSRF test failures** | `test_auth.py` — 3 tests fail due to cookie handling in test client | Tests only, no production impact | Known |

### Recently Fixed (March 2026)

| Bug | Fix |
|-----|-----|
| Strategy name mismatch (3/5 strategies failed) | Frontend values aligned with backend: auto→all, similar_artists→similar_artist, charts→chart |
| Apple Music stats always empty | Added releaseDate fallback when dateAdded is null |
| Recommendations irrelevant (Kanye for Italian users) | Auto-detect storefront, regional genre filtering, language-first scoring |
| Audio scoring always neutral (0.5) | Enrichment pipeline (Deezer→ReccoBeats) now populates features; weight redistributed when unavailable |
| Low confidence scores | Audio weight redistributed proportionally instead of dragging score with 0.5 |
| Docker build crash (VERSION file) | Reverted pyproject.toml to static version |
| OOM on enrichment | Batch processing (5/batch) + decoupled from profile build |
| Deezer ISRC lookup broken | Switched to search-by-name approach |
| Playlist scroll truncation | Added max-h-[60vh] overflow-y-auto |

### Tackled Issues (Fixed)

| Issue | Fix | Commit |
|-------|-----|--------|
| artist_cache PK missing user_id | Migration 006: composite PK (user_id + artist_id) | Phase 1 |
| audio_features_cache PK missing user_id | Migration 006: composite PK | Phase 1 |
| sound_classification_cache PK missing user_id | Migration 006: composite PK | Phase 1 |
| Apple .p8 key in cloud (no file path) | Added `apple_private_key_b64` env var for base64-encoded key | f442a86 |
| Railway PostgreSQL URL format | Auto-convert `postgresql://` → `postgresql+asyncpg://` | 4d6d364 |
| Missing rate_limit module crash | Added `rate_limit.py` module | 0481470 |
| Same-origin cookie issues | Vercel rewrites proxy all /api/* for cookie consistency | aabb9a7 |
| Dockerfile Python version | Upgraded to Python 3.14 for uuid7 support | a937615 |
| Auto-secret generation | Dockerfile generates Fernet + JWT keys on first deploy if not set | Earlier commit |

### Dead Code (Removed)

All ~1,350 lines of dead code were removed in the March 2026 cleanup:
bandit.py, clap_mood.py, lastfm.py, knowledge_graph/, audio/extractor.py, proxy.ts.
Database tables for these features still exist (already migrated) but are unused.

---

## Limitations & Mitigations

### Apple Music API Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| No play count data | Can't rank by actual listening frequency | Use library presence + recently-played frequency as proxy |
| No `dateAdded` in library API response | Period-based stats impossible | ⚠️ **Currently broken** — need to use `releaseDate` or cache ingestion timestamps as fallback |
| Recently played: max 50 songs, no timestamps | Limited listening history window | Poll periodically to build history over time in `listening_history` table |
| Heavy rotation often empty | Can't rely on it for active listening | Use recently-played endpoint instead |
| Rate limit ~20 req/sec (undocumented) | Slow bulk operations | Exponential backoff (3 retries, 1s base), batch requests |

### Spotify API Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Dev mode: 5 authorized users | Can't onboard more than 5 friends | Apply for extended quota when needed |
| Audio Features API deprecated | Future removal risk | Audio pipeline designed for Essentia (provider-agnostic) |
| Genre data on artists not tracks | Less granular genre matching | Cross-service normalization maps artist genres to track level |

### Architecture Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Single-region deployment | Latency for non-EU users | Acceptable for friend group (all in Italy) |
| No background job queue | Can't do heavy async work (audio analysis) | Profile refresh runs as FastAPI background task |
| No CI/CD pipelines | Manual quality checks | Tests run locally before push |
| No WebSocket support | Chat uses SSE (one-direction streaming) | POST-based SSE sufficient for chat |
| Featuring artists not parsed | Only primary artist contributes to taste profile | ⚠️ **Enhancement planned** — parse "Artist ft. Artist B" |

---

## Environment Variables

All backend variables use the `MUSICMIND_` prefix. Frontend uses `NEXT_PUBLIC_` prefix.

### Backend (Required)

| Variable | Description |
|----------|-------------|
| `MUSICMIND_DATABASE_URL` | PostgreSQL connection string (asyncpg format) |
| `MUSICMIND_FERNET_KEY` | Fernet symmetric encryption key |
| `MUSICMIND_JWT_SECRET_KEY` | JWT HS256 signing secret |

### Backend (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `MUSICMIND_SANDBOX` | `false` | Dev mode with pre-filled secrets |
| `MUSICMIND_DEBUG` | `false` | Debug logging |
| `MUSICMIND_LOG_LEVEL` | `INFO` | Log level |
| `MUSICMIND_FRONTEND_URL` | `http://localhost:3000` | OAuth redirect base URL |
| `MUSICMIND_CORS_ORIGINS` | `` | Extra CORS origins (comma-separated) |
| `MUSICMIND_SPOTIFY_CLIENT_ID` | — | Spotify app client ID |
| `MUSICMIND_SPOTIFY_CLIENT_SECRET` | — | Spotify app secret |
| `MUSICMIND_SPOTIFY_REDIRECT_URI` | `http://localhost:3000/api/services/spotify/callback` | Spotify OAuth callback |
| `MUSICMIND_APPLE_TEAM_ID` | — | Apple Developer Team ID |
| `MUSICMIND_APPLE_KEY_ID` | — | MusicKit key ID |
| `MUSICMIND_APPLE_PRIVATE_KEY_PATH` | — | Path to .p8 key file |
| `MUSICMIND_APPLE_PRIVATE_KEY_B64` | — | Base64-encoded .p8 key (cloud alternative) |
| `DATABASE_URL` | — | Railway/Heroku fallback (auto-converted) |

### Frontend

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend URL for API proxy |
| `BACKEND_URL` | `http://localhost:8000` | Server-side backend URL |

---

## Test Coverage

```
backend/tests/    — 33 test files, 352 tests
```

Coverage areas: authentication, service connections, BYOK key management, taste profiles, listening stats, recommendations, multi-service unification, Claude/OpenAI chat, audio pipeline, session model, knowledge graph, dedup, genre normalization, stats by period.

Run: `cd backend && uv run python -m pytest tests/ -v`

---

## Version History

| Version | Date | Notes |
|---------|------|-------|
| 0.1.0 | 2026-03-30 | Initial alpha — core features working, deployed to music.menghi.dev |
