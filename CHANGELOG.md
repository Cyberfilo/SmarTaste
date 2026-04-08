# Changelog

All notable changes to SmarTaste are documented here.

Versioning: **V X.YZA** where X=major, Y=small logic, Z=minor, A=bugfix.
When A reaches 10 → Z+1 (A resets to 0). When Z reaches 10 → Y+1. Etc.

---

## V 4.110 — 2026-04-08

### Added — Admin Dashboard Overhaul
- **Enrichment pipeline breakdown**: unenriched / partially enriched / fully enriched counts + percentages. Per-stage progress bars (Audio Features, Last.fm Tags, MusicBrainz Credits, Lyrics Embeddings).
- **Worker status panel**: last activity timestamp, enriched/failed today counts, recent enrichment entries with stage + result + duration.
- **Live log feed**: SSE-powered real-time log stream from backend. Dark terminal theme with color-coded log levels. Auto-scrolls, reconnects on disconnect.
- **DB capacity footer**: PostgreSQL database size for main DB and logs DB, shown as percentage bars (of Railway 5GB plan).
- **SSE proxy in admin service**: dedicated streaming route for `/api/admin/logs/stream` — SSE can't go through the buffered catch-all proxy.

### Backend
- `GET /api/admin/enrichment-breakdown`: pipeline-level counts across 4 enrichment stages
- `GET /api/admin/db-capacity`: `pg_database_size()` for both databases
- `GET /api/admin/worker-status`: enrichment_logs queries for worker activity

---

## V 4.100 — 2026-04-08

### Fixed
- **Worker snowball bug**: queried ALL songs (including worker-discovered) for artist names, causing exponential growth (446 library songs → 148K worker songs → 78K artists). Now only queries library songs (library_id or date_added_to_library).

### Changed
- **Worker featured artist depth**: primary library artists get full depth (25 tracks), featuring artists from parsed names get 3 tracks only. Builds a focused enrichment database instead of infinite crawl.
- **Worker logging**: shows "120 library artists → 85 primary + 35 featured" breakdown

## V 4.000 — 2026-04-08

### Added — Recommendation Engine Overhaul
- **Phase 1 — Last.fm integration**: Tags (crowd-sourced mood/vibe vectors) + track.getSimilar (collaborative filtering from billions of scrobbles). New lastfm_similar_tracks table. Tags cached in lastfm_tags_cache. Worker enriches all tracks.
- **Phase 2 — MusicBrainz producers**: Recording-level credits (producer, songwriter, mixer, engineer) fetched via ISRC → MBID. Stored in kg_artists + kg_relationships. Enables "producer proximity" scoring.
- **Phase 3 — Genius lyrics embeddings**: Scrape lyrics from Genius (free, no OAuth), embed with all-MiniLM-L6-v2 (384-dim, local). Stored in audio_embeddings (model_version="lyrics-minilm-v2"). Captures semantic content for Italian rap/drill.
- **Phase 4 — Smart polling**: Apple Music ratings (Love/Dislike), Spotify saved status check, smart play count delta detection between polling snapshots.

### Changed — Scorer Rebalanced to 6 Dimensions
- genre 25%, tags 15%, collab 10%, audio 20%, artist 15%, language 15%
- Tag similarity: cosine between Last.fm tag vectors
- Collaborative match: +0.20 boost when candidate in Last.fm getSimilar set
- Unused dimensions redistributed proportionally (graceful degradation)
- Context-adaptive weights updated for 6 dimensions
- Mood mode boosts audio (30%) + tags (25%)
- sentence-transformers as optional dependency (`pip install .[lyrics]`)

## V 3.310 — 2026-04-08

### Added
- Standalone admin dashboard service at admin.music.menghi.dev (FastAPI + static HTML)
- Admin password auth (ADMIN_PASSWORD env var, independent from main app accounts)
- X-Admin-Secret header auth for backend admin endpoints
- Per-user library vs worker song breakdown in admin stats
- Worker song enumeration logging before enrichment starts
- Global ISRC audio features cache (audio_features_global table, migration 013)
- Standalone enrichment worker with startup scan + featuring artist parse
- NocoDB at dbmanager.music.menghi.dev for raw database browsing

### Changed
- Dashboard loads instantly (<1s) — stale profile served immediately, refresh in background
- Enrichment bar only visible during calibration indexing (worker enrichment invisible)
- Taste profile staleTime 5min → 30min, services staleTime 0 → 5min
- Enrichment polling 15s → 30s, removed /me polling for enrichment
- enrichment-status counts library songs only (worker songs excluded from user view)
- Admin UI redesigned with light theme, Inter font, proper CSS variables
- Removed is_admin flag dependency from main frontend (admin is separate service)

