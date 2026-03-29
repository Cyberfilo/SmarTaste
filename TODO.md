# MusicMind Improvement Sprint — Task Board

## Phase 1: Critical Bug Fixes 🔴 ✅
- [x] 1.1 Fix artist_cache PK — add user_id to primary key (currently artist_id only, two users overwrite each other)
- [x] 1.2 Fix audio_features_cache and sound_classification_cache PKs — same user_id missing from composite key
- [x] 1.3 Fix auth transaction safety — signup has 2 separate DB transactions, login has 2, refresh has 4. Wrap each in a single atomic transaction
- [x] 1.4 Fix SSE parser edge case — sse.ts drops events with empty data fields (currentData falsy check on line 68)
- [x] 1.5 Make CORS configurable — replace hardcoded live.menghi.dev with env var (app.py:41-45)

## Phase 2: Performance Fixes 🟠 ✅
- [x] 2.1 Fix rank_candidates O(n²×m) — precompute scores, only recompute MMR penalty per iteration. Add benchmark test (500 candidates, count=20, <500ms)
- [x] 2.2 Fix _compute_staleness linear scan — convert recent_recommendations to a set for O(1) lookup
- [x] 2.3 Fix weights.py optimizer — current "coordinate descent" uses arbitrary linear multiplier, not real optimization. Replace with actual coordinate descent (perturb weights, re-score, keep improvement) or Bayesian update

## Phase 3: Type Safety 🟡 ✅
- [x] 3.1 Create engine/models.py — Candidate, ScoredCandidate, UserProfile, ScoringWeights dataclasses/Pydantic models
- [x] 3.2 Refactor scorer.py, profile.py, similarity.py to use typed models instead of dict[str, Any]
- [x] 3.3 Run full test suite, fix any regressions

## Phase 4: Infrastructure Hardening 🟡 ✅
- [x] 4.1 Add rate limiting (slowapi) — signup/login 5/min per IP, recommendations 30/min per user, chat 20/min per user
- [x] 4.2 Background tasks — move Spotify token refresh, taste profile rebuild, library sync to FastAPI BackgroundTasks. Return 202 + task ID, add profile_status field
- [x] 4.3 Normalize chat storage — create chat_messages table (id, conversation_id FK, role, content, tool_calls, created_at), migrate JSON blobs, keep backward compat

## Phase 5: Essentia Audio Pipeline 🔵
- [ ] 5.1 Build engine/audio/ package — extractor.py (download Apple Music 30s preview → Essentia analysis), cache.py (ISRC-keyed storage), models.py
- [ ] 5.2 Extract per track: TempoCNN (BPM), danceability (0-1), arousal/valence (two floats), key+scale, loudness (LUFS), Discogs-EffNet embeddings (128-dim)
- [ ] 5.3 Replace Spotify audio features in similarity.py — cosine similarity on 128-dim embeddings primary, scalar fallback
- [ ] 5.4 Tests: unit test with CC0 audio fixture, integration test with mocked preview URL

## Phase 6: Sequential Session Model 🔵
- [ ] 6.1 Create engine/session.py — rolling context vector (exponentially-weighted average of last 20 song embeddings, α=0.85)
- [ ] 6.2 Add session_similarity scoring dimension (cosine sim between candidate embedding and context vector, weight ~0.10)
- [ ] 6.3 Session persistence — in-memory with 2hr TTL, POST /session/played endpoint
- [ ] 6.4 Tests: verify context vector math, verify empty session = no influence

## Phase 7: Music Knowledge Graph 🟣
- [ ] 7.1 Create engine/knowledge_graph/ — ingest.py fetching MusicBrainz relationships + Wikidata genres
- [ ] 7.2 Build graph: nodes (Artist, Genre, Label, Track), edges (collaborated_with, influenced_by, member_of, signed_to, subgenre_of, sampled)
- [ ] 7.3 Node2Vec embeddings (128-dim, walk_length=80, num_walks=10, p=1, q=0.5)
- [ ] 7.4 Integrate: replace binary genre cosine in genres.py with embedding distance, enhance artist_affinity with graph proximity

## Phase 8: Contextual Bandit 🟣
- [ ] 8.1 Create engine/bandit.py — Thompson Sampling with Beta(α,β) per context
- [ ] 8.2 Context features: time_of_day (sin/cos), day_of_week (one-hot), session_length
- [ ] 8.3 Replace fixed diversity weight (0.08) with bandit-sampled exploration parameter
- [ ] 8.4 Log sampled weight + outcome for training data

## Phase 9: CLAP Mood Embeddings 🟣
- [ ] 9.1 Add CLAP model (msclap or laion/larger_clap_general) — compute 512-dim audio embeddings alongside Essentia pipeline
- [ ] 9.2 Replace categorical mood matching in mood.py with text-to-audio cosine similarity
- [ ] 9.3 Enable natural language mood queries ("upbeat workout energy", "rainy Sunday chill")

## Phase 10: Last.fm Tag Enrichment 🟣
- [ ] 10.1 Create engine/lastfm.py — fetch+cache top tags per track/artist (5 req/sec rate limit)
- [ ] 10.2 Integrate as secondary genre signal: 70% embedding similarity + 30% Last.fm Jaccard tag overlap

## Completed
- Phase 1: Critical bug fixes (composite PKs, auth transactions, SSE parser, CORS) — af14182
- Phase 2: Performance (scorer O(n²)→O(n), staleness O(1), weight optimizer) — 4fa3fbc
- Phase 3: Typed engine models (Candidate, ScoredCandidate, UserProfile, etc.) — dba3ab2
- Phase 4: Infrastructure (rate limiting, background tasks, chat normalization) — b85553b

## Blocked
[empty]
