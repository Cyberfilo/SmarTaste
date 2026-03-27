---
phase: 10-detail-views-and-responsive-polish
plan: 01
subsystem: api/detail-views
tags: [endpoints, scoring-breakdown, audio-features, RECO-07, RECO-08]
dependency_graph:
  requires: [07-recommendation-feed, 09-claude-chat-integration]
  provides: [scoring-breakdown-endpoint, audio-features-endpoint]
  affects: [frontend-detail-views]
tech_stack:
  added: []
  patterns: [service-layer-query, pydantic-response-model, tdd-red-green]
key_files:
  created:
    - backend/src/musicmind/api/tracks/__init__.py
    - backend/src/musicmind/api/tracks/schemas.py
    - backend/src/musicmind/api/tracks/service.py
    - backend/src/musicmind/api/tracks/router.py
    - backend/tests/test_detail_views.py
    - backend/tests/test_detail_views_schemas.py
  modified:
    - backend/src/musicmind/api/recommendations/schemas.py
    - backend/src/musicmind/api/recommendations/service.py
    - backend/src/musicmind/api/recommendations/router.py
    - backend/src/musicmind/api/router.py
decisions:
  - "7 reportable dimensions exclude cross_strategy_bonus, mood_boost, and raw diversity_penalty/staleness; diversity and anti_staleness are inverted (1.0 - penalty)"
  - "instrumentalness field included in AudioFeaturesResponse as None for API contract completeness (not stored in DB)"
  - "INFR-04 (responsive design) deferred to Phase 11 where the frontend is built"
metrics:
  duration: 5min
  completed: 2026-03-27
  tasks: 2
  files: 10
---

# Phase 10 Plan 01: Detail View Endpoints Summary

Two new GET endpoints exposing scoring breakdown (RECO-07) and per-track audio features (RECO-08) from existing data stores, with TDD-driven development and full integration test coverage.

## What Was Built

### Task 1: Schemas and Service Methods (1f9708c)

Added Pydantic models and service methods for the two detail-view endpoints:

- **BreakdownDimension** and **BreakdownResponse** models in `recommendations/schemas.py` -- catalog_id, overall_score, 7 dimensions (each with name/label/score/weight), explanation
- **AudioFeaturesResponse** model in new `tracks/schemas.py` -- catalog_id plus 8 audio feature fields (energy, danceability, valence, acousticness, tempo, instrumentalness, beat_strength, brightness)
- **RecommendationService.get_scoring_breakdown()** -- looks up song in song_metadata_cache and taste profile, calls score_candidate(), maps _breakdown keys to 7 reportable dimensions with DEFAULT_WEIGHTS
- **TrackService.get_audio_features()** -- queries audio_features_cache, maps valence_proxy to valence, sets instrumentalness=None
- 5 unit tests for schema fields and service methods

### Task 2: Router Endpoints and Integration Tests (64560fe)

Wired up the endpoints and created comprehensive integration tests:

- **GET /api/recommendations/{catalog_id}/breakdown** -- returns 200 with 7-dimension scoring breakdown, 404 for missing track/profile, 401 without auth
- **GET /api/tracks/{catalog_id}/audio-features** -- returns 200 with audio features, 404 with "Audio features not available for this track", 401 without auth
- Tracks router wired into `api_router` in `router.py`
- 7 integration tests covering all success, error, and auth scenarios

## Decisions Made

1. **7 dimensions mapped from scorer breakdown**: genre_match, audio_similarity, novelty, freshness, diversity (1.0 - diversity_penalty), artist_affinity (artist_match), anti_staleness (1.0 - staleness). Cross-strategy bonus, mood boost excluded as they are bonuses, not scored dimensions.
2. **instrumentalness=None**: Included in the API response for contract completeness but always None since the audio_features_cache table does not store it. Phase 11 frontend can show "N/A".
3. **INFR-04 deferred**: Responsive design is Phase 11 scope (frontend build). This plan is backend-only.

## Verification Results

- All 294 tests pass (282 existing + 5 unit + 7 integration)
- No ruff lint errors in new files (8 pre-existing errors in other files, out of scope)
- Breakdown endpoint returns correct 7-dimension structure verified by test_breakdown_returns_7_dimensions
- Audio features endpoint returns correct data verified by test_audio_features_returns_data
- 404 behavior verified by test_breakdown_not_found and test_audio_features_not_found
- Auth required verified by test_breakdown_unauthenticated and test_audio_features_unauthenticated

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None. All endpoints return real data from existing database tables. instrumentalness=None is documented as intentional (not stored in DB).

## Self-Check: PASSED

All 6 created files verified on disk. Both task commits (1f9708c, 64560fe) verified in git log.