### Fixed
- 5-minute dashboard load caused by blocking profile rebuild on stale cache
- useServices refetching on every navigation (staleTime was 0)

## V 3.240 — 2026-04-08

### Added
- NocoDB admin UI on Railway for browsing/querying both databases
- Separate PostgreSQL logging DB with batched async writer (request_logs, enrichment_logs, error_logs)
- Request logging middleware — every request logged with method, path, status, duration_ms, user_id
- Frontend error boundary (error.tsx) with retry button
- Global mutation error toast via MutationCache.onError
- 429 retry with exponential backoff in discovery strategies (_request_with_retry)
- Rate limits: taste 20/min, calibration 10/min, profile refresh 5/min

### Fixed
- Deezer call signature crash in analyzer.py (positional arg to keyword-only function)
- Spotify candidates scored 0 on genre/language (empty genre_names never backfilled)
- 18 httpx.AsyncClient() calls missing timeouts (infinite hang risk)
- 3 bare `except: pass` in enrichment pipeline replaced with debug logging
- Enrichment orchestrator unused variable removed

## V 3.130 — 2026-04-08

### Fixed
- Apple Music OAuth popup silently blocked — pre-load MusicKit JS + developer token on mount
- Apple Music OAuth silent failure — removed hard redirect from apiFetch 401 handler
- Per-step error toasts in Apple Music connect flow (token fetch, SDK load, configure, authorize)

## V 3.100 — 2026-04-07

### Added
- Context-adaptive scoring weights — shift based on user profile (regional concentration, audio availability, calibration, mood)
- Play count proxy — Apple Music workaround: seen_count from recently-played polling, up to 10x weight
- Engagement-based profile weighting (songs played recently get 2x recency boost)
- Calibration boost: continuous function min(0.20, weight * 0.03), zeroed on genre mismatch

### Changed
- Scoring weights rebalanced: genre 35%, audio 25%, artist 20%, language 20%
- Language match: non-regional songs score 0.5 (was 0.2 penalty)
- Layout renders immediately with skeleton UI (was blocking auth spinner)
- Enrichment polling 3s → 15s, /me calls 30s → 60s
- Calibration status staleTime 30s → 5min

## V 2.900 — 2026-04-01

### Added
- Onboarding taste calibration wizard: 3-step flow (playlists, artists drag-to-reorder, songs)
- Calibration API: GET /albums, GET /artists, POST /save, GET /status, GET /entries
- user_calibration DB table + Alembic migration 012
- CalibrationManager component in settings (view/edit selections)
- Dashboard calibration summary card
- Calibration-aware recommendation scoring
- Top 3 artists trigger background discography fetch + enrichment

### Fixed
- Calibration save timeout — profile rebuild moved to background task
- Calibration duplicate key crash — dedup by (calibration_type, item_id)
- Calibration redirect race — setQueryData instead of invalidateQueries

## V 2.800 — 2026-04-01

### Added
- Standalone enrichment worker with full artist discography + outlier detection
- 5x faster enrichment via concurrent processing (asyncio.Semaphore)
- Proxy rotation + Retry-After handling in worker
- Auto-load proxies from URLs + Geonode API

### Fixed
- Failed tracks retry on next cycle instead of being permanently skipped
- Split collab artist names for Deezer search (21/140 → ~100+/150)
- ReccoBeats always direct (no proxy), concurrency 10 to avoid 429
- All Deezer requests go direct (proxies killing 138/140 artists)
- Worker handles genre_names stored as scalar strings
- Cast genre_names to text for GROUP BY (json has no equality operator)
- Count only real enrichments + dedup in worker
- Add greenlet to worker requirements

## V 2.500 — 2026-03-31

### Added
- Two-pass scoring: fast metadata pass → lazy audio enrichment → re-score
- User-visible enrichment progress bar with auto-continue
- Indexing status in progress bar with done/total counter
- Listening-based artist affinity (library 0.3, play 3.0, repeat 5.0, love +4.0)
- Library exclusion from recommendations (2-year window for rediscovery)
- Artist discography pre-caching for top 10 artists
- Transparent logos + color favicon

### Fixed
- Enrichment no longer gets stuck on unfindable tracks
- Enrichment batch increased to 50
- OOM crash + ReccoBeats rate limiting

## V 2.200 — 2026-03-31

