# Phase 5: Taste Profile Dashboard - Research

**Researched:** 2026-03-26
**Domain:** FastAPI taste profile API, Spotify Web API data fetching, Apple Music API data fetching, engine port
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Port the existing `build_taste_profile()` from `src/musicmind/engine/profile.py` into the webapp backend. Adapt for multi-user (user_id scoping) and multi-service (service_source filtering).
- **D-02:** The engine modules (profile.py, similarity.py) are the source of truth for taste computation. Copy and adapt, don't rewrite from scratch.
- **D-03:** Profile computation fetches user's library data via the appropriate music service API (Apple Music or Spotify), caches it in the database, then runs the taste profile algorithm on the cached data.
- **D-04:** When a user requests their taste profile, the backend: (1) checks if cached profile is fresh enough (< 24h), (2) if stale, fetches library/recently-played from the connected service API, (3) caches the raw data, (4) runs build_taste_profile on cached data, (5) returns structured JSON.
- **D-05:** Apple Music data fetching uses the existing client patterns from `src/musicmind/client.py`. Spotify data fetching uses httpx with the user's stored (decrypted) access token.
- **D-06:** Profile staleness threshold: 24 hours. User can force refresh via API parameter.
- **D-07:** Endpoints: GET /api/taste/profile (full taste profile), GET /api/taste/genres (top genres), GET /api/taste/artists (top artists), GET /api/taste/audio-traits (audio preferences).
- **D-08:** All endpoints accept optional `?service=spotify|apple_music` query param. If omitted, uses the first connected service.
- **D-09:** Response format: structured JSON with typed fields. Frontend (Phase 11) will render visualizations from this data.
- **D-10:** API-only in this phase. No frontend rendering — that's Phase 11's job.
- **D-11:** Preserve regional genre specificity from the existing engine. "Italian Hip-Hop/Rap" stays as-is, not collapsed to "Hip-Hop/Rap".
- **D-12:** Genre vector from the engine uses the existing weighted, temporally-decayed computation.

### Claude's Discretion

- Exact Spotify library/recently-played API endpoints and pagination strategy
- Whether to create a unified MusicServiceClient interface or keep separate Apple Music / Spotify fetch functions
- Cache table usage (existing song_metadata_cache vs new taste-specific cache)
- How to handle users with no listening data yet (empty profile response)
- Test strategy (mock API responses, test profile computation with fixture data)

### Deferred Ideas (OUT OF SCOPE)

- TAST-05 (unified multi-service profile) → Phase 8
- Taste evolution timeline → Phase 10 / v2
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TAST-01 | User can view their taste profile showing top genres with regional specificity | Engine port: `build_genre_vector()` preserves regional names; endpoint GET /api/taste/genres returns sorted genre_vector |
| TAST-02 | User can view their top artists ranked by affinity | Engine port: `build_artist_affinity()` returns sorted artist list with score + song_count; endpoint GET /api/taste/artists |
| TAST-03 | User can view their audio trait preferences (energy, danceability, valence, acousticness) | Engine port: `build_audio_trait_preferences()` from audio_traits field; endpoint GET /api/taste/audio-traits |
| TAST-04 | User can view their taste profile built from a single connected service | Data pipeline: fetch from service, cache with service_source, filter by service_source at query time; endpoint GET /api/taste/profile with ?service param |
</phase_requirements>

---

## Summary

Phase 5 is primarily a **data pipeline + API layer** phase. The taste computation algorithm already exists and is fully tested in `src/musicmind/engine/profile.py`. The work is: (1) build Spotify data fetching functions (parallel to the existing Apple Music client pattern), (2) write DB queries to fetch and cache songs/history per user per service, (3) implement a staleness-aware pipeline that refreshes data when needed, (4) register four FastAPI endpoints on a new `/api/taste` router.

The existing `taste_profile_snapshots` table is already in the schema and matches the `build_taste_profile()` output dict exactly — no migration needed. The `song_metadata_cache` table has `service_source` column, so Spotify songs can coexist with Apple Music songs per user. Filtering by `service_source` at query time gives per-service profiles.

