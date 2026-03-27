# Phase 6: Listening Stats Dashboard - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning
**Source:** Auto-mode (recommended defaults selected)

<domain>
## Phase Boundary

This phase delivers listening statistics API endpoints: users can view their top tracks, top artists, and top genres broken down by time period (last month, 6 months, all time). Works with either or both connected services. Builds on Phase 5's data fetchers and the service connection infrastructure.

Requirements: STAT-01 (top tracks by period), STAT-02 (top artists by period), STAT-03 (top genres by period), STAT-04 (works with one or both services).

</domain>

<decisions>
## Implementation Decisions

### Time Period Mapping
- **D-01:** Three time periods: "month" (last 30 days), "6months" (last 6 months), "alltime" (all available data).
- **D-02:** Spotify mapping: month → `time_range=short_term`, 6months → `time_range=medium_term`, alltime → `time_range=long_term`.
- **D-03:** Apple Music: no native time-range filtering. Compute from cached library + recently played data with timestamp-based filtering.

### Data Sources
- **D-04:** Spotify: use `GET /me/top/tracks` and `GET /me/top/artists` (native ranking from Spotify). For genres, derive from top artists' genre lists.
- **D-05:** Apple Music: compute top tracks from play count proxy + listening history frequency. Top artists from track artist aggregation. Top genres from track genre aggregation.
- **D-06:** Reuse Phase 5's fetch.py data fetchers for Apple Music library/recently-played data. Add new Spotify top tracks/artists fetchers.

### API Design
- **D-07:** Endpoints: GET /api/stats/tracks?period=month|6months|alltime&service=spotify|apple_music, GET /api/stats/artists (same params), GET /api/stats/genres (same params).
- **D-08:** Default period: "month". Default service: first connected service.
- **D-09:** Response includes rank, name, and metadata (album, genre, play count estimate where available).
- **D-10:** API-only. No frontend — Phase 11 handles visualization.
- **D-11:** Limit: top 20 items per endpoint by default, configurable via ?limit= param (max 50).

### Claude's Discretion
- Whether to create a StatsService class or use standalone functions
- Caching strategy for stats (compute on-demand vs cache with staleness)
- How to handle users with no data for a time period (empty list with message)
- Test mocking strategy

</decisions>

<canonical_refs>
## Canonical References

### Phase 5 Outputs (reuse heavily)
- `backend/src/musicmind/api/taste/fetch.py` — Apple Music + Spotify data fetchers to reuse/extend
- `backend/src/musicmind/api/taste/service.py` — TasteService pipeline pattern to follow
- `backend/src/musicmind/api/taste/router.py` — Router pattern to follow

### Phase 3 Outputs
- `backend/src/musicmind/api/services/service.py` — Token decryption, connection status helpers

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- Phase 5's fetch.py already has Spotify top_tracks and top_artists fetchers
- Phase 5's TasteService pattern (staleness check → fetch → compute → return)
- Existing router/schemas patterns from taste, auth, services, claude modules

</code_context>

<specifics>
## Specific Ideas

None — follows established Phase 5 patterns closely.

</specifics>

<deferred>
## Deferred Ideas

None.

</deferred>

---

*Phase: 06-listening-stats-dashboard*
*Context gathered: 2026-03-27 via auto-mode*
