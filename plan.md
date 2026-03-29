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
[Claude updates this section after each phase]

## Decisions Log
[Record architectural decisions here with rationale]
