# MusicMind

A music discovery webapp that connects to your Spotify and Apple Music accounts, analyzes your listening habits, and delivers genuinely personalized recommendations — powered by a 7-dimension adaptive scoring engine and conversational AI via Claude.

## What It Does

- **Taste Profile** — Visualize your musical DNA: top genres (with regional specificity), favorite artists, audio trait preferences
- **Smart Recommendations** — 4 discovery strategies (similar artists, genre exploration, editorial mining, chart filtering) scored across 7 weighted dimensions that learn from your feedback
- **Claude Chat** — Ask Claude about your music taste, get recommendations by description ("something like early Radiohead but more electronic"), adjust preferences via natural language
- **Multi-Service** — Connect Spotify and/or Apple Music. Unified taste profiles with cross-service genre normalization and ISRC-based deduplication
- **Listening Stats** — Top tracks, artists, and genres by time period (month, 6 months, all time)

## Architecture

```
frontend/          Next.js 16 · React 19 · Tailwind 4 · shadcn/ui
    |
    | REST + SSE
    |
backend/           FastAPI · SQLAlchemy Core · asyncpg · Alembic
    |
    |── engine/    Taste profiling · 7-dim scorer · Discovery · Mood filter · Adaptive weights
    |── auth/      JWT (httpOnly cookies) · bcrypt · CSRF · Refresh tokens
    |── api/       Taste · Stats · Recommendations · Chat · Services · Claude BYOK · Tracks
    |
    |── PostgreSQL 16 (user-scoped, multi-service)
    |── Anthropic API (BYOK — users bring their own Claude key)
    |── Spotify Web API (OAuth PKCE)
    |── Apple Music API (MusicKit JS + ES256 developer tokens)
```

## Quick Start

### Sandbox Mode (fastest — no keys needed)

```bash
# Clone
git clone https://github.com/Cyberfilo/MusicMind.git
cd MusicMind

# Start database
docker compose up db -d

# Backend (sandbox auto-fills secrets with dev defaults)
cd backend
uv sync --dev
MUSICMIND_SANDBOX=true uv run alembic upgrade head
MUSICMIND_SANDBOX=true uv run uvicorn musicmind.app:app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:3000 — sign up, explore the dashboard. Spotify/Apple Music features require API keys (see below).

### Production Setup

```bash
# 1. Copy and fill environment variables
cp .env.example .env
# Edit .env — generate the required secrets:
#   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 2. Start everything
docker compose up db -d
cd backend && uv sync --dev
uv run alembic upgrade head
uv run uvicorn musicmind.app:app --reload --port 8000

# 3. Frontend (new terminal)
cd frontend && npm install && npm run dev
```

### Docker Compose (full stack)

```bash
cp .env.example .env
# Fill in .env
docker compose up -d
# Backend at :8000, PostgreSQL at :5432
# Frontend: cd frontend && npm install && npm run dev
```

### Deploy to Vercel + Railway (cloud)

**Frontend → Vercel:**

1. Import the GitHub repo at [vercel.com/new](https://vercel.com/new)
2. Vercel auto-detects `rootDirectory: frontend` from `vercel.json`
3. Set environment variable: `NEXT_PUBLIC_API_URL` = your Railway backend URL (e.g., `https://musicmind-backend.up.railway.app`)
4. Deploy

**Backend → Railway:**

