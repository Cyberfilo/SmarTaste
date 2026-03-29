# MusicMind Improvement Sprint — Implementation Plan

## Branch: claude/musicmind-sprint-setup-hbH3p
## Started: 2026-03-29

## Dependency Order
Phases 1-4 are independent fixes (bugs → perf → types → infra).
Phase 5 (Essentia) must complete before Phase 6 (session model needs embeddings) and Phase 9 (CLAP extends the audio pipeline).
Phase 7 (knowledge graph) is independent of Phase 5 but must complete before its scorer integration.
Phase 8 (bandit) requires Phase 3 (typed weights model) but is otherwise independent.
Phase 10 (Last.fm) is independent and can slot in anywhere.

## Deployment
- Frontend: Next.js on Vercel (auto-detected, no vercel.json needed for framework)
- Backend: Docker Compose (FastAPI + PostgreSQL 16) — see docker-compose.yml
- Environment: .env.example documents all required vars

## Codebase Findings (from full read)

### Confirmed Bugs
1. **artist_cache PK** (schema.py:179) — `artist_id` is sole PK, `user_id` is just a column. Two users caching artist "12345" collide. Same for `audio_features_cache` (line 253) and `sound_classification_cache` (line 278).
2. **Auth transactions** (auth/router.py) — signup: 2 transactions (user insert, then token insert). login: 2 (user fetch, token insert). refresh: 4 (check token, revoke, fetch user, insert new). Race conditions on refresh.
3. **SSE parser** (sse.ts:68) — `currentEvent && currentData` uses falsy check; empty string data is dropped.
4. **CORS hardcoded** (app.py:41-45) — three origins hardcoded, no env var.
5. **rank_candidates O(n²)** (scorer.py:231-273) — greedy MMR loop rescores ALL remaining candidates each iteration.
6. **_compute_staleness linear scan** (scorer.py:59-90) — iterates list to find catalog_id match.
7. **weights.py optimizer** (line 92-95) — `ratio * 0.3 + 0.7` linear approximation doesn't reflect actual score function.

### Architecture Notes
- Engine uses `dict[str, Any]` throughout — no typed models for candidates, profiles, or scores
- 7 scoring dimensions with DEFAULT_WEIGHTS summing to 1.0 (genre=0.35, audio=0.20, novelty=0.12, freshness=0.10, diversity=0.08, artist=0.08, staleness=0.07)
- Chat stores messages as JSON blob in chat_conversations.messages column
- Dedup pipeline: ISRC match (O(n)) → fuzzy match (O(m²) on remainder)
- Profile builder: temporal decay, regional genre prioritization, Shannon entropy familiarity

## Phase Progress

### Phase 1 — Critical Bug Fixes ✅ (af14182)
- Fixed composite PKs for artist_cache, audio_features_cache, sound_classification_cache (migration 006)
- Merged auth transactions into atomic blocks (signup: 2→1, login: 2→1, refresh: 4→1)
- Fixed SSE parser falsy check → boolean flag
- Made CORS configurable via MUSICMIND_CORS_ORIGINS env var

### Phase 2 — Performance ✅ (4fa3fbc)
- Rewrote rank_candidates: precompute base scores, greedy MMR only recomputes diversity penalty
- Built staleness index for O(1) lookup instead of O(n) scan
- Rewrote weight optimizer with real coordinate descent using stored breakdowns + early stopping

### Phase 3 — Type Safety ✅ (dba3ab2)
- Created engine/models.py: Candidate, ScoreBreakdown, ScoredCandidate, UserProfile, ScoringWeights, AudioFeatures
- Added 12 model tests + 12 performance tests
- Typed models work alongside existing dict interface (from_dict/to_dict roundtrip)

### Phase 4 — Infrastructure ✅ (b85553b)
- Added slowapi rate limiting: auth 5/min, chat 20/min, recommendations 30/min
- Added POST /api/taste/profile/refresh (202) + GET /api/taste/profile/status for background rebuilds
- Created chat_messages table (migration 007) with normalized writes + JSON blob backward compat
- 287 tests passing (28 pre-existing uuid7 failures excluded — Python 3.11 issue)

## Decisions Log
- **chat_messages not in DATA_TABLE_NAMES**: chat_messages has conversation_id FK (not user_id directly), so excluded from test_user_id_on_all_data_tables
- **Rate limit key**: Uses authenticated user_id when available, falls back to IP for unauthenticated endpoints
- **Background rebuild status**: In-memory dict (not DB) — acceptable for single-instance deployment targeting small friend group
