# Changelog

All notable changes to SmarTaste (formerly MusicMind) will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Onboarding taste calibration wizard** — 3-step flow after service connection: pick playlists (5x weight), drag-to-reorder artists (top 3 get discography enrichment), pick favorite songs (3x weight). Calibration section in settings for viewing/editing. Dashboard summary card.
- **Context-adaptive scoring weights** — weights shift per-user based on profile: regional listeners get higher language weight (up to 35%), global listeners drop to 5%. Mood active → audio at 40%. Calibration present → artist +5%. Blends 60/40 with feedback-learned weights.
- **Play count proxy** — Apple Music has no play counts. `play_count_proxy` table now populated from recently-played polling. Songs with high `seen_count` get proportional weight in profile (capped at 10x). 7-day recency boost (2x).
- **Calibration boost in scorer** — continuous `min(0.20, weight * 0.03)`, zeroed if `genre_score < 0.15` (artist-in-wrong-genre penalty).
- **Request logging middleware** — every request logged with method, path, status, duration_ms, user_id. Slow requests (>5s) flagged.
- **Separate logging PostgreSQL** — `request_logs`, `enrichment_logs`, `error_logs` with batched async writer (5s flush). `MUSICMIND_LOGS_DATABASE_URL` env var. Graceful degradation if logs DB down.
- **NocoDB admin UI** — `nocodb/nocodb` Docker image on Railway for spreadsheet-style browsing/querying of both databases.
- **Frontend error boundary** — `error.tsx` with retry button. Global mutation error toast via `MutationCache.onError`.
- **429 retry with backoff** — `_request_with_retry()` helper in discovery fetch: exponential backoff on 429/5xx, respects Retry-After header, max 3 retries.
- **Rate limits** — taste 20/min, calibration 10/min, profile refresh 5/min (previously unlimited).
- **Spotify genre backfill** — discovery strategies now attach artist genres to Spotify tracks (were always empty, scoring 0 on genre/language).

### Fixed
- **Apple Music OAuth popup blocked** — pre-load MusicKit JS + developer token on mount; authorize() now within browser's user activation window.
- **Apple Music OAuth silent failure** — removed `window.location.href="/login"` from apiFetch 401 handler; per-step error toasts.
- **Deezer call signature crash** — `fetch_deezer_features(isrc)` → `fetch_deezer_features(name=, artist_name=, isrc=)`.
- **Calibration save timeout** — profile rebuild moved to background task (was blocking 30s+).
- **Calibration duplicate key** — dedup by `(calibration_type, item_id)` before insert.
- **Calibration redirect race** — `setQueryData` instead of `invalidateQueries` for immediate cache update.
- **18 missing httpx timeouts** — all `AsyncClient()` calls now have 30s timeout (was infinite hang risk).
- **Silent enrichment failures** — replaced 3 bare `except: pass` with debug logging.

### Changed
- **Scoring weights rebalanced** — genre 35%, audio 25%, artist 20%, language 20% (was 45/32/13/10).
- **Language match neutral** — non-regional songs score 0.5 (was 0.2 penalty).
- **Enrichment polling 3s → 15s**, /me calls 30s → 60s.
- **Layout renders immediately** — skeleton UI instead of blocking auth spinner.

---

- **Synesthesia visual identity** — new color palette (deep purple backgrounds, electric violet primary, magenta energy, coral warmth, mint freshness), Sora Bold headings + DM Sans body typography, logo integration in sidebar/header/auth. Replaces the emerald theme entirely.
- **Audio enrichment pipeline** — progressive background enrichment from free external APIs:
  - Deezer (ISRC-native, free) for BPM/tempo
  - ReccoBeats (Spotify ID, free) for energy, danceability, valence, acousticness
  - SoundStat (Spotify ID, paid, budget-gated) for complete features including key/scale
  - MusicBrainz ISRC→Spotify ID resolver with permanent caching
