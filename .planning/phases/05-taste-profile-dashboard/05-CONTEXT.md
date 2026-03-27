# Phase 5: Taste Profile Dashboard - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning
**Source:** Auto-mode (recommended defaults selected)

<domain>
## Phase Boundary

This phase delivers taste profile API endpoints: users can view their top genres (with regional specificity), top artists, audio trait preferences, and a taste profile built from a single connected service. Builds on Phase 3's service connections and the existing MCP engine's profile building logic.

Requirements: TAST-01 (top genres), TAST-02 (top artists), TAST-03 (audio traits), TAST-04 (single-service profile).

Note: TAST-05 (unified multi-service profile) is Phase 8, not this phase.

</domain>

<decisions>
## Implementation Decisions

### Engine Integration
- **D-01:** Port the existing `build_taste_profile()` from `src/musicmind/engine/profile.py` into the webapp backend. Adapt for multi-user (user_id scoping) and multi-service (service_source filtering).
- **D-02:** The engine modules (profile.py, similarity.py) are the source of truth for taste computation. Copy and adapt, don't rewrite from scratch.
- **D-03:** Profile computation fetches user's library data via the appropriate music service API (Apple Music or Spotify), caches it in the database, then runs the taste profile algorithm on the cached data.

### Data Pipeline
- **D-04:** When a user requests their taste profile, the backend: (1) checks if cached profile is fresh enough (< 24h), (2) if stale, fetches library/recently-played from the connected service API, (3) caches the raw data, (4) runs build_taste_profile on cached data, (5) returns structured JSON.
- **D-05:** Apple Music data fetching uses the existing client patterns from `src/musicmind/client.py`. Spotify data fetching uses httpx with the user's stored (decrypted) access token.
- **D-06:** Profile staleness threshold: 24 hours. User can force refresh via API parameter.

### API Design
- **D-07:** Endpoints: GET /api/taste/profile (full taste profile), GET /api/taste/genres (top genres), GET /api/taste/artists (top artists), GET /api/taste/audio-traits (audio preferences).
- **D-08:** All endpoints accept optional `?service=spotify|apple_music` query param. If omitted, uses the first connected service.
- **D-09:** Response format: structured JSON with typed fields. Frontend (Phase 11) will render visualizations from this data.
- **D-10:** API-only in this phase. No frontend rendering — that's Phase 11's job.

### Genre Handling
- **D-11:** Preserve regional genre specificity from the existing engine. "Italian Hip-Hop/Rap" stays as-is, not collapsed to "Hip-Hop/Rap".
- **D-12:** Genre vector from the engine uses the existing weighted, temporally-decayed computation.

### Claude's Discretion
- Exact Spotify library/recently-played API endpoints and pagination strategy
- Whether to create a unified MusicServiceClient interface or keep separate Apple Music / Spotify fetch functions
- Cache table usage (existing song_metadata_cache vs new taste-specific cache)
- How to handle users with no listening data yet (empty profile response)
- Test strategy (mock API responses, test profile computation with fixture data)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing MCP Engine (source of truth for taste computation)
- `src/musicmind/engine/profile.py` — `build_taste_profile()` function. This is the algorithm to port.
- `src/musicmind/engine/similarity.py` — Genre vector cosine similarity utilities.
- `src/musicmind/client.py` — Apple Music API client patterns (library, recently-played, catalog endpoints).
- `src/musicmind/tools/taste.py` — MCP tool that orchestrates taste profile building. Shows the flow.

### Phase 1-3 Outputs
- `backend/src/musicmind/db/schema.py` — All data tables (song_metadata_cache, listening_history, taste_profile_snapshots, etc.) with user_id + service_source columns
- `backend/src/musicmind/api/services/service.py` — Service connection helpers including token decryption
- `backend/src/musicmind/config.py` — Settings with Spotify/Apple Music credentials
- `backend/src/musicmind/auth/dependencies.py` — get_current_user dependency

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/musicmind/engine/profile.py` — Complete taste profile algorithm (genre vectors, artist affinity, release year distribution, audio trait preferences). Port this.
- `src/musicmind/client.py` — Apple Music httpx client with 25+ endpoints, pagination, error handling. Reference for Spotify client.
- `backend/src/musicmind/api/services/service.py` — Pattern for service-specific operations with encrypted token decryption.

### Established Patterns
- FastAPI router + SQLAlchemy Core + Pydantic schemas
- Async httpx for external API calls
- User-scoped data via get_current_user dependency
- Encrypted token retrieval via EncryptionService

</code_context>

<specifics>
## Specific Ideas

No specific requirements — auto-mode selected standard patterns. Key insight: the existing MCP engine already computes taste profiles. This phase is primarily about porting that logic behind a web API with multi-user support.

</specifics>

<deferred>
## Deferred Ideas

- TAST-05 (unified multi-service profile) → Phase 8
- Taste evolution timeline → Phase 10 / v2

</deferred>

---

*Phase: 05-taste-profile-dashboard*
*Context gathered: 2026-03-27 via auto-mode*
