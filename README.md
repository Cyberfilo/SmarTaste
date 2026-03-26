# MusicMind MCP

**V2.10** | **30 tools** | **150 tests** | **Adaptive scoring** | **Audio analysis** | **Mood filtering**

> An MCP server that gives Claude deep, intelligent access to your Apple Music account — not just CRUD operations, but a full taste-understanding engine that analyzes listening patterns, builds genre/artist/mood profiles, and generates genuinely personalized recommendations and playlists.

---

## How It Works

```
You → Claude → MusicMind MCP → Apple Music API
                    ↓
              SQLite Cache ← Taste Engine → Recommendations
                    ↓
           Audio Analysis (optional)
```

MusicMind sits between Claude and Apple Music as an MCP server. It caches your library and listening history locally, builds a multi-dimensional taste profile (genre vectors, artist affinity, audio preferences, temporal patterns), and uses that profile to score and rank new music — producing recommendations that actually reflect what you listen to, not just what's popular.

The recommendation engine is **adaptive**: give feedback on songs and it learns your preferences over time, adjusting scoring weights automatically.

## Features

### Library & Catalog Access
Browse your full Apple Music library, search the catalog, look up songs/artists/albums with rich metadata, view charts and editorial categories.

### Taste Profiling
Algorithmic profile built from your cached data:
- **Genre affinity vector** — weighted by recency and frequency, with hierarchical genre expansion
- **Artist affinity scores** — library presence + listening history + ratings
- **Audio trait preferences** — lossless, Atmos, spatial audio
- **Release year distribution** — new releases vs. catalog preference
- **Familiarity score** — Shannon entropy measure of how adventurous your taste is

### Smart Recommendations
Four discovery strategies combined with MMR-diversity selection, all using full regional genre names and pre-filtering by genre overlap:
- **Similar artist crawl** — 1-hop traversal of Apple Music's artist graph (tight, no drift)
- **Genre-adjacent exploration** — search your top regional genres, filter out zero-overlap results
- **Editorial mining** — extract songs from editorial playlists using full genre names
- **Chart filtering** — genre-filtered charts with mandatory genre overlap check

### Adaptive Scoring Engine
10 scoring dimensions with weights that learn from your feedback:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Genre Match | **35%** | Cosine similarity with regional genre prioritization (full genre 1.0, parent 0.3) |
| Audio Similarity | **20%** | Cosine on audio feature vectors — beat/style matters (when available) |
| Novelty | **12%** | Gaussian bell curve — rewards familiar-genre, new-artist combos |
| Freshness | **10%** | Matches your release year preferences |
| Diversity | **8%** | MMR penalty to avoid echo chambers |
| Artist Affinity | **8%** | How much you listen to this artist (penalized if wrong genre) |
| Staleness | **7%** | Cooldown on recently recommended songs |
| Cross-Strategy | bonus | +5% per additional strategy that found the same song |
| Mood Boost | bonus | Contextual filtering (workout, chill, focus, party, sad, driving) |
| Classification | bonus | From SoundAnalysis labels (macOS, optional) |

### Audio Analysis (3-tier, graceful degradation)
- **Tier 1** (always available): Metadata-only scoring
- **Tier 2** (requires ffmpeg + librosa): 7-dimension audio feature extraction from 30s previews — tempo, energy, brightness, danceability, acousticness, valence proxy, beat strength
- **Tier 3** (macOS only): Apple SoundAnalysis classification labels via Swift CLI

### Playlist Generation
Describe a vibe in natural language and MusicMind creates a real Apple Music playlist:
```
"underground drill tracks for a late night drive"
"chill atmospheric stuff for studying"
"high energy workout bangers"
```