The Spotify API exposes exactly the data the engine needs: `GET /me/top/tracks` (top 50 songs, provides popularity/affinity signal as a proxy for library affinity), `GET /me/top/artists` (top 50 artists), `GET /me/player/recently-played` (last 50 tracks). Spotify tracks do not carry `audioTraits` like Apple Music — the `audio_trait_preferences` dimension will be empty for Spotify users unless audio analysis runs separately (Phase 7 concern). This is expected and acceptable for Phase 5.

**Primary recommendation:** Implement a `TasteService` class (analogous to `service.py` in the services module) that contains all business logic — fetching, caching, staleness checks, and profile computation — keeping routers thin. Use separate Apple Music and Spotify fetch functions (not a unified interface) since their data shapes differ fundamentally.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.135.2 (installed) | Router definition, dependency injection | Already used by all existing routes |
| SQLAlchemy (Core) | 2.0.48 (installed) | Async DB queries against PostgreSQL/SQLite | Already used throughout backend |
| httpx | 0.28.1 (installed) | Async HTTP calls to Spotify API | Already used for Spotify token exchange and Apple Music |
| Pydantic v2 | 2.12.5 (installed) | Request/response schema validation | Already used throughout backend |
| aiosqlite | 0.22.1 (dev dep) | In-memory SQLite for tests | Already used by all existing tests |

### No New Dependencies Required

All needed libraries are already in `pyproject.toml`. No new `pip install` or `uv add` needed for this phase.

---

## Architecture Patterns

### Recommended Project Structure (additions only)

```
backend/src/musicmind/
├── api/
│   └── taste/                       # NEW — taste profile endpoints
│       ├── __init__.py
│       ├── router.py                # GET /api/taste/* endpoints
│       ├── schemas.py               # Pydantic response models
│       └── service.py               # TasteService: pipeline + business logic
├── engine/                          # NEW — ported from src/musicmind/engine/
│   ├── __init__.py
│   └── profile.py                   # Copy of engine/profile.py (adapted)
└── db/
    └── queries.py                   # EXTEND — add taste query methods
```

The `api/router.py` gains one line: `api_router.include_router(taste_router)`.

### Pattern 1: TasteService Pipeline (D-04)

**What:** A service class encapsulating the entire taste profile pipeline. Router calls one method; service handles staleness, fetch, cache, compute.

**When to use:** Whenever business logic spans DB + external API calls. Keeps routers to <30 lines.

```python
# Source: pattern extrapolated from existing service.py structure
class TasteService:
    async def get_profile(
        self,
        engine: AsyncEngine,
        encryption: EncryptionService,
        *,
        user_id: str,
        service: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        # 1. Check for fresh snapshot (< 24h)
        snapshot = await self._get_fresh_snapshot(engine, user_id, service)
        if snapshot and not force_refresh:
            return snapshot

        # 2. Fetch & cache raw data from service API
        songs, history = await self._fetch_and_cache(engine, encryption, user_id, service)

        # 3. Run engine
        profile = build_taste_profile(songs, history, use_temporal_decay=True)

        # 4. Persist snapshot
        await self._save_snapshot(engine, user_id, service, profile)
        return profile
```

### Pattern 2: Spotify Data Fetching (D-05)

**What:** Standalone async functions (not a class) that fetch Spotify data using the stored, decrypted access token. Mirrors the approach in `service.py` for Spotify token exchange.

**Spotify endpoints needed:**

| Data | Endpoint | Scope | Max Items | Pagination |
|------|----------|-------|-----------|------------|
| Top tracks (library proxy) | `GET /v1/me/top/tracks?time_range=long_term&limit=50` | `user-top-read` | 50 per request; offset pagination to 200 total | offset+limit |
| Top artists | `GET /v1/me/top/artists?time_range=long_term&limit=50` | `user-top-read` | 50 per request | offset+limit |
| Recently played | `GET /v1/me/player/recently-played?limit=50` | `user-read-recently-played` | 50 total (cursor paged but max is effectively 50) | cursor (after/before) |
| Saved tracks (library) | `GET /v1/me/tracks?limit=50` | `user-library-read` | 50 per page; offset pagination | offset+limit |

**Note on scope alignment:** The existing `SPOTIFY_SCOPES` in `service.py` already includes `user-library-read user-read-recently-played user-top-read` — no re-authorization is needed for users already connected.

