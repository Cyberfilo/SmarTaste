# Changelog

All notable changes to SmarTaste are documented here.

Versioning: **V X.YZA** where X=major, Y=small logic, Z=minor, A=bugfix.
When A reaches 10 → Z+1 (A resets to 0). When Z reaches 10 → Y+1. Etc.

---

## V 5.200 — 2026-04-09

### Reliability Sprint — 32 Fixes Across 20 Files

#### Enrichment Priority & Smart Worker
- **Library-first enrichment**: Worker now fills user library gaps BEFORE cobweb/global songs. If 218 library songs are missing, exactly those 218 get enriched first.
- **ISRC backfill phase**: 807 library songs had NULL ISRC. New worker phase resolves ISRCs via Deezer search (100/cycle). Deezer track response now extracts ISRC.
- **Cobweb total cap**: Artist cobweb capped at 50% of library artists (was unbounded — grew to 194 for 446 library songs).
- **Worker yields to active indexing**: Checks `user_indexing_status` before processing each user. No more API competition between worker and backend indexer.
- **Skip permanently failed enrichments**: Songs with `no_data_available` marker no longer retried every cycle.
- **Indexer WHERE clause fix**: Last.fm suggested artists query now filters by THIS user's library artists (was querying all users' data globally).
- **Last.fm tags UPSERT**: `ON CONFLICT DO UPDATE` replaces `DO NOTHING` — stale tags now refresh on re-fetch.

#### Scoring & Recommendation Engine
- **Collab boost continuous**: Was binary (0 or 1) due to `*5.0` scaling. Now proportional 0.0-0.20.
- **Weight redistribution renormalized**: When audio/tags unavailable, redistributed weights now renormalize to sum=1.0 (prevented score inflation).
- **Regional weights proportional**: 90% Italian = 40% language weight (was capped at 22.5%). 10% global = 3% language. Continuous scaling via `(strength - 0.3)^1.5`.
- **MMR diversity fix**: Base score was computed without diversity, but adjustment subtracted `diversity_weight * 1.0` — incorrectly penalizing every candidate. Now simply subtracts `diversity_weight * penalty`.
- **Mood genre matching**: Uses word-boundary/slash-split matching instead of substring containment. "Pop" no longer matches "Rock Pop".
- **Audio centroid NaN filtering**: NaN values from bad API data no longer corrupt the weighted centroid.
- **Genre normalize None**: Returns `""` instead of `None` (prevented downstream `.lower()` crashes).
- **Library filter NULL dates**: Songs with NULL `date_added_to_library` treated as old (allow rediscovery) instead of assumed recent (excluded).

#### Spotify Support
- **Token auto-refresh in worker + indexer**: Spotify tokens expire in 1 hour. Both now check `token_expires_at` and refresh via `refresh_spotify_token()` before API calls.
- **Market detection**: Fetches user's `country` from Spotify profile and uses it as `market` parameter. Italian Spotify users get Italian charts.
- **Editorial discovery fix**: Uses `genre:rock year:2024-2026` Spotify search syntax instead of `"best new rock"` (literal text match).
- **Genre backfill**: `discover_genre_adjacent` batch-fetches artist genres via `/artists?ids=` endpoint. Spotify tracks now get proper genre metadata.
- **Top-tracks market parameter**: Required `market` param added per Spotify API spec.

#### Infrastructure
- **DB indexes (Alembic 018)**: Standalone `user_id` indexes on 8 tables. Per-user queries go from O(n) table scan to O(log n) index lookup.
- **Connection pooling**: Shared `httpx.AsyncClient` with `max_connections=20` across all discovery strategies. Was creating 4+ separate TCP+SSL connections per recommendation request.
- **Retry on all API calls**: `_request_with_retry()` (was defined but never called) now wired into all 13 API call sites. Exponential backoff on 429/5xx.
- **Async-safe indexing locks**: `asyncio.Lock` per user replaces plain `dict[str, bool]` (had TOCTOU race condition).
- **Taste rebuild status persisted**: Moved from in-memory `_rebuild_status` dict to `user_indexing_status` table (step=99). Survives server restarts.
- **Token decryption safety**: `decrypt()` raises `ValueError` with clear message. New `decrypt_or_none()` for background tasks. Worker/indexer handle corrupted tokens gracefully.
- **Admin N+1 fix**: `get_enrichment_progress()` replaced 8N queries (8 per user in loop) with 5 batched GROUP BY queries.
- **12+ silent `except: pass`** replaced with `logger.debug/warning` calls.
- **Cobweb stats use rowcount**: Only counts actual INSERTs, not CONFLICT DO NOTHING no-ops.

