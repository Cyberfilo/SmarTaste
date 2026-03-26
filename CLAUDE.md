# MusicMind MCP

## Quick Commands
- `uv run python -m musicmind.server` — Start MCP server (stdio)
- `uv run python -m musicmind.setup` — Run one-time Apple Music OAuth setup
- `uv run pytest` — Run tests
- `uv run ruff check src/` — Lint

## Architecture
Python FastMCP server connecting Claude to Apple Music API. Three layers:
1. **API Client** (client.py) — async httpx client for api.music.apple.com, 25+ endpoints
2. **Persistence** (db/) — SQLite cache for listening history, song metadata, taste profiles
3. **Taste Engine** (engine/) — algorithmic profile building + candidate scoring from metadata
4. **Audio Analysis** (engine/audio.py, engine/classifier.py) — optional librosa-based feature extraction + macOS SoundAnalysis

Tools are organized by domain: library, catalog, playback, manage, taste, recommend.

## Adaptive Recommendation Engine
The scorer uses 7 weighted dimensions with adaptive weights learned from user feedback. Additional bonuses: cross-strategy convergence, mood filtering, optional SoundAnalysis labels.

### Default Weight Distribution (genre-first)
- **genre: 0.35** — most important signal; uses regional genre prioritization
- **audio: 0.20** — beat/style similarity from audio features
- **novelty: 0.12** — rewards new artists in familiar genres (Gaussian bell curve)
- **freshness: 0.10** — matches user's release year preferences
- **diversity: 0.08** — MMR penalty to avoid echo chambers
- **artist: 0.08** — deliberately low; style matters more than specific artist
- **staleness: 0.07** — cooldown on recently recommended songs

### Regional Genre Prioritization
When building genre vectors and computing cosine similarity:
- Original genre names (e.g., "Italian Hip-Hop/Rap") get **full weight (1.0)**
- Expanded parent genres (e.g., "Hip-Hop/Rap") get **reduced weight (0.3)**
- This ensures a user who listens to 90% Italian music gets Italian recommendations, not generic American equivalents that happen to share the parent genre

### Artist-in-Wrong-Genre Penalty
If a known artist appears in a genre with cosine score < 0.2, their artist_match is penalized to 30%. This prevents "you listen to Artist X" from recommending their one country song to a drill listener.

### Discovery Strategy Noise Reduction
- **similar_artist_crawl**: default depth=1 (was 2) — two hops drifts too far
- **chart_filter**: passes user's #1 genre to API + pre-filters by top-5 genre overlap
- **genre_adjacent_explore**: uses full regional genre names for search, filters out zero-overlap results

### Audio Analysis Tiers
- **Tier 1** (always): Metadata-only scoring (genres, artists, editorial notes)
- **Tier 2** (requires `ffmpeg` + `librosa`): 7-dimension audio feature extraction from 30s previews
- **Tier 3** (macOS only): SoundAnalysis classification labels via Swift CLI helper

### New DB Tables (adaptive engine)
- `recommendation_feedback` — user feedback on recommendations (thumbs_up/down, skipped, added_to_library)
- `audio_features_cache` — extracted audio features (tempo, energy, brightness, danceability, acousticness, valence_proxy, beat_strength)
- `sound_classification_cache` — optional SoundAnalysis labels
- `play_count_proxy` — approximate play counts from recently-played observations

## Code Style
- All tool inputs: Pydantic BaseModel with Field() descriptions
- All tool names: musicmind_{action}_{resource} in snake_case
- Async everywhere for network calls
- Type hints on all functions
- No stdout logging (stderr only — MCP requirement)
- Genre handling: always split hierarchical genres (e.g., "Italian Hip-Hop/Rap" → ["Italian Hip-Hop/Rap", "Hip-Hop/Rap"])

## API Auth
- Developer Token: ES256 JWT signed with .p8 private key, 6-month expiry
- Music User Token: obtained via MusicKit JS OAuth browser flow (setup.py)
- Config stored at ~/.config/musicmind/config.json (permissions 600)
- Both tokens sent as headers: Authorization: Bearer {dev_token}, Music-User-Token: {user_token}