**Spotify response → song_metadata_cache field mapping:**

| song_metadata_cache column | Spotify top/tracks source | Spotify saved tracks source |
|---------------------------|--------------------------|----------------------------|
| `catalog_id` | `track.id` | `item.track.id` |
| `name` | `track.name` | `item.track.name` |
| `artist_name` | `track.artists[0].name` | `item.track.artists[0].name` |
| `album_name` | `track.album.name` | `item.track.album.name` |
| `genre_names` | `[]` (not in track object) | `[]` (not in track object) |
| `duration_ms` | `track.duration_ms` | `item.track.duration_ms` |
| `release_date` | `track.album.release_date` | `item.track.album.release_date` |
| `isrc` | `track.external_ids.isrc` | `item.track.external_ids.isrc` |
| `audio_traits` | `[]` (not available) | `[]` (not available) |
| `date_added_to_library` | not available for top tracks | `item.added_at` |
| `service_source` | `"spotify"` | `"spotify"` |

**Critical limitation:** Spotify track objects from `/me/top/tracks` and `/me/tracks` do NOT include genres. Genres are on the Artist object. For the genre vector to work for Spotify users, the fetcher must either: (a) fetch artist genres in a follow-up call, or (b) accept that Spotify users get empty `genre_names` and thus an empty genre vector. Option (a) is preferred but complex — requires additional `GET /v1/artists?ids=...` calls. Option (b) yields a usable profile (artist affinity still works). **Recommendation:** Fetch artist genres in a batch call during the pipeline. Spotify's `GET /v1/artists?ids=<comma-list>` (up to 50 IDs) returns artist objects including `genres`. This requires extracting unique artist IDs from the fetched tracks and making an additional batch request.

**February 2026 Spotify API impact:** The `GET /v1/artists?ids=...` bulk endpoint was removed in the February 2026 changes. Use `GET /v1/artists/{id}` individually, or use the artist IDs from `GET /me/top/artists` (which already includes genres) as the genre source. The recommended approach: use `GET /me/top/artists` (which returns artist objects with `genres` arrays) to build genre data, rather than doing secondary artist lookups from track data.

### Pattern 3: Apple Music Data Fetching (D-05)

**What:** Use the existing `AppleMusicClient` from `src/musicmind/client.py` — it already has `get_library_songs()` and `get_recently_played_tracks()`. The webapp backend needs its own minimal Apple Music fetch wrapper that uses the stored (decrypted) Music User Token from the DB rather than the MCP config file.

**Key difference from MCP version:** The MCP version uses `AuthManager` initialized from a config file. The webapp version reads the Music User Token from the DB (via `EncryptionService.decrypt`) and passes it directly as a header. No full `AuthManager` needed.

**Apple Music fields that map directly:**
- `genreNames` → `genre_names` (genres on track objects — already present!)
- `audioTraits` → `audio_traits` (lossless, dolby-atmos, hi-res-lossless — present on catalog songs)
- `dateAdded` on library songs → `date_added_to_library`

**Apple Music pagination:** `get_library_songs()` returns up to 100 per page; loop with offset until `next` is None or a practical limit (500 songs).

### Pattern 4: Staleness Check with service_source Filtering

**What:** The `taste_profile_snapshots` table does NOT have a `service_source` column currently. Two options:

1. **Add `service_source` column** to `taste_profile_snapshots` via Alembic migration — clean, required for per-service staleness checks.
2. **Use computed_at** and always recompute from `song_metadata_cache` filtered by `service_source` — avoids migration but means snapshots are per-user not per-service.

**Recommendation:** Add `service_source` to `taste_profile_snapshots`. The table already exists in the schema, so an Alembic migration is needed. Without this column, the staleness check (D-04) cannot distinguish a cached Spotify profile from a cached Apple Music profile.

### Pattern 5: Endpoint Design

```
GET /api/taste/profile?service=spotify&refresh=false
GET /api/taste/genres?service=spotify
GET /api/taste/artists?service=spotify
GET /api/taste/audio-traits?service=spotify
```

