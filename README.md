<p align="center">
  <img src="https://img.shields.io/badge/version-0.1.0-emerald?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Next.js-16-000000?style=for-the-badge&logo=nextdotjs&logoColor=white" alt="Next.js">
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
</p>

# SmarTaste <sup><sub>formerly MusicMind</sub></sup>

> Your music, understood. A music discovery platform that connects to Spotify and Apple Music, analyzes your actual listening data, and delivers genuinely personalized recommendations — powered by a 7-dimension adaptive scoring engine and conversational AI.

**Live:** [music.menghi.dev](https://music.menghi.dev) · **Docs:** [`what.md`](what.md) · *Previously known as MusicMind*

---

## Features

| Feature | Description |
|---------|-------------|
| **Taste Profile** | Visualize your musical DNA — top genres (with regional specificity), artists, audio traits, familiarity score |
| **Smart Recommendations** | 4 discovery strategies scored across 7 adaptive dimensions that learn from your feedback |
| **AI Chat** | Ask Claude or GPT about your taste, get recommendations by description, explore music conversationally |
| **Multi-Service** | Connect Spotify and/or Apple Music — unified profiles with cross-service dedup and genre normalization |
| **Listening Stats** | Top tracks, artists, and genres by period (month, 6 months, all time) |
| **BYOK** | Users bring their own Claude or OpenAI API keys — no shared billing |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (Vercel)                                          │
│  Next.js 16 · React 19 · Tailwind 4 · shadcn/ui            │
│  TanStack Query · Zustand · Recharts · SSE streaming        │
└────────────────────────┬────────────────────────────────────┘
                         │ REST + SSE (proxied via Next.js)
┌────────────────────────┴────────────────────────────────────┐
│  Backend (Railway)                                          │
│  FastAPI · SQLAlchemy Core · asyncpg · Alembic · Pydantic   │
│                                                             │
│  ┌─── API Layer ──────────────────────────────────────────┐ │
│  │ Auth · Taste · Stats · Recommendations · Chat          │ │
│  │ Services (Spotify/Apple) · Claude BYOK · OpenAI BYOK   │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌─── Engine Layer ──────────────────────────────────────┐  │
│  │ 7-dim Scorer · Profile Builder · Genre Normalizer     │  │
│  │ Discovery Strategies · Mood Filter · Adaptive Weights │  │
│  │ ISRC Dedup · Similarity · Session Model               │  │
│  └────────────────────────────────────────────────────────┘ │
│  ┌─── Data Layer ────────────────────────────────────────┐  │
│  │ PostgreSQL 16 · 15+ tables · user-scoped · encrypted  │  │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         ↕                    ↕                    ↕
   Spotify API          Apple Music API      Anthropic/OpenAI
   (OAuth PKCE)         (MusicKit JS)        (BYOK keys)
```

## Recommendation Engine

The scorer evaluates candidates across **7 weighted dimensions** that adapt to your feedback:

| Dimension | Weight | How it works |
|-----------|--------|-------------|
| Genre match | **0.35** | Cosine similarity with regional prioritization (Italian Hip-Hop ≠ generic Hip-Hop) |
| Audio similarity | **0.20** | Energy, tempo, danceability, valence proximity |
| Novelty | **0.12** | Gaussian bell curve — rewards new artists in familiar genres |
| Freshness | **0.10** | Release year match to your listening distribution |
| Diversity (MMR) | **0.08** | Maximal Marginal Relevance penalty to avoid echo chambers |
| Artist affinity | **0.08** | Deliberately low — style matters more than specific artists |
| Anti-staleness | **0.07** | Exponential cooldown on recently recommended songs |

After 10+ feedback records, weights optimize via coordinate descent. Cross-strategy convergence bonuses and mood filtering applied on top.

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

### Production

```bash
cp .env.example .env   # Fill in secrets (see .env.example for generators)
docker compose up db -d
cd backend && uv sync --dev && uv run alembic upgrade head
uv run uvicorn musicmind.app:app --reload --port 8000
# Frontend: cd frontend && npm install && npm run dev
```

### Cloud Deploy (Vercel + Railway)

| Component | Platform | Setup |
|-----------|----------|-------|
| Frontend | **Vercel** | Import repo → auto-detects `rootDirectory: frontend` → set `NEXT_PUBLIC_API_URL` |
| Backend | **Railway** | Add PostgreSQL service → add GitHub service (backend/) → set env vars |
| Migrations | Auto | Dockerfile runs `alembic upgrade head` on every deploy |
| Secrets | Auto | Fernet + JWT keys auto-generated on first deploy if not set |

## Connecting Services

<details>
<summary><b>Spotify</b> — OAuth PKCE flow</summary>

1. Create app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Add redirect URI: `http://localhost:3000/api/services/spotify/callback`
3. Set `MUSICMIND_SPOTIFY_CLIENT_ID` and `MUSICMIND_SPOTIFY_CLIENT_SECRET`
4. In-app: Settings → Connect Spotify

> Dev mode limits to 5 users. Apply for extended quota if needed.
</details>

<details>
<summary><b>Apple Music</b> — MusicKit JS flow</summary>

1. Create MusicKit key at [developer.apple.com](https://developer.apple.com/account/resources/authkeys)
2. Download `.p8` private key
3. Set `MUSICMIND_APPLE_TEAM_ID`, `MUSICMIND_APPLE_KEY_ID`, `MUSICMIND_APPLE_PRIVATE_KEY_PATH`
4. In-app: Settings → Connect Apple Music
</details>

<details>
<summary><b>Claude / OpenAI</b> — BYOK (Bring Your Own Key)</summary>

Users store their own API keys in Settings. Keys are encrypted at rest with Fernet. No shared billing.
</details>

## Environment Variables

All backend variables use the `MUSICMIND_` prefix. See [`.env.example`](.env.example) for the full list with generators.

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

*Auto-generated on first Docker deploy if not set.

<details>
<summary><b>Full API endpoint list</b></summary>

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/signup` | Create account |
| POST | `/api/auth/login` | Log in (sets JWT cookies) |
| POST | `/api/auth/logout` | Clear cookies |
| POST | `/api/auth/refresh` | Refresh access token |
| GET | `/api/auth/me` | Current user info |

### Services
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/services` | List connected services |
| POST | `/api/services/spotify/connect` | Initiate Spotify OAuth |
| GET | `/api/services/spotify/callback` | OAuth callback |
| POST | `/api/services/apple-music/connect` | Store Apple Music token |
| DELETE | `/api/services/{service}` | Disconnect |

### Taste Profile
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/taste/profile` | Full taste profile |
| GET | `/api/taste/genres` | Top genres |
| GET | `/api/taste/artists` | Top artists |
| GET | `/api/taste/audio-traits` | Audio preferences |

### Listening Stats
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/stats/tracks?period=month` | Top tracks by period |
| GET | `/api/stats/artists?period=6months` | Top artists by period |
| GET | `/api/stats/genres?period=alltime` | Top genres by period |

### Recommendations
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/recommendations?strategy=all` | Get scored recommendations |
| POST | `/api/recommendations/{id}/feedback` | Thumbs up/down |
| GET | `/api/recommendations/{id}/breakdown` | 7-dim score breakdown |

### Chat
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/message` | Send message (SSE stream) |
| GET | `/api/chat/conversations` | List conversations |
| GET | `/api/chat/conversations/{id}` | Load conversation |
| DELETE | `/api/chat/conversations/{id}` | Delete conversation |

### BYOK Keys
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/claude/key` | Store Claude key (encrypted) |
| GET | `/api/claude/key/status` | Check key status |
| POST | `/api/claude/key/validate` | Test key works |
| DELETE | `/api/claude/key` | Remove key |

</details>

## Project Structure

```
smartaste/
├── backend/
│   ├── src/musicmind/
│   │   ├── api/                  # REST endpoints (8 domains)
│   │   ├── auth/                 # JWT + bcrypt auth
│   │   ├── engine/               # Recommendation algorithms
│   │   │   ├── scorer.py         # 7-dimension scoring + MMR
│   │   │   ├── profile.py        # Taste profile builder
│   │   │   ├── mood.py           # Mood filtering (8 profiles)
│   │   │   ├── weights.py        # Adaptive weight optimizer
│   │   │   ├── genres.py         # 150+ genre mappings
│   │   │   ├── dedup.py          # ISRC + fuzzy dedup
│   │   │   └── similarity.py     # Cosine/Jaccard/L2
│   │   ├── db/                   # SQLAlchemy Core schema
│   │   └── security/             # Fernet encryption
│   ├── alembic/                  # 8 database migrations
│   ├── tests/                    # 370+ tests
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/                  # Next.js 16 App Router pages
│   │   ├── components/           # shadcn/ui + custom components
│   │   ├── hooks/                # TanStack Query hooks
│   │   ├── stores/               # Zustand auth store
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
| **Frontend** | Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 · shadcn/ui · TanStack Query · Zustand · Recharts |
| **Backend** | Python 3.11+ · FastAPI · SQLAlchemy Core · asyncpg · Alembic · Pydantic · bcrypt · PyJWT · Fernet |
| **AI** | Anthropic SDK · OpenAI SDK (BYOK — users bring their own keys) |
| **Music APIs** | Spotify Web API (OAuth PKCE) · Apple Music API (MusicKit JS + ES256 JWT) |
| **Infrastructure** | PostgreSQL 16 · Docker Compose · Vercel (frontend) · Railway (backend) · uv + npm |

## Tests

```bash
cd backend && uv run python -m pytest tests/ -v
```

370+ tests covering: auth, service connections, BYOK keys, taste profiles, stats, recommendations, multi-service unification, Claude/OpenAI chat, genre normalization, track deduplication.

## License

MIT — Copyright 2026 [Filippo Mattia Menghi](https://github.com/Cyberfilo)