## Key Apple Music API Notes
- Library songs lack play count — only available via native MusicKit on iOS/macOS
- Use ?include=catalog on library requests to get full catalog metadata (genres, ISRC, editorial notes)
- Recently played tracks: max 50, paginated with offset in batches of 10, no timestamps
- Heavy rotation: often returns empty for light listeners — don't rely on it exclusively
- Ratings: value 1 = love, -1 = dislike. PUT to set, DELETE to remove.
- Rate limits: ~20 req/sec (undocumented), handle 429 with exponential backoff
- Artist views parameter: top-songs, similar-artists, featured-playlists, latest-release, full-albums
- Storefront: default "it" (Italy), auto-detect via /v1/me/storefront

## Known Issues
[Empty — add issues here as discovered]

## Dependencies
- mcp — FastMCP server framework
- httpx — async HTTP client
- aiosqlite — async SQLite driver
- sqlalchemy — query building (Core only, no ORM)
- pyjwt[crypto] + cryptography — ES256 JWT for Apple developer tokens
- numpy — vector operations for taste profile scoring
- scikit-learn — TF-IDF for editorial note extraction
- pydantic — input validation for all MCP tools
- ruff — linting
- pytest + pytest-asyncio — testing

### Optional (audio analysis)
- librosa + soundfile — audio feature extraction from preview URLs (Tier 2)
- ffmpeg (system) — required for M4A/AAC decoding
- Swift 5.9+ — for building SoundAnalysis CLI tool (Tier 3, macOS only)

<!-- GSD:project-start source:PROJECT.md -->
## Project

**MusicMind Web**

A hybrid dashboard + AI chat webapp for music discovery, built on top of an existing MCP-based recommendation engine. Users connect their Spotify and/or Apple Music accounts, bring their own Claude API key, and get a unified taste profile with personalized recommendations — plus a Claude chat interface for deeper musical exploration. Designed for a small group of friends, not a public product.

**Core Value:** Users get genuinely good music recommendations powered by real audio analysis and their actual listening data across services — not just "people who liked X also liked Y."

### Constraints

