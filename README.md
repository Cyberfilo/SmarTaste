<p align="center">
  <img src="https://img.shields.io/badge/version-6.367-8b5cf6?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Next.js-16-000000?style=for-the-badge&logo=nextdotjs&logoColor=white" alt="Next.js">
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
</p>

# SmarTaste <sup><sub>formerly MusicMind</sub></sup>

> Your music, understood. A music discovery platform that connects to Spotify and Apple Music, analyzes your actual listening data, and delivers genuinely personalized recommendations — powered by a 6-dimension scoring engine, 4-stage audio/metadata enrichment pipeline, and conversational AI.

**Live:** [music.menghi.dev](https://music.menghi.dev) &middot; **Admin:** [admin.music.menghi.dev](https://admin.music.menghi.dev) &middot; **DB Browser:** [dbmanager.music.menghi.dev](https://dbmanager.music.menghi.dev)

---

## Features

| Feature | Description |
|---------|-------------|
| **Taste Profile** | Your musical DNA — top genres (with regional specificity like "Italian Hip-Hop/Rap"), artists with featuring detection, audio traits, embedding centroids, familiarity score |
| **6-Dim Recommendations** | CLAP + MERT + EffNet embeddings + genre cosine + scalar audio + artist affinity — context-adaptive weights shift per user |
| **4-Stage Enrichment** | Automatic pipeline: Essentia (CPU) scalar features + EffNet embeddings, Modal GPU (CLAP + MERT), Deezer preview fallback, OpenAI captions |
| **Taste Calibration** | 3-step onboarding wizard: pick playlists, rank artists (drag-to-reorder), pick favorite songs — compensates for Apple Music's missing play counts |
| **AI Chat** | Ask Claude or GPT about your taste, get recommendations by description, explore music conversationally (BYOK — bring your own API key) |
| **Playlists** | Browse your real Apple Music / Spotify playlists with per-playlist recommendations |
| **Listening Timeline** | Chronological song view with date labels |
| **Multi-Service** | Connect Spotify and/or Apple Music — unified profiles with ISRC dedup and genre normalization |
| **Admin Dashboard** | Standalone React dashboard on its own Railway service (admin.music.menghi.dev): songs + artists drill-down tables, worker heartbeat, diagnostics, SSE log stream |
| **Background Worker** | Artist cobweb discovery, global enrichment, ISRC backfill via free APIs (Deezer + MusicBrainz), preview audio caching |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (Vercel)                                          │
│  Next.js 16 · React 19 · Tailwind 4 · shadcn/ui            │
│  TanStack Query · Zustand · Recharts · SSE streaming        │
└────────────────────────┬────────────────────────────────────┘
                         │ REST + SSE
┌────────────────────────┴────────────────────────────────────┐
│  Backend (Railway)                                          │
│  FastAPI · SQLAlchemy Core · asyncpg · Alembic · Pydantic   │
│                                                             │
│  ┌─── API Layer (13 routers) ────────────────────────────┐  │
│  │ Auth · Taste · Stats · Recommendations · Playlists     │  │
│  │ Chat · Services · Calibration · Admin · Tracks         │  │
│  └────────────────────────────────────────────────────────┘  │
│  ┌─── Indexer (per-user, prioritized) ───────────────────┐  │
│  │ 1. Library songs → 100% enriched                       │  │
│  │ 2. Top artist → 100% discography                       │  │
│  │ 3. 2nd artist → 70% · 3rd → 50% · rest → 30%          │  │
│  │ 4. Suggested artists (40% of library count) → 50%      │  │
│  └────────────────────────────────────────────────────────┘  │
│  ┌─── Engine Layer ──────────────────────────────────────┐   │
│  │ 6-dim Scorer (CLAP/MERT/EffNet/genre/scalar/artist)    │  │
│  │ Profile Builder · Adaptive Weights · Mood Filter       │  │
│  └────────────────────────────────────────────────────────┘  │
│  ┌─── Data Layer ────────────────────────────────────────┐   │
│  │ PostgreSQL 16 · 22 tables · user-scoped + global       │  │
│  │ + Logging DB (3 tables: requests, enrichment, errors)  │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
         ↕                    ↕                    ↕
   Spotify API          Apple Music API      Anthropic/OpenAI

┌──────────────────┐  ┌──────────────┐  ┌──────────────┐
│  Worker (Railway)  │  │  Admin       │  │  NocoDB      │
│  Artist cobweb     │  │  Dashboard   │  │  DB browser  │
│  Global enrichment │  │  (standalone)│  │  (standalone) │
│  No user_id        │  └──────────────┘  └──────────────┘
│  Top 50 per artist │
└──────────────────┘
```

### Two Enrichment Systems

| | **Indexer** (per-user) | **Worker** (global) |
|---|---|---|
| **Runs in** | Backend (background task) | Separate Railway service |
| **Triggered by** | Service connection, manual | Always running |
| **Stores in** | `song_metadata_cache` + `audio_features_cache` (user-scoped) | `global_song_cache` + `audio_features_global` (no user_id) |
| **Artist selection** | Library artists ranked by play count/calibration | Artist cobweb (feats, similar, genre overlap) |
| **Depth** | 100% → 70% → 50% → 30% (decreasing by rank) | Top 50 per artist |
| **Suggested cap** | `library_artists * 0.4` new artists | Same cap on cobweb |
| **Dashboard** | Per-user enrichment section | Worker status section |

## Recommendation Engine

**6-dimension embedding-based scorer** — weights shift based on user profile characteristics:

| Dimension | Default | Source |
|-----------|---------|--------|
| CLAP cosine | **30%** | 512-dim semantic audio embedding (Modal GPU) |
| MERT cosine | **25%** | 768-dim musical structure embedding (Modal GPU) |
| EffNet cosine | **15%** | 1280-dim timbral fingerprint (Essentia ONNX, CPU) |
| Genre match | **15%** | Cosine similarity with regional prioritization (Italian Hip-Hop/Rap at 1.0, parent at 0.3) |
| Scalar audio | **10%** | Euclidean on tempo/energy/danceability (Essentia CPU) |
| Artist affinity | **5%** | Library presence + calibration ranking + featuring parse |

Plus additive bonuses: calibration boost (+0.03 to +0.20, zeroed on genre mismatch), diversity penalty (MMR), staleness cooldown, cross-strategy bonus, mood boost.

Missing dimensions redistributed proportionally — graceful degradation when embeddings are unavailable.

## Enrichment Pipeline (4 stages)

Shared by both indexer and worker. Each stage checks cache before making API calls — no duplicate requests. Preview audio cached locally to survive Deezer URL expiry.

| Stage | Source | What it provides | Cost |
|-------|--------|-----------------|------|
| 1 | **Essentia (CPU, Railway)** | 11 scalar features (tempo, energy, danceability, etc.) + 1280-dim EffNet ONNX embedding + classifier heads (mood, genre, acousticness) | Free |
| 2 | **Modal GPU (serverless A100)** | 512-dim CLAP embedding (semantic audio-text) + 768-dim MERT embedding (musical structure) | ~$0.018/track |
| 3 | **Deezer (fallback)** | Preview URL resolution via ISRC lookup, BPM from track metadata | Free |
| opt | **OpenAI** | AI-generated track captions from extracted features | BYOK |

Global ISRC cache (`audio_features_global`, `audio_embeddings_global`): features enriched once per song, shared across all users. ISRC backfill via Deezer + MusicBrainz free APIs.

## Quick Start

### Sandbox Mode (no API keys needed)

```bash
git clone https://github.com/Cyberfilo/SmarTaste.git && cd SmarTaste

# Database
docker compose up db -d

# Backend
cd backend && uv sync --dev
MUSICMIND_SANDBOX=true uv run alembic upgrade head
MUSICMIND_SANDBOX=true uv run uvicorn musicmind.app:app --reload --port 8000

# Frontend (new terminal)
cd frontend && npm install && npm run dev
```

Open **http://localhost:3000** — sign up, explore the dashboard. Music service features require API keys.

### Cloud Deploy (Vercel + Railway)

| Component | Platform | Setup |
|-----------|----------|-------|
| Frontend | **Vercel** | Import repo → auto-detects `rootDirectory: frontend` → set `NEXT_PUBLIC_API_URL` |
| Backend | **Railway** | Add PostgreSQL service → add GitHub service (backend/) → set env vars |
| Worker | **Railway** | Separate service from worker/ directory → same DATABASE_URL + FERNET_KEY |
| Admin | **Railway** | Separate service from admin/ directory (two-stage Dockerfile builds the Next.js dashboard then serves it from FastAPI) → set ADMIN_PASSWORD + ADMIN_SECRET + BACKEND_URL |
| Logs DB | **Railway** | Second PostgreSQL instance → set MUSICMIND_LOGS_DATABASE_URL on backend + worker |
| NocoDB | **Railway** | Docker image `nocodb/nocodb` → separate metadata PostgreSQL |
| Migrations | Auto | Dockerfile runs `alembic upgrade head` on every deploy |

## Environment Variables

All backend variables use the `MUSICMIND_` prefix. See [`.env.example`](.env.example) for the full list.

| Variable | Required | Description |
|----------|:--------:|-------------|
| `MUSICMIND_DATABASE_URL` | Yes | PostgreSQL asyncpg connection string |
| `MUSICMIND_FERNET_KEY` | Yes* | Fernet encryption key for secrets at rest |
| `MUSICMIND_JWT_SECRET_KEY` | Yes* | JWT HS256 signing secret |
| `MUSICMIND_SANDBOX` | — | `true` for dev defaults (skips key requirements) |
| `MUSICMIND_SPOTIFY_CLIENT_ID` | — | Spotify OAuth client ID |
| `MUSICMIND_SPOTIFY_CLIENT_SECRET` | — | Spotify OAuth client secret |
| `MUSICMIND_APPLE_TEAM_ID` | — | Apple Developer Team ID |
| `MUSICMIND_APPLE_KEY_ID` | — | MusicKit key ID |
| `MUSICMIND_APPLE_PRIVATE_KEY_PATH` | — | Path to `.p8` key file (or `_B64` for base64) |
| `MUSICMIND_LOGS_DATABASE_URL` | — | Separate PostgreSQL for request/enrichment/error logs |
| `MUSICMIND_LASTFM_API_KEY` | — | Last.fm API key (free, enables tags + collaborative filtering) |
| `MUSICMIND_SOUNDSTAT_API_KEY` | — | SoundStat API key for premium enrichment |
| `MUSICMIND_ADMIN_SECRET` | — | Shared secret for admin dashboard → backend auth |

*Auto-generated on first Docker deploy if not set.

### Worker
| Variable | Description |
|----------|-------------|
| `WORKER_CONCURRENCY` | Tracks enriched in parallel (default 5) |
| `WORKER_BATCH_SIZE` | Tracks per enrichment batch (default 50) |
| `WORKER_POLL_INTERVAL` | Seconds between cycles (default 60) |
| `WORKER_ARTIST_DEPTH` | Top songs per artist to fetch (default 25) |

### Admin Dashboard
| Variable | Description |
|----------|-------------|
| `ADMIN_PASSWORD` | Login password for admin UI |
| `ADMIN_SECRET` | Shared secret sent as `X-Admin-Secret` header to backend |
| `BACKEND_URL` | Backend URL to proxy API requests to |

### NocoDB
| Variable | Description |
|----------|-------------|
| `NC_DB` | Internal PostgreSQL URL for NocoDB metadata storage |
| `NC_AUTH_JWT_SECRET` | JWT secret for NocoDB admin auth |
| `NC_PUBLIC_URL` | Public URL (set after generating Railway domain) |

<details>
<summary><b>Full API endpoint list (57 endpoints)</b></summary>

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/signup` | Create account |
| POST | `/api/auth/login` | Log in (sets JWT cookies) |
| POST | `/api/auth/logout` | Clear cookies |
| POST | `/api/auth/refresh` | Refresh access token |
| GET | `/api/auth/me` | Current user info + triggers background sync |

### Taste Profile
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/taste/profile` | Full taste profile (stale-while-revalidate) |
| GET | `/api/taste/genres` | Top genres |
| GET | `/api/taste/artists` | Top artists (with featuring detection) |
| GET | `/api/taste/audio-traits` | Audio preferences |
| GET | `/api/taste/enrichment-status` | Library enrichment progress (library songs only) |

### Listening Stats
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/stats/tracks?period=month` | Top tracks by period |
| GET | `/api/stats/artists?period=6months` | Top artists by period |
| GET | `/api/stats/genres?period=alltime` | Top genres by period |
| GET | `/api/stats/timeline` | Chronological song timeline |

### Recommendations
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/recommendations?strategy=all` | Get scored recommendations |
| POST | `/api/recommendations/{id}/feedback` | Thumbs up/down |
| GET | `/api/recommendations/{id}/breakdown` | 6-dimension score breakdown |

### Playlists
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/playlists` | User's service playlists |
| GET | `/api/playlists/{id}/tracks?service=` | Playlist tracks |
| GET | `/api/playlists/{id}/recommendations?service=` | Per-playlist suggestions |

### Calibration
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/calibration/albums` | User's playlists for calibration |
| GET | `/api/calibration/artists` | User's artists for ranking |
| POST | `/api/calibration/save` | Save calibration selections |
| GET | `/api/calibration/status` | Calibration completion status |
| GET | `/api/calibration/entries` | Current calibration entries |

### Tracks
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tracks/{id}/audio-features` | Audio feature radar |
| POST | `/api/tracks/analyze` | On-demand Essentia analysis (max 10) |

### Chat
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/message` | Send message (SSE stream) |
| GET | `/api/chat/conversations` | List conversations |
| GET | `/api/chat/conversations/{id}` | Load conversation |
| DELETE | `/api/chat/conversations/{id}` | Delete conversation |

### Admin (requires `X-Admin-Secret` or `is_admin`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin/logs` | Recent log entries (from memory buffer) |
| GET | `/api/admin/logs/stream` | Live SSE log stream |
| GET | `/api/admin/progress` | Per-user enrichment progress |
| GET | `/api/admin/status` | System health summary |
| GET | `/api/admin/errors` | Recent 500 errors from logs DB |
| GET | `/api/admin/request-stats` | Requests/errors/slow today |
| GET | `/api/admin/recent-songs` | Latest cached songs with enrichment status |
| GET | `/api/admin/enrichment-breakdown` | Pipeline-level: unenriched / partial / fully enriched |
| GET | `/api/admin/db-capacity` | Database size usage (main + logs) |
| GET | `/api/admin/worker-status` | Worker activity from enrichment_logs |
| GET | `/api/admin/diagnostics` | Smart per-user per-stage enrichment breakdown with failure analysis |
| POST | `/api/admin/cleanup-orphans` | Delete orphaned audio_features_cache rows |

</details>

## Project Structure

```
smartaste/
├── backend/
│   ├── src/musicmind/
│   │   ├── api/                    # REST endpoints (13 routers)
│   │   │   ├── admin/              # Live logs, progress, status, worker, capacity
│   │   │   ├── calibration/        # Onboarding wizard API
│   │   │   ├── playlists/          # Service playlist fetching
│   │   │   ├── chat/               # Claude/GPT conversation + SSE
│   │   │   └── ...                 # Auth, taste, stats, recs, tracks, services, session
│   │   ├── auth/                   # JWT + bcrypt auth + admin roles
│   │   ├── engine/
│   │   │   ├── scorer.py           # 6-dimension scoring (context-adaptive)
│   │   │   ├── profile.py          # Taste profile + featuring artist parser
│   │   │   ├── weights.py          # Context-adaptive weight computation
│   │   │   ├── mood.py             # Mood filtering (8 profiles)
│   │   │   ├── audio/              # On-demand Essentia + cache layer
│   │   │   ├── enrichment/         # 3-stage pipeline (shared by indexer + worker)
│   │   │   │   ├── orchestrator.py # Deezer → ReccoBeats → SoundStat cascade
│   │   │   │   ├── lastfm.py       # Tags + similar tracks (collaborative)
│   │   │   │   ├── musicbrainz_credits.py  # Producer/songwriter credits
│   │   │   │   ├── genius.py       # Lyrics scrape + MiniLM embedding
│   │   │   │   ├── acousticbrainz.py       # Bulk mood/genre features
│   │   │   │   └── ...             # deezer.py, reccobeats.py, soundstat.py, musicbrainz.py
│   │   │   └── ...                 # genres.py, dedup.py, similarity.py, session.py
│   │   ├── db/
│   │   │   ├── schema.py           # 29-table SQLAlchemy Core schema
│   │   │   └── logs.py             # 3-table logging DB + batched async writer
│   │   ├── security/               # Fernet encryption
│   │   ├── indexer.py              # Per-user prioritized enrichment (6-step pipeline)
│   │   └── worker.py               # Global artist cobweb enrichment (no user_id)
│   ├── alembic/                    # 21 database migrations
│   ├── tests/                      # 92 tests
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/                    # Pages: dashboard, recommendations, playlists, chat, settings, onboarding
│   │   ├── components/             # 34 components (shadcn/ui + charts + calibration wizard)
│   │   ├── hooks/                  # 10 TanStack Query hooks
│   │   ├── stores/                 # Zustand auth store
│   │   └── lib/                    # API client, SSE parser, utils
│   └── package.json
├── admin/                          # Standalone admin dashboard (FastAPI gate + React UI)
│   ├── app.py                      # Password HMAC cookie + /api/* proxy + SSE streaming proxy
│   ├── templates/login.html        # Jinja login page (only)
│   ├── ui/                         # Next.js 16 static export (built at image time, served from /app/static)
│   │   ├── app/                    #   page.tsx + layout + providers (TanStack Query)
│   │   ├── components/admin/       #   songs-table, artists-table, status-dot
│   │   ├── components/ui/          #   4 shadcn primitives (card/badge/button/skeleton)
│   │   ├── hooks/use-admin-tables.ts
│   │   └── lib/                    #   minimal api.ts (same-origin fetch) + cn helper
│   └── Dockerfile                  # Two-stage: node build → python runtime
├── worker/                         # Standalone enrichment worker (Docker wrapper)
│   ├── enrichment_worker.py
│   └── Dockerfile
├── scripts/
│   └── import_acousticbrainz.py    # Bulk import AcousticBrainz CC0 dump
├── VERSION                         # Single source of truth (5.200)
├── CHANGELOG.md
├── what.md                         # Full technical documentation
├── docker-compose.yml
└── .env.example
```

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Frontend** | Next.js 16 &middot; React 19 &middot; TypeScript &middot; Tailwind CSS 4 &middot; shadcn/ui &middot; TanStack Query &middot; Zustand &middot; Recharts &middot; Sora + DM Sans |
| **Backend** | Python 3.11+ &middot; FastAPI &middot; SQLAlchemy Core &middot; asyncpg &middot; Alembic &middot; Pydantic &middot; bcrypt &middot; PyJWT &middot; Fernet |
| **AI** | Anthropic SDK &middot; OpenAI SDK (BYOK) &middot; sentence-transformers (MiniLM-L6-v2 for lyrics) |
| **Music APIs** | Spotify Web API (OAuth PKCE) &middot; Apple Music API (MusicKit JS + ES256 JWT) |
| **Enrichment** | Deezer (free) &middot; ReccoBeats (free) &middot; Last.fm (free) &middot; MusicBrainz (free) &middot; Genius (free) &middot; SoundStat (paid) &middot; AcousticBrainz (bulk) |
| **Infrastructure** | PostgreSQL 16 &middot; Docker Compose &middot; Vercel (frontend) &middot; Railway (backend + worker + admin + 2x Postgres + NocoDB) &middot; uv + npm |

## Tests

```bash
cd backend && uv run python -m pytest tests/ -v
```

92 tests covering: auth, service connections, BYOK keys, taste profiles, stats, recommendations, multi-service unification, Claude/OpenAI chat, genre normalization, track deduplication, audio pipeline, scoring dimensions.

## License

MIT — Copyright 2026 [Filippo Mattia Menghi](https://github.com/Cyberfilo)
