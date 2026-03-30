# Changelog

All notable changes to SmarTaste (formerly MusicMind) will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Recommendation strategies fail with 400 error** — frontend sent `auto`/`similar_artists`/`charts` but backend expects `all`/`similar_artist`/`chart`. Fixed strategy-selector.tsx values and default state.
- **Apple Music listening stats always empty** — `dateAdded` field missing from API response caused `_filter_songs_by_period()` to skip every song. Added `releaseDate` fallback and robust date parsing.

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