- **Audio features now active in scoring** — the 20% audio_similarity weight is no longer neutral. User audio centroid computed from enriched features and used in `rank_candidates()`.
- **Mood filtering with audio features** — `filter_candidates_by_mood` now receives actual audio features.
- New DB tables: `isrc_spotify_mapping` for permanent ISRC→Spotify ID cache
- Expanded `audio_features_cache` with: key, scale, instrumentalness, loudness, feature_source (provenance), enriched_at
- `audio_centroid` saved in taste profile snapshots
- `MUSICMIND_SOUNDSTAT_API_KEY` env var for optional paid enrichment
- **Featuring artist parsing** — `parse_artists()` splits "Artist feat. B & C" into weighted tuples. Primary artists get 1.0 weight, featuring artists get 0.3. Applied to taste profile artist affinity and stats top artists.
- **Dashboard filled with visualizations** — genre donut chart, top 8 artists with affinity bars, audio traits radar, release year bar chart. All from existing taste profile API data.
- **Listening timeline** — `GET /api/stats/timeline` endpoint returns songs in chronological order with date type labels (added/release). Frontend component groups by month with artwork, song details, and date badges.
- **On-demand Essentia deep analysis** — `POST /api/tracks/analyze` accepts up to 10 seed track IDs, downloads 30s previews (Apple Music AAC / Deezer MP3 fallback), extracts 128-dim Discogs-EffNet embeddings + scalar features. Gracefully degrades when Essentia is not installed.
- **Playlists page** — shows real playlists from connected Apple Music and Spotify. Grid view with artwork, click to see tracks. Backend fetches live from service APIs: `GET /api/playlists`, `GET /api/playlists/{id}/tracks?service=`. Added `playlist-read-private` scope for Spotify.

### Fixed
- **Recommendations still showing non-Italian artists** — genre overlap filter now distinguishes regional genres ("Italian Hip-Hop/Rap") from parent genres ("Hip-Hop/Rap"). Prefers exact regional matches, limits parent-only fallback to 5. Applied to all 4 discovery strategies.
- **Scorer weight mismatch** — inline fallback weights in `score_candidate()` didn't match `DEFAULT_WEIGHTS`. Fixed to use consistent values.
- **Chart colors indistinguishable** — replaced all-green palette with 8 distinct colors (emerald, blue, amber, red, violet, pink, cyan, orange).

### Changed
- **Scorer rebalanced: language 45%, audio 32%, genre 13%, artist 10%** — new `_language_match` dimension detects regional genre prefixes ("Italian Hip-Hop/Rap" → "Italian"). Italian tracks score 1.0, generic tracks 0.2, wrong-region 0.0. This makes language/region the dominant signal, with audio similarity second.
- **Enrichment pipeline fixed: SoundStat-only** — Deezer ISRC lookup broken (returns "no data"), ReccoBeats returns 404. Removed both dead sources. SoundStat is now the sole enrichment API.
- Removed novelty/freshness/staleness from weighted sum (kept as minor penalties)

### Fixed
- **Recommendation strategies fail with 400 error** — frontend sent `auto`/`similar_artists`/`charts` but backend expects `all`/`similar_artist`/`chart`. Fixed strategy-selector.tsx values and default state.
- **Apple Music listening stats always empty** — `dateAdded` field missing from API response caused `_filter_songs_by_period()` to skip every song. Added `releaseDate` fallback and robust date parsing.
- **Recommendations irrelevant for regional listeners** — discovery strategies used US storefront (`storefront="us"`) instead of user's actual region, and `genre_adjacent`/`editorial` had no genre overlap filtering. Now auto-detects storefront via `/v1/me/storefront` and filters candidates by genre overlap with user profile.

## [0.1.0] - 2026-03-30

### Added
- Complete project documentation (`what.md`)
- Centralized `VERSION` file — single source of truth for version numbers
- `CHANGELOG.md` for tracking changes
- Version reported in `/health` endpoint response

### Removed
- ~1,350 lines of dead code:
  - `engine/bandit.py` — Thompson Sampling (never wired into scorer)
  - `engine/clap_mood.py` — CLAP mood embeddings (dependency missing)
  - `engine/lastfm.py` — Last.fm tag enrichment (never integrated)
  - `engine/knowledge_graph/` — MusicBrainz graph (tables never populated)
  - `engine/audio/extractor.py` — Essentia extraction (never called)
  - `frontend/src/proxy.ts` — replaced by `next.config.ts` rewrites

### Changed
- **Rebranded from MusicMind to SmarTaste** — all user-facing text, UI, docs, test docstrings
  - Python package name (`musicmind`) and env prefix (`MUSICMIND_`) kept for backward compatibility
  - Docker/DB credentials unchanged to avoid migration issues
- Backend `__version__` now reads from root `VERSION` file
- FastAPI app version now dynamic (reads from `__version__`)
- `pyproject.toml` uses Hatch dynamic versioning from `VERSION` file
