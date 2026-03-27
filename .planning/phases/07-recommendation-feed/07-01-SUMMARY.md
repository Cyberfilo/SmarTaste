---
phase: 07-recommendation-feed
plan: 01
subsystem: engine
tags: [numpy, scoring, discovery, pydantic, httpx, recommendations]

# Dependency graph
requires:
  - phase: 05-taste-profile-dashboard
    provides: "Engine profile.py with expand_genres and build_taste_profile"
provides:
  - "7-dimension candidate scorer (scorer.py) with MMR diversity"
  - "Adaptive weight optimizer (weights.py) with coordinate descent"
  - "6 mood profiles with filter_candidates_by_mood (mood.py)"
  - "Genre jaccard and audio feature similarity (similarity.py)"
  - "4 discovery fetch strategies for Spotify and Apple Music (fetch.py)"
  - "Pydantic schemas: RecommendationsResponse, FeedbackRequest, FeedbackResponse"
affects: [07-recommendation-feed, 08-claude-chat]

# Tech tracking
tech-stack:
  added: [numpy>=1.26]
  patterns: [verbatim-engine-port, dual-service-discovery, field-validator-pattern]

key-files:
  created:
    - backend/src/musicmind/engine/scorer.py
    - backend/src/musicmind/engine/weights.py
    - backend/src/musicmind/engine/mood.py
    - backend/src/musicmind/engine/similarity.py
    - backend/src/musicmind/api/recommendations/__init__.py
    - backend/src/musicmind/api/recommendations/schemas.py
    - backend/src/musicmind/api/recommendations/fetch.py
  modified:
    - backend/pyproject.toml
    - backend/uv.lock

key-decisions:
  - "Engine modules copied verbatim from MCP source (D-02) -- no multi-user adaptations needed at engine level"
  - "Discovery fetch functions branch on service param for Spotify/Apple Music dual support (D-03)"
  - "FeedbackRequest uses Pydantic field_validator to reject invalid feedback types at schema level"

patterns-established:
  - "Verbatim engine port: copy MCP engine modules unchanged, import paths resolve via backend package structure"
  - "Dual-service fetch: each discovery function accepts service param and branches on Spotify vs Apple Music API"

requirements-completed: [RECO-01, RECO-02, RECO-05, RECO-06]

# Metrics
duration: 6min
completed: 2026-03-27
---

# Phase 7 Plan 1: Engine Port & Discovery Layer Summary

**7-dimension adaptive scorer, mood filter, weight optimizer, and 4 dual-service discovery strategies for Spotify and Apple Music**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-27T13:33:38Z
- **Completed:** 2026-03-27T13:40:04Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments
- Ported 4 MCP engine modules (scorer, weights, mood, similarity) verbatim into the web backend with numpy dependency
- Created 4 discovery fetch functions (similar_artists, genre_adjacent, editorial, chart_filter) supporting both Spotify and Apple Music
- Defined Pydantic schemas for recommendation response, feedback request/response with validation

## Task Commits

Each task was committed atomically:

1. **Task 1: Port engine modules and add numpy dependency** - `f7cfb31` (feat)
2. **Task 2: Create Pydantic schemas and discovery fetch functions** - `2392318` (feat)

## Files Created/Modified
- `backend/pyproject.toml` - Added numpy>=1.26 dependency
- `backend/uv.lock` - Lock file updated with numpy
- `backend/src/musicmind/engine/scorer.py` - 7-dimension candidate scoring with genre cosine, MMR diversity, cross-strategy bonus
- `backend/src/musicmind/engine/weights.py` - DEFAULT_WEIGHTS dict and optimize_weights() coordinate descent
- `backend/src/musicmind/engine/mood.py` - 6 mood profiles (workout, chill, focus, party, sad, driving) with filter_candidates_by_mood
- `backend/src/musicmind/engine/similarity.py` - genre_jaccard, audio_feature_similarity, song_similarity, classification_similarity
- `backend/src/musicmind/api/recommendations/__init__.py` - Empty package init
- `backend/src/musicmind/api/recommendations/schemas.py` - RecommendationsResponse, RecommendationItem, FeedbackRequest, FeedbackResponse
- `backend/src/musicmind/api/recommendations/fetch.py` - 4 discovery strategies with dual Spotify/Apple Music support

## Decisions Made
- Engine modules copied verbatim from MCP source per D-02 -- import paths (musicmind.engine.profile, musicmind.engine.weights) resolve correctly in backend package structure without modification
- Discovery fetch functions adapted from MCP's discovery.py which used AppleMusicClient directly; new version uses raw httpx calls against both Spotify and Apple Music APIs
- FeedbackRequest allows "skip" instead of MCP's "skipped" to match web API conventions; the weights module's FEEDBACK_TARGETS still maps "skipped" for internal scoring

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Engine scoring core ready for Plan 02's RecommendationService to orchestrate
- Schemas define the API contract for recommendation endpoints
- Discovery fetch layer provides all 4 strategies for candidate acquisition
- Plan 02 will wire the service layer, router, and tests

## Self-Check: PASSED

All 8 created files verified present. Both task commits (f7cfb31, 2392318) verified in git log.

---
*Phase: 07-recommendation-feed*
*Completed: 2026-03-27*