---

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — Python package manager
- **Apple Developer account** with a MusicKit key (`.p8` file)
  - Create one at [developer.apple.com/account/resources/authkeys](https://developer.apple.com/account/resources/authkeys/list)
  - Enable "Media Services (MusicKit)" capability

### Optional (for audio analysis)
- `ffmpeg` — for M4A/AAC decoding (`brew install ffmpeg`)
- `librosa` — installed via `uv sync --extra audio`
- Swift 5.9+ — for SoundAnalysis CLI (macOS only)

---

## Quick Start

```bash
# Clone
git clone https://github.com/Cyberfilo/musicmind-mcp.git
cd musicmind-mcp

# Install
uv sync --all-extras

# Setup (one-time: enters your Apple credentials + OAuth)
uv run python -m musicmind.setup

# Connect to Claude (automatic)
./scripts/connect-claude.sh
```

Or connect manually — see [Manual Integration](#manual-integration) below.

---

## Setup Details

### 1. Install Dependencies

```bash
uv sync --all-extras
```

For audio analysis (Tier 2), also install ffmpeg:
```bash
brew install ffmpeg          # macOS
sudo apt-get install ffmpeg  # Linux
uv sync --extra audio        # Install librosa + soundfile
```

### 2. Apple Music Authorization

```bash
uv run python -m musicmind.setup
```

The wizard will:
1. Ask for your **Apple Developer Team ID** (10 characters, from developer.apple.com)
2. Ask for your **MusicKit Key ID** (10 characters, from the key you created)
3. Ask for the **path to your `.p8` private key file**
4. Open a browser window for **Apple Music OAuth** — click "Authorize" to grant access
5. Save everything to `~/.config/musicmind/config.json` (permissions `600`)

### 3. Connect to Claude

Run the automated setup script:

```bash
./scripts/connect-claude.sh
```

This detects whether you have Claude Desktop, Claude Code, or both — and configures the MCP server automatically.

---

## Manual Integration

### Claude Desktop

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "musicmind": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/musicmind-mcp", "python", "-m", "musicmind"]
    }
  }
}
```

Then restart Claude Desktop.

### Claude Code (CLI)

From any project directory:

```bash
claude mcp add musicmind -- uv run --directory /absolute/path/to/musicmind-mcp python -m musicmind
```

Or add to `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "musicmind": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/musicmind-mcp", "python", "-m", "musicmind"]
    }
  }
}
```

### Verify

In Claude, ask: **"Check MusicMind health"** — it should show server version, auth status, and cache stats.

---

## Tools (30)

### Library Browsing
| Tool | Description |
|------|-------------|
| `musicmind_library_songs` | Browse your library songs (paginated, with genre metadata) |
| `musicmind_library_albums` | Browse your library albums |
| `musicmind_library_artists` | Browse your library artists |
| `musicmind_library_playlists` | List all your playlists |
| `musicmind_playlist_tracks` | Get tracks in a specific playlist |
| `musicmind_search_library` | Search within your library |

### Catalog Search & Lookup
| Tool | Description |
|------|-------------|
| `musicmind_search` | Search the Apple Music catalog |
| `musicmind_lookup_song` | Full song details with metadata and relationships |
| `musicmind_lookup_artist` | Artist details + top songs + similar artists |
| `musicmind_lookup_album` | Album details + track listing |
| `musicmind_charts` | Top charts (songs, albums, playlists), optionally by genre |
| `musicmind_activities` | Mood/activity categories (Chill, Workout, Focus, etc.) |
| `musicmind_genres` | Full genre listing for your storefront |

### Listening History
| Tool | Description |
|------|-------------|
| `musicmind_recently_played` | Recent tracks (auto-cached for taste analysis) |
| `musicmind_heavy_rotation` | Content you've been listening to heavily |
| `musicmind_apple_recommendations` | Apple's own personalized picks |

### Library Management
| Tool | Description |
|------|-------------|
| `musicmind_create_playlist` | Create a new playlist with optional initial tracks |
| `musicmind_add_to_playlist` | Add tracks to an existing playlist |
| `musicmind_add_to_library` | Add catalog songs to your library |
| `musicmind_rate_song` | Love, dislike, or remove rating |

### Taste Analysis
| Tool | Description |
|------|-------------|
| `musicmind_taste_profile` | Build and display your full taste profile |
| `musicmind_taste_compare` | Score how well a song matches your taste |
| `musicmind_listening_stats` | Aggregate stats from your cached data |
| `musicmind_why_this_song` | Explain per-dimension taste match for a song |

### Smart Recommendations
| Tool | Description |
|------|-------------|
| `musicmind_discover` | Find new songs with optional mood and strategy selection |
| `musicmind_smart_playlist` | Create a vibe-based playlist from natural language |
| `musicmind_feedback` | Train the engine with thumbs up/down feedback |
| `musicmind_refresh_cache` | Re-fetch data and rebuild taste profile |

### System
| Tool | Description |
|------|-------------|
| `musicmind_health` | Server status, auth check, cache stats |
| `musicmind_help` | Full tool guide with example prompts |

---

## Example Prompts

Once connected, try these in Claude:

```
"Show me my music taste profile"
"What have I been listening to lately?"
"Search for 'Kendrick Lamar' in my library"
"Find me 20 new songs — use the similar artists strategy"
"Discover chill music for studying"
"Create a playlist called 'Night Drive' with underground drill vibes"
"Why would I like this song?" (after looking up a song ID)
"Give thumbs up to song 1234567890" (after a recommendation)
"What are the top charts right now?"
"Compare my taste to this album's tracks"
```

---

## Architecture

```
src/musicmind/
├── server.py          # FastMCP server + lifespan (DB, auth, client init)
├── config.py          # Config from ~/.config/musicmind/config.json
├── auth.py            # ES256 JWT developer token + Music User Token
├── client.py          # Async Apple Music API client (25+ endpoints)
├── models.py          # Pydantic response models
├── tools/
│   ├── library.py     # 6 library browsing tools
│   ├── catalog.py     # 7 catalog search/lookup tools
│   ├── playback.py    # 3 listening history tools
│   ├── manage.py      # 4 library management tools
│   ├── taste.py       # 4 taste analysis tools
│   └── recommend.py   # 4 recommendation tools (discover, playlist, feedback, refresh)
├── engine/
│   ├── profile.py     # Taste profile builder (genre vectors, artist affinity, temporal decay)
│   ├── similarity.py  # Song/artist/audio similarity scoring
│   ├── scorer.py      # 10-dimension candidate scoring with adaptive weights
│   ├── discovery.py   # 4 discovery strategies
│   ├── weights.py     # Adaptive weight optimizer (coordinate descent from feedback)
│   ├── mood.py        # 6 mood profiles with genre + audio feature ranges
│   ├── audio.py       # Audio feature extraction via librosa (Tier 2)
│   └── classifier.py  # SoundAnalysis integration via Swift CLI (Tier 3)
└── db/
    ├── schema.py      # 9 SQLAlchemy Core tables
    ├── manager.py     # Async DB lifecycle
    └── queries.py     # All query methods
```

---

## Development

```bash
uv run pytest              # Run all 150 tests
uv run pytest -v           # Verbose output
uv run ruff check src/     # Lint
uv run ruff check src/ --fix  # Auto-fix lint issues
```

---

## License

MIT
