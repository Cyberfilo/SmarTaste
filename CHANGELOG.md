# Changelog

All notable changes to MusicMind MCP are documented here.

## [0.2.0] - 2026-03-26

### Added
- **Adaptive recommendation engine** — scoring weights that learn from user feedback
- **`musicmind_feedback` tool** — give thumbs up/down, mark as added/skipped to train the engine
- **Temporal decay** — exponential decay weighting (90-day half-life) for genre vectors and artist affinity, so recent listening counts more than old library additions
- **Audio feature extraction** (Tier 2) — 7-dimension analysis (tempo, energy, brightness, danceability, acousticness, valence proxy, beat strength) from 30-second Apple Music previews via librosa
- **SoundAnalysis integration** (Tier 3) — optional macOS-only classification labels via Swift CLI helper
- **Mood filtering** — 6 mood profiles (workout, chill, focus, party, sad, driving) with genre heuristics and audio feature ranges
- **Anti-staleness** — recently recommended songs get a cooldown penalty (7-day and 30-day tiers)
- **Cross-strategy convergence** — bonus scoring when multiple discovery strategies independently find the same song
- **Graduated novelty** — Gaussian bell curve scoring that peaks at moderate genre distance, rewarding "familiar genre, new artist" combinations
- **Play count proxy** — tracks song appearance frequency in recently-played to approximate play counts
- 4 new DB tables: `recommendation_feedback`, `audio_features_cache`, `sound_classification_cache`, `play_count_proxy`
- `mood` parameter on `musicmind_discover` tool
- `audio_feature_similarity()` and `classification_similarity()` in similarity engine
- `build_audio_centroid()` for computing weighted average audio profile

### Changed
- Scorer now uses 10 dimensions (was 5) with adaptive weights from feedback
- `rank_candidates()` accepts audio features, adaptive weights, and recent recommendations
- `musicmind_taste_profile` and `musicmind_refresh_cache` use temporal decay by default
- `musicmind_recently_played` now tracks play count observations
- `musicmind_why_this_song` shows audio similarity breakdown when features available

## [0.1.0] - 2026-03-26

### Added
- **Project scaffold** — uv project, FastMCP server, ES256 JWT auth, browser-based OAuth setup wizard
- **Apple Music API client** — async httpx client with 25+ endpoints covering library, catalog, history, and write operations; 429 retry with exponential backoff
- **SQLite persistence** — 5 tables (listening_history, song_metadata_cache, artist_cache, taste_profile_snapshots, generated_playlists) with async queries via SQLAlchemy Core + aiosqlite
- **29 MCP tools** across 7 domains:
  - Library (6): songs, albums, artists, playlists, playlist tracks, search
  - Catalog (7): search, lookup song/artist/album, charts, activities, genres
  - Playback (3): recently played, heavy rotation, Apple recommendations
  - Manage (4): create playlist, add tracks, add to library, rate songs
  - Taste (4): profile, compare, stats, why-this-song
  - Recommend (3): discover, smart playlist, refresh cache
  - System (2): health, help
- **Taste engine** — genre vectors with hierarchical expansion, artist affinity scoring, release year distribution, audio trait preferences, familiarity score via Shannon entropy
- **4 discovery strategies** — similar artist crawl, genre-adjacent exploration, editorial mining, chart filtering
- **Candidate scoring** — genre cosine similarity, artist match, novelty bonus, freshness, MMR diversity penalty
- **Smart playlist generation** — natural language vibe parsing to Apple Music playlist creation
- Auto-caching: every API response that returns song/artist data is upserted into SQLite
- All tools return markdown for human readability
- 94 tests, ruff lint clean