1. Create a new project at [railway.app](https://railway.app)
2. Add a **PostgreSQL** service (Railway provisions it automatically)
3. Add a **service from GitHub** → point to `backend/` directory
4. Set environment variables in Railway:
   - `MUSICMIND_DATABASE_URL` — Railway auto-fills this from the PostgreSQL service (use the `DATABASE_URL` variable with `postgresql+asyncpg://` prefix)
   - `MUSICMIND_FERNET_KEY` — generate with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `MUSICMIND_JWT_SECRET_KEY` — generate with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
   - `MUSICMIND_FRONTEND_URL` — your Vercel URL (e.g., `https://musicmind.vercel.app`)
   - `MUSICMIND_SPOTIFY_REDIRECT_URI` — `https://your-vercel-url.vercel.app/api/services/spotify/callback`
   - `MUSICMIND_DEBUG` = `false`
   - Plus Spotify/Apple Music keys if needed
5. Railway builds from the Dockerfile and runs migrations automatically on deploy

**Important:** Update your Spotify Developer Dashboard redirect URI to match the Vercel URL.

## Commands Reference

| Command | What it does |
|---------|-------------|
| `docker compose up db -d` | Start PostgreSQL |
| `docker compose up -d` | Start PostgreSQL + backend |
| `cd backend && uv sync --dev` | Install backend dependencies |
| `cd backend && uv run alembic upgrade head` | Run database migrations |
| `cd backend && uv run uvicorn musicmind.app:app --reload --port 8000` | Start backend (dev) |
| `cd backend && uv run python -m pytest tests/ -v` | Run backend tests (294 tests) |
| `cd backend && uv run ruff check src/` | Lint backend |
| `cd frontend && npm install` | Install frontend dependencies |
| `cd frontend && npm run dev` | Start frontend (dev, port 3000) |
| `cd frontend && npm run build` | Build frontend for production |
| `MUSICMIND_SANDBOX=true uv run uvicorn ...` | Start in sandbox mode (no keys needed) |

## Environment Variables

All variables use the `MUSICMIND_` prefix. See [`.env.example`](.env.example) for the full list.

| Variable | Required | Description |
|----------|----------|-------------|
| `MUSICMIND_DATABASE_URL` | Yes | PostgreSQL connection string |
| `MUSICMIND_FERNET_KEY` | Yes* | Encryption key for secrets at rest |
| `MUSICMIND_JWT_SECRET_KEY` | Yes* | JWT signing secret |
| `MUSICMIND_SANDBOX` | No | Set `true` for dev defaults (skips key requirements) |
| `MUSICMIND_SPOTIFY_CLIENT_ID` | No | Spotify app client ID |
| `MUSICMIND_SPOTIFY_CLIENT_SECRET` | No | Spotify app client secret |
| `MUSICMIND_SPOTIFY_REDIRECT_URI` | No | OAuth callback URL (default: `http://127.0.0.1:8000/api/services/spotify/callback`) |
| `MUSICMIND_APPLE_TEAM_ID` | No | Apple Developer Team ID |
| `MUSICMIND_APPLE_KEY_ID` | No | MusicKit key ID |
| `MUSICMIND_APPLE_PRIVATE_KEY_PATH` | No | Path to `.p8` private key file |
| `MUSICMIND_DEBUG` | No | Enable debug mode |
| `MUSICMIND_LOG_LEVEL` | No | Logging level (default: `INFO`) |

*Not required in sandbox mode.

## Connecting Music Services

### Spotify

1. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Add redirect URI: `http://127.0.0.1:8000/api/services/spotify/callback`
3. Set `MUSICMIND_SPOTIFY_CLIENT_ID` and `MUSICMIND_SPOTIFY_CLIENT_SECRET` in `.env`
4. In the app, go to Settings → Connect Spotify

> Spotify dev mode limits to 5 authorized users. Apply for extended quota if needed.

### Apple Music

1. Create a MusicKit key at [developer.apple.com](https://developer.apple.com/account/resources/authkeys)
2. Download the `.p8` private key file
3. Set `MUSICMIND_APPLE_TEAM_ID`, `MUSICMIND_APPLE_KEY_ID`, and `MUSICMIND_APPLE_PRIVATE_KEY_PATH` in `.env`
4. In the app, go to Settings → Connect Apple Music

### Claude (BYOK)

Users provide their own Anthropic API key in Settings → Claude API Key. The key is encrypted at rest with Fernet. No shared API key — each user pays for their own usage.

## API Endpoints

<details>
<summary>Full endpoint list (click to expand)</summary>

### Auth
- `POST /api/auth/signup` — Create account
- `POST /api/auth/login` — Log in (sets JWT cookies)
- `POST /api/auth/logout` — Log out (clears cookies)
- `POST /api/auth/refresh` — Refresh access token
- `GET /api/auth/me` — Current user info

### Services
- `GET /api/services` — List connected services
- `POST /api/services/spotify/connect` — Initiate Spotify OAuth
- `GET /api/services/spotify/callback` — OAuth callback
- `GET /api/services/apple-music/developer-token` — Get Apple developer token
- `POST /api/services/apple-music/connect` — Store Apple Music token
- `DELETE /api/services/{service}` — Disconnect service

### Claude BYOK
- `POST /api/claude/key` — Store API key (encrypted)
- `GET /api/claude/key/status` — Check if key configured
- `POST /api/claude/key/validate` — Test key works
- `DELETE /api/claude/key` — Remove key
- `GET /api/claude/key/cost` — Estimated cost per message

### Taste Profile
- `GET /api/taste/profile` — Full taste profile
- `GET /api/taste/genres` — Top genres
- `GET /api/taste/artists` — Top artists
- `GET /api/taste/audio-traits` — Audio preferences

### Listening Stats
- `GET /api/stats/tracks?period=month` — Top tracks
- `GET /api/stats/artists?period=6months` — Top artists
- `GET /api/stats/genres?period=alltime` — Top genres

### Recommendations
- `GET /api/recommendations?strategy=all&mood=chill&limit=10` — Get recommendations
- `POST /api/recommendations/{id}/feedback` — Thumbs up/down
- `GET /api/recommendations/{id}/breakdown` — 7-dimension scoring

### Tracks
- `GET /api/tracks/{id}/audio-features` — Audio feature radar data

### Chat
- `POST /api/chat/message` — Send message (SSE stream response)
- `GET /api/chat/conversations` — List conversations
- `GET /api/chat/conversations/{id}` — Load conversation
- `DELETE /api/chat/conversations/{id}` — Delete conversation

### Health
- `GET /health` — Backend health check

</details>

## Recommendation Engine

The scorer evaluates candidates across 7 weighted dimensions:

| Dimension | Default Weight | What it measures |
|-----------|---------------|-----------------|
| Genre match | 0.35 | Cosine similarity of genre vectors (with regional prioritization) |
| Audio similarity | 0.20 | Energy, tempo, danceability, valence proximity |
| Novelty | 0.12 | New artists in familiar genres (Gaussian bell curve) |
| Freshness | 0.10 | Release year match to listening distribution |
| Diversity (MMR) | 0.08 | Penalty for similarity to already-selected songs |
| Artist affinity | 0.08 | Deliberately low — style matters more than specific artist |
| Anti-staleness | 0.07 | Cooldown on recently recommended songs |

Weights adapt via coordinate descent optimization after 10+ feedback records. Cross-strategy convergence bonuses and mood filtering applied on top.

## Tech Stack

**Backend:** Python 3.11+ · FastAPI · SQLAlchemy Core · asyncpg · Alembic · Pydantic · bcrypt · PyJWT · Fernet · Anthropic SDK · httpx · numpy

**Frontend:** Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 · shadcn/ui · TanStack Query · Zustand · Recharts · Sonner

**Infrastructure:** PostgreSQL 16 · Docker Compose · uv (Python) · npm (Node)

## Project Structure

```
musicmind/
├── backend/
│   ├── src/musicmind/
│   │   ├── api/              # REST endpoints
│   │   │   ├── chat/         # Claude chat (SSE streaming)
│   │   │   ├── claude/       # BYOK key management
│   │   │   ├── recommendations/  # Discovery + scoring
│   │   │   ├── services/     # Spotify/Apple Music OAuth
│   │   │   ├── stats/        # Listening statistics
│   │   │   ├── taste/        # Taste profile
│   │   │   └── tracks/       # Audio features
│   │   ├── auth/             # JWT authentication
│   │   ├── db/               # Schema + engine
│   │   ├── engine/           # Recommendation algorithms
│   │   │   ├── scorer.py     # 7-dimension scoring
│   │   │   ├── profile.py    # Taste profile builder
│   │   │   ├── mood.py       # Mood filtering
│   │   │   ├── weights.py    # Adaptive weight optimizer
│   │   │   ├── genres.py     # Cross-service genre normalization
│   │   │   ├── dedup.py      # ISRC + fuzzy deduplication
│   │   │   └── similarity.py # Vector similarity
│   │   └── security/         # Fernet encryption
│   ├── alembic/              # Database migrations
│   ├── tests/                # 294 tests
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/              # Next.js pages (login, dashboard, chat, settings)
│   │   ├── components/       # React components (ui, chat, dashboard, recommendations, settings)
│   │   ├── hooks/            # TanStack Query hooks
│   │   ├── stores/           # Zustand stores
│   │   ├── lib/              # API client, SSE, utils
│   │   └── types/            # TypeScript types
│   └── package.json
├── docker-compose.yml
├── .env.example
└── LICENSE
```

## Tests

```bash
cd backend
uv run python -m pytest tests/ -v
```

294 tests covering: authentication, service connections, BYOK key management, taste profiles, listening stats, recommendations, multi-service unification, Claude chat, detail views, genre normalization, track deduplication.

## License

MIT