All return 200 with structured JSON. If `service` param is omitted, use the first connected service from DB. If user has no connected service, return 400. If user has no data yet (no songs cached), return an empty profile struct (not 404) with `total_songs_analyzed: 0`.

### Anti-Patterns to Avoid

- **Don't call the music API on every request:** The staleness check (D-04) exists for a reason. Unconditional API calls on every GET will hit rate limits and be slow.
- **Don't store profiles without `service_source`:** Future Phase 8 merges profiles — they must be distinguishable from the start.
- **Don't rewrite `build_taste_profile()`:** D-02 is explicit. Copy and adapt the existing function.
- **Don't use `genre_names` from Spotify track objects directly:** They are always empty on track objects. Use artist genres from `GET /me/top/artists` instead.
- **Don't block on full library pagination:** Fetch up to a reasonable limit (e.g., 200 saved tracks, 50 top tracks) on first profile build. The engine works better with data it has than waiting for exhaustive pagination.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Genre vector computation | Custom genre counting | `build_genre_vector()` from `engine/profile.py` | Already handles hierarchical expansion, regional weighting, temporal decay |
| Artist affinity scoring | Custom artist ranker | `build_artist_affinity()` from `engine/profile.py` | Already handles love/dislike ratings, recency weighting |
| Audio trait aggregation | Custom trait counter | `build_audio_trait_preferences()` from `engine/profile.py` | Already normalized correctly |
| Familiarity score | Custom entropy computation | `compute_familiarity_score()` from `engine/profile.py` | Uses normalized Shannon entropy |
| Token decryption | Custom crypto | `EncryptionService.decrypt()` from `security/encryption.py` | Fernet, already tested |
| Auth dependency | Custom JWT extraction | `get_current_user` from `auth/dependencies.py` | Already handles cookie extraction, JWT decode, error responses |
| Spotify token refresh | Custom refresh logic | `refresh_spotify_token()` from `api/services/service.py` | Already implemented in Phase 3 — call before Spotify API requests |

**Key insight:** ~70% of Phase 5's logic already exists. The new code is the glue: Spotify fetch functions, DB query methods, a staleness-check pipeline, router, and schemas.

---

## Common Pitfalls

### Pitfall 1: Spotify Genres Are on Artists, Not Tracks

**What goes wrong:** Engineer queries Spotify tracks and finds `genre_names` is always empty. Genre vector is empty. Profile shows no genres.

**Why it happens:** Spotify's track/album objects don't include genre data. Genres live on Artist objects (`artist.genres`). This is a long-standing Spotify API design decision.

**How to avoid:** Use `GET /me/top/artists` response as the genre source. Each artist object has a `genres: list[str]` field. Map artist genres onto tracks by matching `track.artists[0].name`.

**Warning signs:** Any Spotify fetch function that maps `track.get("genre_names")` — this will always be empty and should use artist genre lookup instead.

### Pitfall 2: Snapshot Table Missing service_source

**What goes wrong:** User has Spotify and Apple Music connected. Profile computed from Spotify gets cached. Next request for Apple Music profile finds the 24h-fresh Spotify snapshot and returns Spotify data as Apple Music data.

**Why it happens:** `taste_profile_snapshots` table has no `service_source` column. Staleness check matches any snapshot for the user regardless of service.

**How to avoid:** Add `service_source TEXT NOT NULL DEFAULT 'apple_music'` to `taste_profile_snapshots` via Alembic migration. Staleness check query must `WHERE user_id = ? AND service_source = ?`.

**Warning signs:** Missing Alembic migration for this column is a red flag. The planner should include a Wave 0 migration task.

### Pitfall 3: Apple Music Developer Token Required for Library Calls

**What goes wrong:** Backend attempts to fetch Apple Music library using only the `Music-User-Token`. Gets 401.

**Why it happens:** Apple Music API requires BOTH the Developer Token (`Authorization: Bearer {dev_token}`) AND the Music User Token (`Music-User-Token: {user_token}`) for library endpoints.

**How to avoid:** The TasteService must read `apple_team_id`, `apple_key_id`, `apple_private_key_path` from `app.state.settings` to generate a fresh Developer Token, then use both headers. The existing `generate_apple_developer_token()` function in `api/services/service.py` is ready to use.