- **Auth complexity**: Three OAuth flows needed (Spotify, Apple Music, user accounts) — each with different requirements
- **Apple Music**: Requires Apple Developer account with MusicKit key (.p8 file) — already have this
- **Spotify**: Requires Spotify Developer app registration for OAuth client credentials
- **Claude BYOK**: Users need their own Anthropic API keys — no free tier for AI features
- **Data normalization**: Apple Music and Spotify use completely different data models, IDs, and genre taxonomies
- **Rate limits**: Apple Music ~20 req/sec (undocumented), Spotify 100+ req/sec but with monthly quotas
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python >= 3.11 - All application code, configured in `pyproject.toml` with `requires-python = ">=3.11"`
- Swift 5.9+ - Optional SoundAnalysis CLI helper for Tier 3 audio classification (macOS only), referenced in `src/musicmind/engine/classifier.py`
- JavaScript - MusicKit JS OAuth flow served from `src/musicmind/setup.py` (inline HTML template)
- Bash - Setup/connection script at `scripts/connect-claude.sh`
## Runtime
- Python 3.11+ (target version configured as `py311` in ruff)
- macOS primary development/deployment target (SoundAnalysis framework, Apple ecosystem)
- uv (Astral) - Primary package manager and runner
- Lockfile: `uv.lock` present (version 1, revision 3)
## Frameworks
- FastMCP (via `mcp>=1.0`) - MCP server framework, entry point at `src/musicmind/server.py`
- Pydantic >= 2.0 - Input validation for all MCP tools and API response models (`src/musicmind/models.py`)
- SQLAlchemy >= 2.0 (Core only, no ORM) - Query building and schema definition (`src/musicmind/db/schema.py`)
- pytest >= 8.0 - Test runner, config in `pyproject.toml` `[tool.pytest.ini_options]`
- pytest-asyncio >= 0.23 - Async test support, `asyncio_mode = "auto"`
- Hatchling - Build backend (`pyproject.toml` `[build-system]`)
- Ruff >= 0.3 - Linting and formatting, version 0.15.7 detected in `.ruff_cache/`
## Key Dependencies
- `mcp>=1.0` - FastMCP server framework; the entire application is an MCP server
- `httpx>=0.27` - Async HTTP client for Apple Music API (`src/musicmind/client.py`)
- `pyjwt[crypto]>=2.8` + `cryptography>=42.0` - ES256 JWT generation for Apple Developer tokens (`src/musicmind/auth.py`)
- `pydantic>=2.0` - All tool input models and API response parsing (`src/musicmind/models.py`)
- `aiosqlite>=0.20` - Async SQLite driver for persistence (`src/musicmind/db/manager.py`)
- `sqlalchemy>=2.0` - Schema definition and query building (Core mode, `src/musicmind/db/schema.py`, `src/musicmind/db/queries.py`)
- `greenlet>=3.0` - Required by SQLAlchemy async engine
- `numpy>=1.26` - Vector operations for taste profile scoring, cosine similarity (`src/musicmind/engine/scorer.py`, `src/musicmind/engine/weights.py`)
- `scikit-learn>=1.3` - TF-IDF for editorial note extraction in taste profiling
- `librosa>=0.10` - Audio feature extraction from 30s preview clips (`src/musicmind/engine/audio.py`), gracefully degrades if absent
- `soundfile>=0.12` - Audio file I/O companion to librosa
## Configuration
- No `.env` files used
- Config stored at `~/.config/musicmind/config.json` (permissions 600)
- Config model defined in `src/musicmind/config.py` as `MusicMindConfig` (Pydantic BaseModel)
- Required fields: `team_id`, `key_id`, `private_key_path` (Apple Developer credentials)
- Optional fields: `music_user_token` (obtained via OAuth), `storefront` (default: "it" for Italy)
- `pyproject.toml` - Single source of truth for project metadata, dependencies, tool config
- `[tool.hatch.build.targets.wheel]` packages from `src/musicmind`
- `[tool.ruff]` - Linting config: line-length 100, select rules E/F/I/N/W/UP
- `[tool.pytest.ini_options]` - Test paths: `tests/`, async mode: auto
- SQLite database at `~/.config/musicmind/musicmind.db`
- Schema auto-created on startup via SQLAlchemy `metadata.create_all`
- 9 tables defined in `src/musicmind/db/schema.py`
## Platform Requirements
- Python >= 3.11
- uv package manager
- macOS recommended (for Tier 3 SoundAnalysis, optional)
- ffmpeg (system) required for librosa M4A/AAC decoding (optional, Tier 2)
- Runs as stdio MCP server (no network port)
- Designed to run locally via `uv run python -m musicmind`
- Connects to Claude Desktop or Claude Code via MCP protocol
- Requires Apple Developer account with MusicKit key (.p8 file)
## Version Info
- Project version: 2.20 (defined in `src/musicmind/__init__.py`)
- Resolved dependency versions (from `uv.lock`): aiosqlite 0.22.1, anyio 4.13.0
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Use `snake_case.py` for all Python modules: `scorer.py`, `audio.py`, `helpers.py`
- Test files use `test_` prefix: `test_engine.py`, `test_db.py`, `test_auth.py`
- `__init__.py` files are empty or minimal (only `__version__` in package root)
- Use `snake_case` for all functions and methods: `build_genre_vector()`, `score_candidate()`, `get_library_songs()`
- Private/internal functions prefixed with underscore: `_genre_cosine()`, `_compute_staleness()`, `_request()`, `_ctx()`
- MCP tool names follow `musicmind_{action}_{resource}` pattern: `musicmind_search`, `musicmind_lookup_song`, `musicmind_taste_profile`
- `snake_case` throughout: `genre_vector`, `catalog_id`, `release_date`
- Constants use `UPPER_SNAKE_CASE`: `BASE_URL`, `MAX_RETRIES`, `BACKOFF_BASE`, `TOKEN_EXPIRY_SECONDS`, `DEFAULT_WEIGHTS`
- Private instance variables prefixed with underscore: `self._http`, `self._auth`, `self._developer_token`
- `PascalCase` for all classes: `AppleMusicClient`, `DatabaseManager`, `QueryExecutor`, `AudioFeatures`
- Pydantic models use `PascalCase` with descriptive suffixes: `SongAttributes`, `PaginatedResponse`, `MusicMindConfig`
- SQLAlchemy table variables use `snake_case`: `listening_history`, `song_metadata_cache`
## Code Style
- Ruff (v0.15.7) for both linting and formatting
- Line length: 100 characters (configured in `pyproject.toml`)
- Target Python version: 3.11
- Ruff with select rules: `["E", "F", "I", "N", "W", "UP"]`
- Run with: `uv run ruff check src/`
- Config in `pyproject.toml` under `[tool.ruff]`
- Use `from __future__ import annotations` at the top of every module (PEP 604 union types)
- Use `X | None` instead of `Optional[X]` (enabled by future annotations)
- Use `dict[str, Any]` instead of `Dict[str, Any]` (modern generics)
- Use `list[str]` instead of `List[str]`
## Import Organization
- Ruff `I` rule enforces import sorting automatically
- `noqa` comments used for intentional violations: `import musicmind.tools.catalog  # noqa: F401, E402` (side-effect imports in `server.py`)
- Lazy imports used in hot paths to avoid circular deps: `from musicmind.engine.similarity import audio_feature_similarity` inside function body (see `src/musicmind/engine/scorer.py` lines 132, 161)
- `from musicmind.models import Resource as Res` used locally to re-parse raw dicts (see `src/musicmind/tools/catalog.py` lines 148, 208, 251)
- None. All imports use full dotted paths from package root: `musicmind.engine.scorer`, `musicmind.db.queries`
## Type Annotations
- Return type annotations on all functions including `-> None`
- Use `dict[str, Any]` for loosely typed dictionaries (common for API response data)
- Use `list[dict[str, Any]]` for collections of records
- Use `str | None` for optional string fields
- Pydantic `Field()` with `alias` for camelCase API fields: `artist_name: str = Field(default="", alias="artistName")`
- Pydantic `Field()` with `description` for MCP tool inputs and config fields
## Error Handling
- Raise `RuntimeError` for initialization errors: `raise RuntimeError("Client not initialized.")` in `src/musicmind/client.py`
- Raise `ValueError` for missing config: `raise ValueError("Music User Token not configured.")` in `src/musicmind/auth.py`
- Raise `FileNotFoundError` for missing config files: `raise FileNotFoundError(f"Config file not found: {CONFIG_FILE}")` in `src/musicmind/config.py`
- Catch `httpx.HTTPStatusError` and handle by status code: 404 returns `None`, others re-raised (see `src/musicmind/client.py` line 390)
- HTTP 429 rate limiting: exponential backoff with `MAX_RETRIES = 3` and `BACKOFF_BASE = 1.0` (see `src/musicmind/client.py` lines 70-89)
- Bare `except Exception: pass` used only in non-critical display paths (health check in `src/musicmind/server.py` lines 103, 135)
- Server lifespan catches startup errors and yields limited mode context rather than crashing (`src/musicmind/server.py` lines 43-53)
- Optional features (audio analysis, SoundAnalysis) return `None` when unavailable
- Server starts in "limited mode" when config is missing
## Logging
- Never use `print()` or write to stdout (MCP protocol uses stdio)
- Use `logger.info()` for normal operations, `logger.warning()` for recoverable issues, `logger.error()` for failures
- Use %-formatting in log calls (not f-strings): `logger.info("MusicMind MCP v%s starting up", __version__)`
## Comments
- Every `.py` file has a top-level docstring: `"""Async Apple Music API client."""`
- Docstrings use triple-quoted strings, single line for brief or multi-line for detailed
- Use `# ── Section Name ──────────────` comment separators to organize code within files (see `src/musicmind/client.py`, `tests/test_engine.py`)
- MCP tools have detailed docstrings with `Args:` blocks (these are exposed to the LLM user)
- Internal functions have brief docstrings or none
- No TSDoc/JSDoc equivalent enforced; plain docstrings only
- Used sparingly for non-obvious logic: `# Apple returns 201 with data in some cases, empty in others`
- `# noqa:` comments for intentional linter suppressions
## Function Design
- Keyword-only arguments after `*` for optional config: `def score_candidate(..., *, weights=None, audio_features=None)`
- Default values for pagination: `limit: int = 25, offset: int = 0`
- MCP tool functions use positional parameters with defaults (exposed to LLM)
- Return typed Pydantic models from client methods: `PaginatedResponse`, `Resource`, `SearchResults`
- Return `dict[str, Any]` from engine functions (profile, scores)
- Return formatted markdown strings from MCP tool functions
- Return `None` for missing/not-found cases (never raise for "not found")
## Module Design
- No `__all__` declarations; rely on import discipline
- `__init__.py` files are empty (except package root with `__version__`)
- Not used. Each module imports directly from the file it needs.
- All network-facing code is `async`: client methods, DB operations, MCP tool handlers
- Use `async with` context managers for resource lifecycle: `AppleMusicClient`, `DatabaseManager`
- `pytest-asyncio` with `asyncio_mode = "auto"` means test functions are auto-detected as async
## Pydantic Model Conventions
- Use `alias` for camelCase JSON fields from Apple Music API
- Use `model_config = {"populate_by_name": True}` on `Resource` for flexible construction
- Default to empty values, never required fields (API responses are unpredictable)
- Use `Field(default_factory=list)` for mutable defaults
- Use `Field(description=...)` for documentation
- Properties for derived values: `has_user_token`, `private_key`
- Document in `CLAUDE.md`: "All tool inputs: Pydantic BaseModel with Field() descriptions"
- In practice, current tools use plain function parameters (not input models), but the convention is documented for future tools
## SQLAlchemy Conventions
- Table definitions in `src/musicmind/db/schema.py` using `sa.Table()`
- All queries use SQLAlchemy Core expressions in `src/musicmind/db/queries.py`
- JSON columns for arrays/dicts (stored as TEXT in SQLite)
- `sa.func.now()` as `server_default` for timestamp columns
- Apple Music IDs stored as `sa.Text` (they are string identifiers)
## Summary
- Python 3.11+, `from __future__ import annotations` in every file
- Ruff for linting/formatting, line length 100, rules: E, F, I, N, W, UP
- All functions have type annotations, use modern union syntax (`X | None`)
- Logging to stderr only, never stdout (MCP protocol requirement)
- MCP tools named `musicmind_{action}_{resource}`, return markdown strings
- Pydantic for API models (with aliases) and config validation
- SQLAlchemy Core (no ORM) for persistence
- Async everywhere for I/O: httpx client, aiosqlite DB, MCP tool handlers
- Keyword-only arguments for optional parameters in engine functions
- Graceful degradation: optional features return `None`, server starts in limited mode
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- FastMCP server exposing 30+ tools to Claude via stdio transport
- Four distinct layers: Tools (MCP interface) -> Engine (algorithmic logic) -> Client (API) -> DB (persistence)
- Async-first design throughout (httpx, aiosqlite, SQLAlchemy async)
- Lifespan-managed shared state (config, auth, client, DB) passed via MCP context
- All tool outputs are markdown strings consumed by Claude, not structured data
## Layers
- Purpose: Bootstrap the FastMCP server, manage lifespan (startup/shutdown), register tools
- Location: `src/musicmind/server.py`, `src/musicmind/__main__.py`
- Contains: `mcp` FastMCP instance, `lifespan()` async context manager, `musicmind_health` and `musicmind_help` tools
- Depends on: All other layers (initializes them in lifespan)
- Used by: Claude Desktop / MCP client via stdio transport
- Purpose: Define MCP-callable tools that Claude invokes; format results as markdown
- Location: `src/musicmind/tools/`
- Contains: 6 tool modules organized by domain, plus shared helpers
- Depends on: Server (`mcp` instance for `@mcp.tool()` decorator), Client, DB (QueryExecutor), Engine
- Used by: MCP server (tools auto-registered via side-effect imports in `server.py`)
- Key pattern: Each tool module defines a `_ctx()` helper that extracts `client` and `queries` from `mcp.get_context().request_context.lifespan_context`
- Purpose: Algorithmic taste profiling, candidate scoring, discovery strategies, audio analysis
- Location: `src/musicmind/engine/`
- Contains: 8 modules covering profile building, scoring, similarity, discovery, mood filtering, audio extraction, classification, and adaptive weight optimization
- Depends on: numpy, scikit-learn, librosa (optional); DB layer (QueryExecutor for discovery strategies)
- Used by: Tools layer (recommend, taste)
- Key constraint: Profile and scoring modules are local-only (no API calls). Discovery strategies DO call the API.
- Purpose: Async HTTP client wrapping the Apple Music API (25+ endpoints)
- Location: `src/musicmind/client.py`
- Contains: `AppleMusicClient` class with library, catalog, history, and write endpoints
- Depends on: Auth (`AuthManager`), httpx, Pydantic models
- Used by: Tools layer, Engine discovery strategies
- Purpose: SQLite cache for songs, artists, listening history, taste profiles, feedback, audio features
- Location: `src/musicmind/db/`
- Contains: Schema definitions (SQLAlchemy Core), DatabaseManager (lifecycle), QueryExecutor (all queries)
- Depends on: SQLAlchemy, aiosqlite
- Used by: Tools layer, Engine discovery strategies
- Storage: `~/.config/musicmind/musicmind.db`
- Purpose: Apple Music API authentication (ES256 JWT developer tokens, Music User Token management)
- Location: `src/musicmind/auth.py`
- Contains: `AuthManager` class with token generation and header building
- Depends on: Config, PyJWT, cryptography
- Used by: Client layer
- Purpose: Load/save configuration from `~/.config/musicmind/config.json`
- Location: `src/musicmind/config.py`
- Contains: `MusicMindConfig` Pydantic model, `load_config()`, `save_config()`
- Depends on: Pydantic
- Used by: Auth, Server lifespan
- Purpose: Pydantic models for Apple Music API responses
- Location: `src/musicmind/models.py`
- Contains: `Resource`, `PaginatedResponse`, `SearchResults`, `ChartResponse`, attribute models for songs/albums/artists/playlists
- Used by: Client (parsing), Tools (helpers for extraction)
## Data Flow
- Shared state lives in the lifespan context dict: `{"config", "auth", "client", "db", "queries"}`
- All tools access state via `mcp.get_context().request_context.lifespan_context`
- No global mutable state beyond the lifespan context
- Server supports "limited mode" when config/auth unavailable (DB still works)
## Key Abstractions
- Purpose: Generic Apple Music API resource wrapper (song, album, artist, playlist)
- Examples: `src/musicmind/models.py` - `Resource` class
- Pattern: Flat dict-like `attributes` field; type discriminated by `resource.type` string
- Purpose: Single class encapsulating all database operations
- Examples: `src/musicmind/db/queries.py`
- Pattern: Each method is a standalone async operation using SQLAlchemy Core; no ORM
- Purpose: Computed representation of user's musical preferences
- Examples: Built by `src/musicmind/engine/profile.py` - `build_taste_profile()`
- Pattern: Plain dict with keys: `genre_vector`, `top_artists`, `audio_trait_preferences`, `release_year_distribution`, `familiarity_score`, `total_songs_analyzed`, `listening_hours_estimated`
- Purpose: A song dict augmented with scoring metadata
- Examples: Produced by `src/musicmind/engine/scorer.py` - `score_candidate()`
- Pattern: Original song dict + `_score`, `_breakdown`, `_explanation` keys
- Purpose: Find candidate songs from the Apple Music catalog using different approaches
- Examples: `src/musicmind/engine/discovery.py` - `similar_artist_crawl()`, `genre_adjacent_explore()`, `editorial_mining()`, `chart_filter()`
- Pattern: Each accepts `(client, queries, profile_or_seeds)`, returns `list[dict]` of cache-format song dicts
## Entry Points
- Location: `src/musicmind/__main__.py` -> `src/musicmind/server.py`
- Triggers: `uv run python -m musicmind` or `uv run python -m musicmind.server`
- Responsibilities: Start FastMCP stdio server, initialize all subsystems via lifespan
- Location: `src/musicmind/setup.py`
- Triggers: `uv run python -m musicmind.setup`
- Responsibilities: Interactive CLI + local HTTP server for Apple Music OAuth flow via MusicKit JS
## Error Handling
- Server starts in "limited mode" if config/auth fails (DB-only tools still work)
- Discovery strategies individually wrapped in try/except; one failing doesn't abort the flow
- API client retries 429 (rate limit) with exponential backoff (3 retries, 1s base)
- Audio analysis gracefully returns None when librosa/ffmpeg unavailable (LIBROSA_AVAILABLE flag)
- SoundAnalysis classifier returns None when Swift binary missing
- All logging goes to stderr (MCP stdio transport requirement — stdout is the protocol channel)
## Cross-Cutting Concerns
- Framework: Python `logging` module, all handlers directed to `sys.stderr`
- Pattern: Each module creates its own logger via `logging.getLogger(__name__)` or `logging.getLogger("musicmind")`
- Level: INFO by default, set in `server.py`
- Tool inputs validated implicitly by Python type hints (FastMCP handles this)
- Config validated via Pydantic `MusicMindConfig` model
- API responses parsed via Pydantic `Resource` / `PaginatedResponse` models
- Two-token system: Developer Token (ES256 JWT, auto-generated, 6-month expiry) + Music User Token (obtained via OAuth setup wizard)
- Tokens injected as HTTP headers by `AuthManager.auth_headers()`
- Config stored at `~/.config/musicmind/config.json` with 0o600 permissions
- Transparent: every API-fetching tool also caches results to SQLite
- No explicit TTL/eviction; data accumulates over time
- `extract_song_cache_data()` in `src/musicmind/tools/helpers.py` is the universal API-to-cache transformer
## Scoring Architecture (7 Weighted Dimensions)
| Dimension | Default Weight | Source |
|-----------|---------------|--------|
| Genre match (cosine) | 0.35 | `_genre_cosine()` with regional prioritization |
| Audio similarity | 0.20 | `audio_feature_similarity()` from Tier 2 features |
| Novelty (Gaussian) | 0.12 | New artist in familiar genre, bell curve at distance 0.3-0.5 |
| Freshness | 0.10 | Release year match to user's distribution |
| Diversity (MMR) | 0.08 | Penalty for similarity to already-selected songs |
| Artist affinity | 0.08 | Deliberately low; style > specific artist |
| Anti-staleness | 0.07 | Cooldown on recently recommended songs |
## Audio Analysis Tiers
| Tier | Requirements | What It Does | Location |
|------|-------------|-------------|----------|
| 1 (always) | None | Metadata-only scoring (genres, artists, editorial) | `src/musicmind/engine/scorer.py` |
| 2 (optional) | librosa + ffmpeg | 7-dimension audio features from 30s previews | `src/musicmind/engine/audio.py` |
| 3 (optional) | macOS + Swift binary | SoundAnalysis classification labels | `src/musicmind/engine/classifier.py` |
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
