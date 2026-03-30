# Changelog

All notable changes to SmarTaste (formerly MusicMind) will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
- **Playlists page** — full CRUD for playlists with AI context field. Backend: `GET/POST /api/playlists`, `GET/PATCH/DELETE /api/playlists/{id}`, `POST/DELETE` tracks. Frontend: playlist grid, create form with AI context textarea, detail view with track list and remove. New sidebar nav item.

### Fixed
- **Recommendations still showing non-Italian artists** — genre overlap filter now distinguishes regional genres ("Italian Hip-Hop/Rap") from parent genres ("Hip-Hop/Rap"). Prefers exact regional matches, limits parent-only fallback to 5. Applied to all 4 discovery strategies.
- **Scorer weight mismatch** — inline fallback weights in `score_candidate()` didn't match `DEFAULT_WEIGHTS`. Fixed to use consistent values.
- **Chart colors indistinguishable** — replaced all-green palette with 8 distinct colors (emerald, blue, amber, red, violet, pink, cyan, orange).

### Changed
- **Audio features now 75% of recommendation score** — `DEFAULT_WEIGHTS.audio` increased from 0.20 to 0.75. Genre reduced to 0.10, other dimensions proportionally reduced. This makes musical similarity (BPM, energy, danceability, etc.) the dominant scoring signal.

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