**Warning signs:** Any Apple Music fetch function that only uses the decrypted access_token from DB without also generating a Developer Token.

### Pitfall 4: Expired Spotify Token Not Refreshed Before Fetch

**What goes wrong:** Profile build fails with 401 on Spotify API call. User sees error.

**Why it happens:** Spotify access tokens expire after 1 hour. The service connection may have an expired token.

**How to avoid:** Before making any Spotify API calls in TasteService, check `token_expires_at`. If expired or within 60s of expiry, call `refresh_spotify_token()` and update the DB. The Phase 3 code `refresh_spotify_token()` in `api/services/service.py` already exists.

**Warning signs:** Any Spotify API call that doesn't first check/refresh the token.

### Pitfall 5: Large Library Pagination Can Timeout

**What goes wrong:** User has 5,000 songs in Apple Music library. Fetching all 5,000 before building profile causes request timeout.

**Why it happens:** Apple Music `/me/library/songs` maxes at 100 per page. 5,000 songs = 50 requests. Even at 20 req/sec, that's 2.5 seconds minimum, plus serialization/deserialization.

**How to avoid:** Set a practical pagination cap (e.g., 500 songs for initial profile, or 200 for Apple Music recently played × 5 pages). The engine builds a meaningful profile from 200-500 songs. Document the cap in code with a comment.

**Warning signs:** Unbounded `while response.next:` loops without a page limit.

### Pitfall 6: song_metadata_cache Composite PK Collision

**What goes wrong:** Upsert of Spotify songs fails because `(catalog_id, user_id)` is already occupied by an Apple Music song with the same track ID (extremely rare but possible if ISRC matches).

**Why it happens:** `song_metadata_cache` primary key is `(catalog_id, user_id)`. Spotify IDs and Apple Music IDs are different namespaces, but this is a data-integrity assumption.

