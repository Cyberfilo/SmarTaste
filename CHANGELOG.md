# Changelog

All notable changes to MusicMind MCP are documented here.

Versioning: **VX.YZ** — X = major change, Y = minor logic change, Z = bug fix.

## [V2.20] - 2026-03-26

### Added
- **`musicmind_curate_playlist` tool** — Claude-as-DJ workflow: pass a list of "artist - title" queries, the tool resolves each to a catalog ID via search, creates the playlist in one call. Eliminates the 20+ tool-call loop for curated playlists
- **`musicmind_taste_deep` tool** — comprehensive taste briefing combining profile + recent listening + genre/artist patterns + familiarity score + era preference + feedback history in a single call. Designed to give Claude maximum context before curating
- **Regional scene keywords in `_parse_vibe()`** — Italian (milanese, napoletano, romano, street, gasa), UK (grime, ukdrill), French (francese), Afro/Latin (afrobeats, dembow), mood-based Italian (carica, malinconica, estate), plus Italian stop words
- **Profile-seeded search terms** — `_parse_vibe()` now accepts the user's taste profile and uses top 3 genres as additional search seeds, combining them with vibe keywords

### Fixed
- **`format_song_md()` now includes catalog IDs** — all tool outputs (search, library, recently_played, lookup_artist top songs, lookup_album tracklist, charts) now show `ID: \`catalog_id\`` so Claude can collect IDs without extra tool calls. This was the root cause of playlist creation failing in loops
- **`format_album_md()` and `format_artist_md()`** also include resource IDs

### Changed
- `musicmind_smart_playlist` docstring updated to recommend `musicmind_curate_playlist` for underground/regional scenes
- `musicmind_taste_profile` docstring updated to recommend `musicmind_taste_deep` for comprehensive briefing
- `_parse_vibe()` search term limit increased from 5 to 8 for better coverage
- 32 tools total (was 30), 150 tests passing

## [V2.10] - 2026-03-26

### Changed
- **Regional genre prioritization** — original genre names (e.g., "Italian Hip-Hop/Rap") now get full weight (1.0) in genre vectors and cosine scoring, while expanded parents ("Hip-Hop/Rap") get 0.3. This ensures a user who listens to 90% Italian music gets Italian recommendations, not generic American equivalents matching the parent genre
- **Weight rebalance (genre-first)** — genre: 0.35 (was 0.25), audio: 0.20 (was 0.15), artist: 0.08 (was 0.15). Style/beat matters more than specific artist
- **Artist-in-wrong-genre penalty** — known artist with genre cosine < 0.2 gets artist_match reduced to 30%. Prevents "you like Artist X" from recommending their one country song to a drill listener
- **similar_artist_crawl** default depth reduced to 1 (was 2) — two hops drifts too far from original style
- **chart_filter** now passes user's #1 genre to API and pre-filters candidates by top-5 genre overlap — no more random global pop polluting results
- **genre_adjacent_explore** uses full regional genre names in searches, increased limit from 10 to 15, filters out candidates with zero genre overlap
- **editorial_mining** uses full regional genre names in search queries and increased limit from 10 to 15

## [V2.00] - 2026-03-26

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

## [V1.00] - 2026-03-26

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