#### Logging
- **DatabaseLogHandler**: New `logging.Handler` subclass forwards all Python logs to the log DB. WARNING+ from all loggers, INFO+ from `musicmind.*` namespace. Wired into both worker and main app.
- All logs visible on Railway are now also persisted to the `error_logs` table.

#### Admin Dashboard
- **New `/api/admin/diagnostics` endpoint**: Per-user, per-stage enrichment breakdown with failure analysis.
- **Smart Diagnostics UI section**: Shows per-user cards with enriched/failed/pending/missing-ISRC counts. Library vs non-library audio split. Actionable insights (CRITICAL/WARNING/INFO). Today's failure breakdown from logs DB.

#### Frontend
- **localStorage key unified**: `use-chat.ts` now reads/writes `musicmind-preferred-model` (was split between two keys — model selection in Settings didn't persist to Chat).

---

## V 5.110 — 2026-04-08

### Fixed — Tags + Credits Not Completing
- **Root cause**: Gap detection used O(n) individual queries per song. With 3,100 songs = 3,100+ SELECT queries just to find what's missing. Now uses single batch `IN` query.
- **Last.fm backfill now concurrent**: 5 simultaneous API calls via `asyncio.Semaphore(5)` instead of sequential. ~5x faster.
- **Worker backfills ALL songs** (per-user + global) not just 200 global songs per cycle.
- **MusicBrainz gap detection batched**: single `IN` query for all ISRCs instead of per-song check.

---

## V 5.100 — 2026-04-08

### Added
- **Indexer auto-trigger on login**: `/api/auth/me` checks if user's library is fully indexed. If not, triggers `run_indexing()` as background task. Skips if already complete or in progress (in-memory lock per user).
- **Orchestrator respects env vars**: `WORKER_CONCURRENCY` and `WORKER_BATCH_SIZE` now configurable via environment (was hardcoded 5/20). Set to 15/100 on Railway for 8 vCPU.

---

## V 5.000 — 2026-04-08

### Architecture Split — Indexer vs Worker

Major refactor separating enrichment into two independent systems:

#### Per-User Indexer (`indexer.py` — NEW)
Backend-managed, prioritized, user-scoped enrichment pipeline:
1. **Library songs** — enrich all songs in the user's library (100%)
2. **Top artist** — 100% discography enriched
3. **2nd artist** — 70% discography
4. **3rd artist** — 50% discography
5. **Other library artists** — 30% discography each
6. **Suggested artists** — `library_artists * 0.4` new artists from feats + similar tracks, 50% discography

Artists ranked by calibration weight then play frequency. All results stored with user_id in per-user tables.

#### Global Worker (`worker.py` — REWRITE)
Standalone Railway service, always running, global enrichment:
- Builds **artist cobweb** per user (from feats, Last.fm similar, genre overlap)
- Enriches each cobweb artist's **top 50 songs** globally — no user_id
- Stores in `global_song_cache` + `audio_features_global` (ISRC-keyed)
- Promotes featured artists who appear alongside library artists
- Caps discovered artists at `library_artists * 0.4` per user
- Results available to ALL users for recommendation scoring

#### New Tables (migration 017)
- `user_indexing_status` — tracks per-user indexing step/progress
- `artist_cobweb` — per-user artist network (source, priority, enriched status)
- `global_song_cache` — worker-discovered songs without user_id

#### Enrichment Pipeline
- Now **3 stages** (lyrics embeddings removed): audio features → Last.fm tags → MusicBrainz credits
- New `enrich_tracks_global()` in orchestrator — writes only to `audio_features_global`, skips per-user cache
- Both systems check caches before API calls — no duplicate requests

#### Admin Dashboard
- Per-user progress shows indexing step indicator + cobweb stats
- Worker status shows global cobweb building phase
- "Discography" label replaces "Worker" in per-user breakdown

---

## V 4.200 — 2026-04-08

### Added — Worker Heartbeat
- **`worker_status` table** (migration 016): Single-row table the worker updates with current phase, progress, cycle count, and timestamp. Written to the main DB so the admin dashboard can read it directly.
- **Live worker status panel**: Visual status bar showing current phase (cleanup/startup_scan/backfill/discovering/idle) with colored pulse dot, cycle count, detail text, and progress bar when available. Updates every 10s via dashboard polling.
- **Worker heartbeat calls**: `_set_status()` called at every phase transition — cleanup, startup scan, backfill, discovering, idle.

### Fixed
- **`/api/admin/status` 5-7s SLOW**: Was calling `get_enrichment_progress()` (expensive per-user orphan-proof subqueries). Now uses fast direct counts in a single connection. Should be <100ms.
- **Live logs only showed backend** — worker is a separate process, its logs never reached the backend's `AdminLogHandler`. The heartbeat system solves this — worker reports phase/progress to the main DB, dashboard reads it directly.

---

## V 4.140 — 2026-04-08

### Changed — Admin Dashboard
- **Cookie login persistence**: Login survives admin service restarts — uses HMAC-signed cookie derived from ADMIN_PASSWORD instead of in-memory session tokens. Cookie valid for 30 days.
- **Live logs enhanced**: Worker activity highlighted with colored left border (blue for enrichment, yellow for scans). Song list entries indented. Message counter shows total received. Log buffer increased to 500 lines. Log feed height increased to 400px.
- **Auto-refresh on worker events**: Dashboard data auto-refreshes 2s after detecting worker cycle completion or backfill in the live log stream (no more waiting for the 10s poll).
- **Polling reduced**: 30s → 10s for KPI/progress data.

---

## V 4.130 — 2026-04-08

### Changed
- **Removed lyrics embeddings** from enrichment pipeline — 3-stage pipeline now (audio features → Last.fm tags → MusicBrainz credits). Lyrics/Genius/sentence-transformers deferred to future phase.
- **Backfill runs every cycle** — partially enriched songs (have audio but missing tags/credits) get completed on every worker cycle, not just startup. This ensures the 1,193 partially enriched songs get their Last.fm tags and MusicBrainz credits filled.
- **Pipeline breakdown shows 3 stages**: "Fully enriched" now means all 3 stages complete (audio + tags + credits).

---

## V 4.120 — 2026-04-08

### Fixed — Data Integrity
- **Enrichment count mismatch (407% bug)**: `audio_features_cache` had orphaned rows from the 148K song cleanup — songs were deleted from `song_metadata_cache` but their audio feature rows remained. Enriched counts now only count songs that still exist (JOIN with song_metadata_cache). Root cause: the cleanup SQL only targeted one table.
- **Enrichment percentage capped at 100%**: Both backend and frontend now cap at 100% instead of showing impossible values like 407%.
- **Negative unenriched count**: `unenriched` now uses `max(0, ...)` to prevent negative display values.

### Added
- **Orphan cleanup**: Worker runs `cleanup_orphaned_features()` on startup — deletes `audio_features_cache` rows with no matching song. Audio data is already preserved in `audio_features_global` (by ISRC). Also available as `POST /api/admin/cleanup-orphans` with button in admin dashboard.
- **Library vs discovered artists**: Per-user progress now shows "Artists: X library + Y discovered" instead of a single total count.
- **Orphan count display**: Per-user progress shows orphan count in red when > 0.
- **Worker logging**: `_startup_scan` and `_backfill_new_signals` now write to `enrichment_logs` (previously only `_process_user` logged). Every cycle logs even when idle. Worker status should now always show activity.
- **Worker status diagnostics**: When worker status shows "no logs", the dashboard lists possible causes (missing env vars, deployment, connectivity).
- **Cleanup button**: "Clean N orphans" button appears in per-user enrichment section when orphaned rows exist.

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