### Added
- ReccoBeats audio analysis — upload 30s preview, get 9 features (free)
- Auto-enrich library on login and service connection
- Admin system with live SSE log stream, enrichment progress, dev/user toggle
- Sidebar recommendations, playlist scroll + per-playlist recs

### Changed
- Scorer rebalanced: language 45%, audio 32%, genre 13%, artist 10%
- Deezer enrichment via search (ISRC lookup broken)

### Fixed
- Docker build — revert Hatch dynamic version to static
- Stricter genre filtering, distinct chart colors

## V 2.100 — 2026-03-30

### Added
- Audio enrichment pipeline: Deezer → ReccoBeats → SoundStat (3-tier cascade)
- Featuring artist weighting in taste profile (primary 1.0, feat 0.3)
- Dashboard visualizations: genre donut, artist bars, audio radar, release year chart
- Listening timeline with chronological song view and date badges
- On-demand Essentia deep analysis (128-dim Discogs-EffNet embeddings)
- Playlists page with real Apple Music/Spotify playlists + per-playlist recs
- Synesthesia visual identity — purple/violet theme, Sora + DM Sans fonts

### Fixed
- Frontend strategy names aligned with backend validation
- Apple Music listening stats no longer empty
- Recommendations respect user's regional storefront

## V 2.000 — 2026-03-30

### Added
- Rebrand MusicMind → SmarTaste
- Centralized VERSION file
- Comprehensive what.md documentation
- Vercel + Railway deployment support
- Base64-encoded Apple .p8 key support

### Removed
- ~1,350 lines of dead code (bandit, CLAP mood, Last.fm, knowledge graph, extractor)

### Fixed
- Railway PostgreSQL connection failure
- Auto-generate FERNET_KEY and JWT_SECRET on first deploy
- Missing rate_limit.py module crash
- Dockerfile Python 3.14 for uuid7

## V 1.200 — 2026-03-28

### Added
- Multi-LLM support: Claude + OpenAI GPT-4o (Phase 12)
- LLM provider abstraction with tool converter
- OpenAI BYOK key manager + model selector in frontend

### Fixed
- Chat system prompt crash — top_artists is list of dicts
- Nested button hydration error in conversation sidebar
- Model selector dropdown stays open

## V 1.100 — 2026-03-28

### Added
- MusicKit JS Apple Music authorization flow
- BYOK key setup UX with link to Anthropic Console

### Fixed
- CORS, API proxy, sandbox mode, missing migration, empty config guards
- MusicKit JS v3 initialization (musickitloaded event + async configure)
- Comprehensive production fixes for all features
- Hosting/tunnel issues for Cloudflare deployment
- CSRF exemptions for Apple Music connect, Spotify callback, chat
- Disabled CSRF middleware (redundant behind same-origin proxy)

## V 1.000 — 2026-03-27

### Added
- Full webapp: 12 phases of development
- Phase 01: Monorepo structure, Settings, EncryptionService
- Phase 02: Auth system (signup, login, logout, refresh, me)
- Phase 03: Spotify + Apple Music service connections (OAuth PKCE, MusicKit JS)
- Phase 04: Claude BYOK (bring your own key) with Fernet encryption
- Phase 05: Taste profile pipeline (fetch → cache → compute → snapshot)
- Phase 06: Listening stats (top tracks, artists, genres by time period)
- Phase 07: Recommendation engine (4 discovery strategies, MMR scoring)
- Phase 08: Multi-service unification (ISRC dedup, genre normalization)
- Phase 09: Claude chat with agentic loop, SSE streaming, tool registry
- Phase 10: Score breakdown + audio features detail views
- Phase 11: Next.js frontend (dashboard, recommendations, playlists, chat, settings)
- 23 PostgreSQL tables, 12 Alembic migrations, 370+ tests

### Infrastructure
- FastAPI + SQLAlchemy Core + asyncpg
- Next.js 16 + React 19 + Tailwind 4 + shadcn/ui
- Cookie-based JWT auth with automatic token refresh
- Background library sync + enrichment pipeline

## V 0.200 — 2026-03-26

### Added
- Adaptive recommendation engine with feedback, audio analysis, mood filtering
- Regional genre handling, weight rebalancing, discovery noise reduction

## V 0.100 — 2026-03-25

### Added
- Initial MCP server: Apple Music API client, SQLite persistence
- 21 MCP tools for library, catalog, playback, management
- Taste engine with profile builder, scorer, discovery strategies
- Smart recommendation tools (taste, discover, smart playlist)
