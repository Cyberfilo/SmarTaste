---
phase: 08-multi-service-unification
plan: "01"
subsystem: engine
tags: [genre-normalization, deduplication, isrc, cross-service, unified-profile, taste-profile]

# Dependency graph
requires:
  - phase: 05-taste-profile-dashboard
    provides: TasteService pipeline, engine profile.py, genre vectors, song_metadata_cache schema
  - phase: 06-listening-stats-dashboard
    provides: Multi-service stats fetchers, time-range-parameterized data access
  - phase: 07-recommendation-feed
    provides: RecommendationService pipeline, scorer, discovery strategies, mood filtering
provides:
  - Canonical genre taxonomy normalizer (genres.py) mapping Spotify and Apple Music genres to shared form
  - Cross-service track deduplication module (dedup.py) via ISRC primary and fuzzy title+artist fallback
  - Unified taste profile pipeline merging both services with dedup and genre normalization
  - Cross-service recommendation discovery running strategies against both catalogs in parallel
affects: [claude-chat-integration, detail-views, ui-design]

# Tech tracking
tech-stack:
  added: []
  patterns: [cross-service-dedup, canonical-genre-mapping, unified-profile-pipeline, parallel-multi-service-discovery]

key-files:
  created:
    - backend/src/musicmind/engine/genres.py
    - backend/src/musicmind/engine/dedup.py
    - backend/tests/test_genre_normalizer.py
    - backend/tests/test_dedup.py
    - backend/tests/test_unified_taste.py
    - backend/tests/test_unified_recommendations.py
  modified:
    - backend/src/musicmind/api/taste/service.py
    - backend/src/musicmind/api/taste/schemas.py
    - backend/src/musicmind/api/taste/router.py
    - backend/src/musicmind/api/recommendations/service.py

key-decisions:
  - "Canonical genre form uses Apple Music Title Case convention (preserves regional prefixes and slash-separated parent/child)"
  - "Two-phase dedup: ISRC match first, fuzzy title+artist fallback for tracks without ISRC"
  - "Unified auto-detection: both services connected -> default to 'unified'; explicit service param overrides"
  - "Cross-service recommendations run discovery against all connected services in parallel via asyncio.gather"
  - "Genre normalization applied to both songs and history entries before profile building"

patterns-established:
  - "Cross-service dedup: ISRC primary key match -> fuzzy title+artist fallback -> metadata merge"
  - "Genre normalization: CANONICAL_MAP lookup with case-insensitive matching, unknown genres pass through"
  - "Unified profile: fetch both services, deduplicate, normalize genres, build single merged profile"
  - "Multi-service discovery: resolve credentials for all services, run strategies in parallel, cross-service dedup candidates"

requirements-completed: [MSVC-01, MSVC-02, MSVC-03, MSVC-04, TAST-05]

# Metrics
duration: 11min
completed: 2026-03-27
---

# Phase 8 Plan 1: Genre Normalization, Track Deduplication, and Unified Profile Summary

**Cross-service unification layer: canonical genre taxonomy mapping 130+ Spotify/Apple Music genre variants, ISRC+fuzzy track deduplication, unified taste profile merging both services, and parallel cross-catalog recommendation discovery**

## Performance

- **Duration:** 11 min
- **Started:** 2026-03-27T19:00:09Z
- **Completed:** 2026-03-27T19:11:09Z
- **Tasks:** 4
- **Files modified:** 10

## Accomplishments
- Canonical genre taxonomy normalizer with 130+ mappings from Spotify lowercase-hyphenated and Apple Music Title Case to shared canonical form
- Cross-service track deduplication using ISRC primary matching (case-insensitive) with fuzzy title+artist fallback for tracks without ISRC codes
- Unified taste profile pipeline that fetches from both services, deduplicates songs, normalizes genres, and builds a single merged profile with services_included metadata
- Cross-service recommendation discovery running all 4 strategies against both Spotify and Apple Music catalogs in parallel with ISRC+fuzzy candidate dedup

## Task Commits

Each task was committed atomically:

1. **Task 1: Genre taxonomy normalizer module** - `59f633f` (feat)
2. **Task 2: Track deduplication module** - `e5b8e07` (feat)
3. **Task 3: Unified taste profile service** - `a5e315c` (feat)
4. **Task 4: Cross-service recommendation support** - `81ed8aa` (feat)

## Files Created/Modified
- `backend/src/musicmind/engine/genres.py` - Canonical genre mapping (CANONICAL_MAP), normalize_genre, normalize_genre_list, merge_genre_vectors
- `backend/src/musicmind/engine/dedup.py` - ISRC-based dedup, fuzzy title+artist matching, metadata merge, deduplicate_tracks pipeline
- `backend/src/musicmind/api/taste/service.py` - Extended with _build_unified_profile, updated _resolve_service for auto-detection
- `backend/src/musicmind/api/taste/schemas.py` - TasteProfileResponse extended with services_included field
- `backend/src/musicmind/api/taste/router.py` - Passes services_included through to response
- `backend/src/musicmind/api/recommendations/service.py` - _resolve_all_credentials for multi-service, parallel discovery, cross-service candidate dedup
- `backend/tests/test_genre_normalizer.py` - 25 tests for genre normalization
- `backend/tests/test_dedup.py` - 34 tests for track deduplication
- `backend/tests/test_unified_taste.py` - 12 tests for unified taste profile
- `backend/tests/test_unified_recommendations.py` - 5 tests for cross-service recommendations

## Decisions Made
- Canonical genre form uses Apple Music Title Case convention (preserves more information than Spotify lowercase)
- Two-phase dedup: ISRC primary (case-insensitive) then fuzzy title+artist fallback (strips parentheticals, featuring credits, accents)
- Metadata merge prefers richer data: longer genre list, non-empty editorial notes, preserves both catalog IDs via _service_ids
- Auto-detection: when service=None and both services connected, automatically returns "unified" profile
- Explicit service=spotify/apple_music parameter still works for single-service view (backward compatible)
- RecommendationService refactored from _resolve_credentials (single) to _resolve_all_credentials (list of all connected services)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Multi-service unification layer complete, all MSVC requirements satisfied
- Ready for Phase 9 (Claude Chat Integration) which depends on Phases 4 and 7
- Unified taste profile and cross-service recommendations available for Claude tool_use integration
- 222 total tests pass (146 original + 76 new)

## Self-Check: PASSED

All 10 created/modified files verified present. All 4 task commit hashes verified in git log.

---
*Phase: 08-multi-service-unification*
*Completed: 2026-03-27*