**How to avoid:** Prefix Spotify `catalog_id` values as-is (they're Spotify-format IDs like `4iV5W9uYEdYUVa79Axb7Rh`) which never collide with Apple Music IDs (integer or alphanumeric strings). The `service_source` column distinguishes them. The existing composite PK is safe.

---

## Code Examples

### Spotify Fetch Function Pattern

```python
# Source: extrapolated from existing service.py httpx patterns (Phase 3)
SPOTIFY_API_BASE = "https://api.spotify.com/v1"


async def fetch_spotify_top_tracks(access_token: str, limit: int = 50) -> list[dict]:
    """Fetch user's top tracks (long_term) as song cache dicts."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SPOTIFY_API_BASE}/me/top/tracks",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"time_range": "long_term", "limit": limit, "offset": 0},
        )
        resp.raise_for_status()
        data = resp.json()
    return [_spotify_track_to_cache_dict(item) for item in data.get("items", [])]


def _spotify_track_to_cache_dict(track: dict) -> dict:
    """Map Spotify track object to song_metadata_cache-compatible dict."""
    artists = track.get("artists", [])
    artist_name = artists[0].get("name", "") if artists else ""
    album = track.get("album", {})
    ext_ids = track.get("external_ids", {})
    return {
        "catalog_id": track["id"],
        "library_id": None,
        "name": track.get("name", ""),
        "artist_name": artist_name,
        "album_name": album.get("name", ""),
        "genre_names": [],  # populated separately from artist genres
        "duration_ms": track.get("duration_ms"),
        "release_date": album.get("release_date"),
        "isrc": ext_ids.get("isrc"),
        "editorial_notes": "",
        "audio_traits": [],
        "has_lyrics": False,
        "content_rating": None,
        "artwork_bg_color": "",
        "artwork_url_template": "",
        "preview_url": (track.get("preview_url") or ""),
        "user_rating": None,
        "date_added_to_library": None,
        "service_source": "spotify",
    }
```

### Staleness Check Query Pattern

```python
# Source: extrapolated from existing SQLAlchemy Core patterns in backend
from datetime import UTC, datetime, timedelta
import sqlalchemy as sa
from musicmind.db.schema import taste_profile_snapshots

async def get_fresh_snapshot(
    engine, *, user_id: str, service_source: str
) -> dict | None:
    """Return the most recent snapshot if < 24h old, else None."""
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(taste_profile_snapshots)
            .where(
                sa.and_(
                    taste_profile_snapshots.c.user_id == user_id,
                    taste_profile_snapshots.c.service_source == service_source,
                    taste_profile_snapshots.c.computed_at >= cutoff,
                )
            )
            .order_by(taste_profile_snapshots.c.computed_at.desc())
            .limit(1)
        )
        row = result.fetchone()
    if row is None:
        return None
    return dict(row._mapping)
```

### Engine Call (adapted for multi-user)

```python
# Source: src/musicmind/tools/taste.py musicmind_taste_profile() — ported pattern
from musicmind.engine.profile import build_taste_profile

# songs: list[dict] from song_metadata_cache (filtered by user_id + service_source)
# history: list[dict] from listening_history (filtered by user_id + service_source)
profile = build_taste_profile(songs, history, use_temporal_decay=True)
# profile keys: genre_vector, top_artists, audio_trait_preferences,
#               release_year_distribution, familiarity_score,
#               total_songs_analyzed, listening_hours_estimated
```

### Response Schema Pattern

```python
# Source: pattern from existing schemas.py files in backend
from pydantic import BaseModel, Field

class TasteProfileResponse(BaseModel):
    service: str = Field(description="Service this profile was built from")
    computed_at: str = Field(description="ISO timestamp of last computation")
    total_songs_analyzed: int
    listening_hours_estimated: float
    familiarity_score: float = Field(description="0=focused, 1=adventurous (Shannon entropy)")
    genre_vector: dict[str, float] = Field(description="Genre name -> normalized affinity (sums to 1)")
    top_artists: list[dict] = Field(description="[{name, score, song_count}, ...] sorted by score")
    audio_trait_preferences: dict[str, float] = Field(description="Trait -> fraction of library")
    release_year_distribution: dict[str, float] = Field(description="Year -> fraction")

class TopGenresResponse(BaseModel):
    service: str
    genres: list[dict[str, float | str]]  # [{genre, weight}, ...]

class TopArtistsResponse(BaseModel):
    service: str
    artists: list[dict]  # [{name, score, song_count}, ...]

class AudioTraitsResponse(BaseModel):
    service: str
    traits: dict[str, float]  # {trait_name: fraction}
    note: str | None = None  # "Audio traits not available for Spotify" etc.
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Spotify bulk artist lookup `GET /artists?ids=...` | Per-artist or top-artist list only | February 2026 | Must use `GET /me/top/artists` for artist genres |
| Spotify `GET /browse/new-releases` | Removed | February 2026 | Not relevant to this phase |
| `taste_profile_snapshots` without `service_source` | Needs `service_source` column | Phase 5 (new) | Required for per-service caching |

**Deprecated/outdated:**
- Spotify `GET /v1/artists?ids=...` (bulk): Removed February 2026. Use `GET /me/top/artists` for genre data instead.
- Spotify `GET /v1/audio-features/{id}`: Removed. Not relevant — we use Apple Music `audioTraits` and librosa (Phase 7).

---

## Open Questions

1. **Spotify genre coverage for users who have no `top_artists` data yet**
   - What we know: `GET /me/top/artists` requires listening history. New Spotify users return empty.
   - What's unclear: How to handle this gracefully vs returning an empty genre vector.
   - Recommendation: Return empty genre_vector with a `note: "Insufficient Spotify data for genre analysis"` field. Document this in the API schema.

2. **Apple Music library size cap during first profile build**
   - What we know: Pagination can run indefinitely for large libraries.
   - What's unclear: What cap is reasonable (100 songs? 500?).
   - Recommendation: Cap at 500 library songs + 50 recently played for Apple Music. Enough for a meaningful profile, safe from timeout.

3. **`taste_profile_snapshots` migration: Wave 0 or inline**
   - What we know: Table needs `service_source` column added.
   - What's unclear: Whether to add it in Wave 0 (dedicated migration task) or at start of pipeline task.
   - Recommendation: Wave 0 task — add Alembic migration for `service_source TEXT NOT NULL server_default='apple_music'` on `taste_profile_snapshots`.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11+ | Engine port | Yes | 3.14.0 (from venv) | — |
| FastAPI | Router | Yes | 0.135.2 | — |
| httpx | Spotify fetch | Yes | 0.28.1 | — |
| SQLAlchemy async | DB queries | Yes | 2.0.48 | — |
| Pydantic v2 | Schemas | Yes | 2.12.5 | — |
| aiosqlite | Tests | Yes | 0.22.1 | — |
| pytest-asyncio | Tests | Yes | pytest 9.0.2 | — |
| Spotify API (user-top-read) | Spotify profile | LOW confidence (Feb 2026 changes) | — | Use GET /me/tracks as fallback |
| Apple Music API | Apple Music profile | Yes (tokens already stored) | — | — |

**Missing dependencies with no fallback:** None — all libraries are installed.

**Dev mode concern (MEDIUM confidence):** After February 2026, Spotify dev mode apps are limited to 5 users and a "smaller set of endpoints." The migration guide says existing integrations had restrictions postponed, but `GET /me/top` availability for dev mode is not explicitly confirmed. **Recommendation:** Plan uses `GET /me/top` as primary endpoint. Include fallback to `GET /me/tracks` (saved tracks) if `/me/top` returns 403, since `user-library-read` scope (already requested) definitely still works.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 0.23+ |
| Config file | `backend/pyproject.toml` (`asyncio_mode = "auto"`) |
| Quick run command | `cd backend && uv run python -m pytest tests/test_taste.py -x` |
| Full suite command | `cd backend && uv run python -m pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TAST-01 | GET /api/taste/genres returns genre_vector with regional names preserved | integration | `pytest tests/test_taste.py::test_genres_endpoint -x` | Wave 0 |
| TAST-01 | `build_genre_vector()` preserves "Italian Hip-Hop/Rap" without collapsing | unit | `pytest tests/test_taste.py::test_genre_regional_specificity -x` | Wave 0 |
| TAST-02 | GET /api/taste/artists returns artists sorted by affinity score | integration | `pytest tests/test_taste.py::test_artists_endpoint -x` | Wave 0 |
| TAST-03 | GET /api/taste/audio-traits returns trait preferences | integration | `pytest tests/test_taste.py::test_audio_traits_endpoint -x` | Wave 0 |
| TAST-04 | GET /api/taste/profile with ?service=spotify returns Spotify-only data | integration | `pytest tests/test_taste.py::test_profile_service_isolation -x` | Wave 0 |
| TAST-04 | Staleness check: fresh snapshot returned without re-fetching | unit | `pytest tests/test_taste.py::test_snapshot_staleness_check -x` | Wave 0 |
| TAST-04 | force_refresh=true bypasses cache and re-fetches | integration | `pytest tests/test_taste.py::test_force_refresh -x` | Wave 0 |
| TAST-04 | Empty profile (no data) returns 200 with total_songs_analyzed=0 | integration | `pytest tests/test_taste.py::test_empty_profile_response -x` | Wave 0 |
| TAST-04 | No connected service returns 400 | integration | `pytest tests/test_taste.py::test_no_service_returns_400 -x` | Wave 0 |

**Test approach:** Mock external API calls at the router import level (same pattern as `test_services.py` and `test_claude_byok.py`). Use `unittest.mock.AsyncMock` + `patch` for Spotify and Apple Music fetch functions. Use in-memory SQLite with `metadata.create_all` for DB operations.

**Engine unit tests:** The existing MCP project already tests `build_taste_profile()` and `build_genre_vector()` in `tests/test_engine.py`. The ported backend version does not need to re-test the algorithm itself — only test the integration layer (fetch → cache → compute → response).

### Sampling Rate

- **Per task commit:** `cd backend && uv run python -m pytest tests/test_taste.py -x`
- **Per wave merge:** `cd backend && uv run python -m pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- `backend/tests/test_taste.py` — all taste endpoint and service integration tests
- Alembic migration: `service_source` column on `taste_profile_snapshots`
- `backend/src/musicmind/engine/__init__.py` — new engine subdirectory
- `backend/src/musicmind/engine/profile.py` — ported engine (copy + adapt)
- `backend/src/musicmind/api/taste/__init__.py`
- `backend/src/musicmind/api/taste/router.py`
- `backend/src/musicmind/api/taste/schemas.py`
- `backend/src/musicmind/api/taste/service.py`

---

## Project Constraints (from CLAUDE.md)

- `from __future__ import annotations` at top of every module
- Use `X | None` not `Optional[X]`; use `dict[str, Any]` not `Dict[str, Any]`
- Ruff linting: `uv run ruff check src/` — rules E, F, I, N, W, UP; line length 100
- All functions have return type annotations including `-> None`
- Logging to `stderr` only via `logger = logging.getLogger(__name__)` with `%-formatting`
- Never `print()`
- All DB operations: SQLAlchemy Core (no ORM), async engine
- No `server_default` with mutable Python defaults — use `sa.text("false")` or `sa.func.now()`
- Pydantic BaseModel with `Field(description=...)` for all API schemas
- Async everywhere for I/O (httpx, DB operations)
- Test pattern: SQLite in-memory engine, `metadata.create_all`, mock external HTTP at import-level namespace
- Module-level `_settings` pattern for CSRF middleware (already done in `app.py`)
- `snake_case` for all modules, functions, DB table variables
- `PascalCase` for all classes and Pydantic models
- Section separators: `# ── Section Name ──────────────────────`
- Dialect-agnostic SELECT-then-INSERT/UPDATE (no PostgreSQL `ON CONFLICT`)
- `server_default` only (no mutable `default=`) for PostgreSQL column defaults
- UTC normalization in routers for SQLite timezone-naive datetime compat

---

## Sources

### Primary (HIGH confidence)

- `src/musicmind/engine/profile.py` — Full `build_taste_profile()` algorithm, all sub-functions. Direct code read.
- `src/musicmind/client.py` — Apple Music API client patterns, endpoint shapes, pagination. Direct code read.
- `backend/src/musicmind/db/schema.py` — All existing table definitions including `taste_profile_snapshots`, `song_metadata_cache`. Direct code read.
- `backend/src/musicmind/api/services/service.py` — `SPOTIFY_SCOPES`, token refresh, Apple Music developer token generation. Direct code read.
- `backend/src/musicmind/api/services/router.py` — Router pattern, CSRF handling, Depends injection. Direct code read.
- `backend/tests/test_services.py` — Test pattern: mock targets, SQLite fixture, auth cookies, in-memory DB. Direct code read.
- Spotify Web API Reference — `GET /me/top/{type}` [developer.spotify.com](https://developer.spotify.com/documentation/web-api/reference/get-users-top-artists-and-tracks): endpoint, params, scopes confirmed via WebFetch.
- Spotify Web API Reference — `GET /me/player/recently-played` [developer.spotify.com](https://developer.spotify.com/documentation/web-api/reference/get-recently-played): cursor pagination, `user-read-recently-played` scope confirmed via WebFetch.
- Spotify Web API Reference — `GET /me/tracks` [developer.spotify.com](https://developer.spotify.com/documentation/web-api/reference/get-users-saved-tracks): `user-library-read` scope, offset pagination, `added_at` field confirmed via WebFetch.

### Secondary (MEDIUM confidence)

- Spotify February 2026 Changelog [developer.spotify.com](https://developer.spotify.com/documentation/web-api/references/changes/february-2026) — `GET /v1/artists?ids=...` bulk endpoint removed; `GET /me/top` not in removed list. Verified via WebFetch.
- Spotify February 2026 Migration Guide [developer.spotify.com](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) — existing integration restrictions postponed. WebFetch confirmed.
- Spotify Blog Post February 2026 [developer.spotify.com](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security) — endpoint restriction postponement for existing integrations. WebFetch confirmed.

### Tertiary (LOW confidence)

- Spotify dev mode endpoint availability for `GET /me/top` — not explicitly confirmed in official docs. Multiple sources indicate it was not removed; treat as available with fallback plan.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries installed and version-verified
- Architecture patterns: HIGH — directly extrapolated from existing backend code
- Spotify API endpoints: HIGH — verified via official docs; dev mode nuance is MEDIUM
- Engine port: HIGH — source code read directly
- Pitfalls: HIGH (Spotify genre issue, snapshot migration, token refresh) — derived from code analysis

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (Spotify API docs stable; dev mode status may clarify)
