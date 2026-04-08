<p align="center">
  <img src="https://img.shields.io/badge/version-3.310-8b5cf6?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Next.js-16-000000?style=for-the-badge&logo=nextdotjs&logoColor=white" alt="Next.js">
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
</p>

# SmarTaste <sup><sub>formerly MusicMind</sub></sup>

> Your music, understood. A music discovery platform that connects to Spotify and Apple Music, analyzes your actual listening data, and delivers genuinely personalized recommendations — powered by language-aware scoring, real audio analysis, and conversational AI.

**Live:** [music.menghi.dev](https://music.menghi.dev) · **Docs:** [`what.md`](what.md) · *Previously known as MusicMind*

---

## Features

| Feature | Description |
|---------|-------------|
| **Taste Profile** | Your musical DNA — top genres (with regional specificity), artists with featuring detection, audio traits, familiarity score |
| **Smart Recommendations** | Language-first scoring (Italian Hip-Hop ≠ generic Hip-Hop) + audio similarity from Deezer/ReccoBeats analysis |
| **Playlists** | Browse your real Apple Music / Spotify playlists with per-playlist recommendations |
| **Taste Calibration** | 3-step onboarding wizard: pick playlists, rank artists (drag-to-reorder), pick favorite songs — compensates for Apple Music's missing play counts |
| **Audio Enrichment** | Automatic: Deezer preview → ReccoBeats analysis → 9 audio features per track (free, no auth) |
| **AI Chat** | Ask Claude or GPT about your taste, get recommendations by description, explore music conversationally |
| **Listening Timeline** | Chronological song view with date labels — critical for Apple Music's limited API |
| **Multi-Service** | Connect Spotify and/or Apple Music — unified profiles with ISRC dedup and genre normalization |
| **Admin Panel** | Live SSE log stream, enrichment progress bars, system status (admin users only) |
| **BYOK** | Users bring their own Claude or OpenAI API keys — no shared billing |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (Vercel)                                          │
│  Next.js 16 · React 19 · Tailwind 4 · shadcn/ui            │
│  TanStack Query · Zustand · Recharts · SSE streaming        │
│  Synesthesia theme (deep purple / electric violet / cream)  │
└────────────────────────┬────────────────────────────────────┘
                         │ REST + SSE (proxied via Next.js)
┌────────────────────────┴────────────────────────────────────┐
│  Backend (Railway)                                          │
│  FastAPI · SQLAlchemy Core · asyncpg · Alembic · Pydantic   │
│                                                             │
│  ┌─── API Layer ──────────────────────────────────────────┐ │
│  │ Auth · Taste · Stats · Recommendations · Playlists     │ │
│  │ Chat · Services · Claude/OpenAI BYOK · Admin · Tracks  │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌─── Engine Layer ──────────────────────────────────────┐  │
│  │ 4-dim Scorer (language/audio/genre/artist)            │  │
│  │ Profile Builder · Featuring Artist Parser             │  │
│  │ Discovery Strategies · Mood Filter · Adaptive Weights │  │
│  │ ISRC Dedup · Genre Normalizer · Similarity            │  │
│  └────────────────────────────────────────────────────────┘ │
│  ┌─── Enrichment Pipeline ───────────────────────────────┐  │
│  │ Deezer (search → preview URL + BPM, free)             │  │
│  │ ReccoBeats (upload preview → 9 audio features, free)  │  │
│  │ SoundStat (Spotify ID → complete features, paid)      │  │
│  │ MusicBrainz (ISRC → Spotify ID resolver)              │  │
│  │ Batch processing (5/batch) · Rate limit backoff       │  │
│  └────────────────────────────────────────────────────────┘ │
│  ┌─── Data Layer ────────────────────────────────────────┐  │
│  │ PostgreSQL 16 · 23 tables · user-scoped · encrypted   │  │
│  │ + Separate logging DB (request/enrichment/error logs) │  │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         ↕                    ↕                    ↕
   Spotify API          Apple Music API      Anthropic/OpenAI
   (OAuth PKCE)         (MusicKit JS)        (BYOK keys)

         ↕                    ↕
   smartaste-logs         NocoDB
   (PostgreSQL)           (Admin UI)
```

## Recommendation Engine

**Context-adaptive weights** — shift based on user profile characteristics:

| Dimension | Default | Adaptive Range | How it works |
|-----------|---------|----------------|-------------|
| Genre match | **35%** | 25-45% | Cosine similarity with regional prioritization |
| Audio similarity | **25%** | 20-40% | Feature similarity from enriched audio; dominant when mood active |
| Artist affinity | **20%** | 15-25% | Calibration-aware; boosted when onboarding complete |
| Language/Region | **20%** | 5-35% | Regional match; high for Italian-only listeners, near-zero for global |

Plus: calibration boost (+0.03 to +0.20 based on artist ranking), diversity penalty (MMR), staleness cooldown, cross-strategy bonus, mood filtering.

**Play count proxy** for Apple Music (no play counts in API): tracks `seen_count` from recently-played polling, songs played recently get up to 10x weight in profile building.

Per-playlist recommendations build a mini-profile from the playlist's songs only.

## Audio Enrichment Pipeline

Runs automatically when a user connects a service or visits the app:

| Stage | API | What it does | Cost |
|-------|-----|-------------|------|
| 1 | **Deezer** | Search by title+artist → 30s preview MP3 + BPM | Free |
| 2 | **ReccoBeats** | Upload preview → acousticness, danceability, energy, instrumentalness, liveness, loudness, speechiness, tempo, valence | Free |
| 3 | **SoundStat** | Spotify ID → complete features + key/scale (optional) | 0.01 EUR/track |

Batch processing: 5 tracks/batch, 1s delay between tracks, `gc.collect()` between batches. Per-field provenance tracking (feature_source JSON).

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
| Migrations | Auto | Dockerfile runs `alembic upgrade head` on every deploy |
| Secrets | Auto | Fernet + JWT keys auto-generated on first deploy if not set |

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
| `MUSICMIND_APPLE_PRIVATE_KEY_PATH` | — | Path to `.p8` key file |
| `MUSICMIND_LOGS_DATABASE_URL` | — | Separate PostgreSQL for request/enrichment/error logs |
| `MUSICMIND_SOUNDSTAT_API_KEY` | — | SoundStat API key for premium enrichment |

*Auto-generated on first Docker deploy if not set.

### NocoDB (admin data browser)
| Variable | Description |
|----------|-------------|
| `NC_DB` | Internal PostgreSQL URL for NocoDB metadata storage |
| `NC_AUTH_JWT_SECRET` | JWT secret for NocoDB admin auth |
| `NC_PUBLIC_URL` | Public URL (set after generating Railway domain) |

<details>
<summary><b>Full API endpoint list</b></summary>

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
| GET | `/api/taste/profile` | Full taste profile |
| GET | `/api/taste/genres` | Top genres |
| GET | `/api/taste/artists` | Top artists (with featuring detection) |
| GET | `/api/taste/audio-traits` | Audio preferences |

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
| GET | `/api/recommendations/{id}/breakdown` | 4-dim score breakdown |

### Playlists
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/playlists` | User's service playlists |
| GET | `/api/playlists/{id}/tracks?service=` | Playlist tracks |
| GET | `/api/playlists/{id}/recommendations?service=` | Per-playlist suggestions |

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

### Admin (requires is_admin)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin/logs` | Recent log entries |
| GET | `/api/admin/logs/stream` | Live SSE log stream |
| GET | `/api/admin/progress` | Per-user enrichment progress |
| GET | `/api/admin/status` | System health summary |

</details>

## Project Structure

```
smartaste/
├── backend/
│   ├── src/musicmind/
│   │   ├── api/                  # REST endpoints (10 domains)
│   │   │   ├── admin/            # Live logs, progress, status
│   │   │   ├── playlists/        # Service playlist fetching
│   │   │   └── ...               # Auth, taste, stats, recs, chat, etc.
│   │   ├── auth/                 # JWT + bcrypt auth + admin roles
│   │   ├── engine/
│   │   │   ├── scorer.py         # 4-dimension scoring (language-first)
│   │   │   ├── profile.py        # Taste profile + featuring artist parser
│   │   │   ├── enrichment/       # Deezer → ReccoBeats → SoundStat pipeline
│   │   │   ├── audio/            # On-demand Essentia + cache layer
│   │   │   ├── mood.py           # Mood filtering (8 profiles)
│   │   │   └── ...               # Weights, genres, dedup, similarity
│   │   ├── db/                   # 23-table SQLAlchemy Core schema + logs DB
│   │   └── security/             # Fernet encryption
│   ├── alembic/                  # 11 database migrations
│   ├── tests/                    # 370+ tests
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/                  # Pages: dashboard, recommendations, playlists, chat, settings
│   │   ├── components/           # shadcn/ui + admin panel + charts
│   │   ├── hooks/                # TanStack Query hooks
│   │   ├── stores/               # Zustand auth store (with is_admin)
│   │   └── lib/                  # API client, SSE, utils
│   └── package.json
├── VERSION                       # Single source of truth
├── CHANGELOG.md
├── what.md                       # Full technical documentation
├── docker-compose.yml
└── .env.example
```

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Frontend** | Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 · shadcn/ui · TanStack Query · Zustand · Recharts · Sora + DM Sans |
| **Backend** | Python 3.11+ · FastAPI · SQLAlchemy Core · asyncpg · Alembic · Pydantic · bcrypt · PyJWT · Fernet |
| **AI** | Anthropic SDK · OpenAI SDK (BYOK — users bring their own keys) |
| **Music APIs** | Spotify Web API (OAuth PKCE) · Apple Music API (MusicKit JS + ES256 JWT) |
| **Audio Enrichment** | Deezer API (free) · ReccoBeats API (free) · SoundStat API (paid) · MusicBrainz (ISRC resolver) |
| **Infrastructure** | PostgreSQL 16 · Docker Compose · Vercel (frontend) · Railway (backend) · uv + npm |

## Tests

```bash
cd backend && uv run python -m pytest tests/ -v
```

370+ tests covering: auth, service connections, BYOK keys, taste profiles, stats, recommendations, multi-service unification, Claude/OpenAI chat, genre normalization, track deduplication, audio pipeline, scoring dimensions.

## License

MIT — Copyright 2026 [Filippo Mattia Menghi](https://github.com/Cyberfilo)
