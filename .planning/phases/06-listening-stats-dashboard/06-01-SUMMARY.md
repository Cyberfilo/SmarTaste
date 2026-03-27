---
phase: 06-listening-stats-dashboard
plan: 01
subsystem: api
tags: [pydantic, spotify-api, apple-music, listening-stats, httpx]

# Dependency graph
requires:
  - phase: 05-taste-profile-dashboard
    provides: "TasteService pattern, fetch.py data fetchers, schema patterns"
  - phase: 03-service-connections
    provides: "Token management, get_user_connections, refresh_spotify_token"
provides:
  - "Six Pydantic response schemas for stats endpoints (StatTrackEntry, StatArtistEntry, StatGenreEntry, TopTracksResponse, TopArtistsResponse, TopGenresResponse)"
  - "Time-range-parameterized Spotify fetchers (fetch_spotify_top_tracks_for_period, fetch_spotify_top_artists_for_period)"
  - "Apple Music stats computation from cached library data (compute_apple_music_top_tracks, compute_apple_music_top_artists)"
  - "StatsService class orchestrating fetch+compute+rank pipeline for both services"
affects: [06-02-PLAN, frontend-stats-dashboard]

# Tech tracking
tech-stack:
  added: []
  patterns: [time-range-parameterized-fetchers, genre-derivation-from-artist-track-crossref]

key-files:
  created:
    - backend/src/musicmind/api/stats/__init__.py
    - backend/src/musicmind/api/stats/schemas.py
    - backend/src/musicmind/api/stats/fetch.py
    - backend/src/musicmind/api/stats/service.py
  modified: []

key-decisions:
  - "StatsService as on-demand (no caching) -- Spotify handles period filtering natively, Apple Music library is lightweight"
  - "Genre derivation via artist-track cross-reference for Spotify (tracks lack genres, artists carry them)"
  - "Apple Music top tracks ranked by date_added_to_library descending as listening frequency proxy"

patterns-established:
  - "Time-range mapping: month->short_term, 6months->medium_term, alltime->long_term for Spotify API"
  - "Period-based date filtering for Apple Music using PERIOD_DAYS_MAP"

requirements-completed: [STAT-01, STAT-02, STAT-03, STAT-04]

# Metrics
duration: 4min
completed: 2026-03-27
---

# Phase 6 Plan 1: Stats Data Layer Summary

**StatsService with Spotify time-range top items API and Apple Music library-computed rankings for tracks, artists, and genres**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-27T12:28:00Z
- **Completed:** 2026-03-27T12:32:26Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Six Pydantic response schemas for stats endpoints matching established patterns
- Spotify fetchers parameterized by period (month/6months/alltime) mapping to time_range API parameter
- Apple Music top tracks/artists computed from cached library with timestamp filtering
- StatsService orchestrating fetch + genre derivation + ranking for both services with token refresh

## Task Commits

Each task was committed atomically:

1. **Task 1: Stats package init, Pydantic schemas, and time-range-parameterized fetchers** - `5b28267` (feat)
2. **Task 2: StatsService orchestrating fetch, genre derivation, and ranked responses** - `d7f78da` (feat)

## Files Created/Modified
- `backend/src/musicmind/api/stats/__init__.py` - Empty package init
- `backend/src/musicmind/api/stats/schemas.py` - Six Pydantic response models for stats endpoints
- `backend/src/musicmind/api/stats/fetch.py` - Spotify time-range fetchers and Apple Music compute functions
- `backend/src/musicmind/api/stats/service.py` - StatsService class with get_top_tracks, get_top_artists, get_top_genres

## Decisions Made
- StatsService is on-demand (no caching) since Spotify handles period filtering natively and Apple Music library is lightweight
- Genre derivation for Spotify cross-references top tracks with top artists to build genre tallies (tracks lack genres, artists carry them)
- Apple Music top tracks use date_added_to_library descending as a proxy for listening frequency (no play count API)
- Period filtering for Apple Music: month=30 days, 6months=180 days, alltime=no filter

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed missing ruff dev dependency**
- **Found during:** Task 1 (verification step)
- **Issue:** ruff was not installed in the worktree virtual environment
- **Fix:** Added ruff as dev dependency via `uv add --dev ruff`
- **Files modified:** backend/pyproject.toml (dev dependency added by uv)
- **Verification:** `uv run ruff check src/musicmind/api/stats/` passes
- **Committed in:** part of environment setup, not committed separately (pyproject.toml changes managed by uv)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Minor tooling fix, no scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Stats data layer complete with schemas, fetchers, and service
- Plan 02 can build router endpoints calling StatsService directly
- All four files pass ruff lint and import verification

## Self-Check: PASSED

- All 4 created files verified on disk
- Both task commits (5b28267, d7f78da) verified in git log
- All ruff lint checks pass
- All import verification checks pass

---
*Phase: 06-listening-stats-dashboard*
*Completed: 2026-03-27*
