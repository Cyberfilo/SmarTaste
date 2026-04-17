# SmarTaste

A music-discovery webapp that connects users' Spotify + Apple Music libraries, builds a taste profile from real listening data, and delivers recommendations scored on 6 embedding-based dimensions. Users BYOK an Anthropic key for the in-app Claude chat. Designed for a small friends group, not a public product.

**Live:** [music.menghi.dev](https://music.menghi.dev) · **Staging:** `musicmind-staging.up.railway.app` · **Admin dashboard:** `/admin` on the frontend (or the legacy `admin.music.menghi.dev` proxy, being deprecated).

---

## Core flow (how a request travels end-to-end)

**User requests recommendations:**
```
Browser (Next.js)  ──GET /api/recommendations──▶  FastAPI backend
                                                    │
                                 ┌──────────────────┼──────────────────┐
                                 ▼                  ▼                  ▼
                          TasteService       _load_candidates_   audio_embeddings
                          (reads snapshot)    from_db (joins      (CLAP/MERT/EffNet
                                              recommendation_     per-user + global
                                              candidates with      ISRC fallback)
                                              global_song_cache)
                                 │                  │                  │
                                 └────────┬─────────┴────────┬─────────┘
                                          ▼                  ▼
                                     score_candidate (6 dims) + rank_candidates (MMR)
                                          │
                                          ▼
                                    JSON response (20 items + explanation + artwork)
```

**Worker cycle** (runs every ~60s on Railway):
```
Startup: GPU backfill → ISRC → audio download → Essentia → EffNet
Loop:    ISRC → audio → Essentia → EffNet → GPU → cobweb + discovery → library-sync
```

**Backend startup orchestration** (v6.322): on boot, the FastAPI lifespan inspects DB gaps and dispatches worker functions as background asyncio tasks (library enrichment → ISRC → GPU backfill → cobweb). Backend and worker share the same Python package + Postgres, so duplicate work is serialized by DB transactions, no HTTP between services.

**User connects a service (indexer path):** `POST /api/services/apple-music/connect` → `indexer.run_indexing` fires (per-user, 6 steps: library enrichment → top-3 discographies at 100%/70%/50% → rest above `AFFINITY_INCLUDE_THRESHOLD=0.05`). Discography tracks write to `global_song_cache`, not per-user tables.

---

## File inventory

### `backend/src/musicmind/`
| Path | Purpose |
|---|---|
| `app.py` | FastAPI app factory + lifespan (startup orchestration) |
| `worker.py` | Long-running polling loop (startup phases + main loop) |
| `indexer.py` | Per-user on-connect enrichment, `_rank_artists_by_affinity`, `compute_depth_fraction` |
| `config.py` | Pydantic `Settings` with `GPU_MODE` switch (CLOUD/LOCAL) |
| `api/router.py` | Aggregates 13 domain routers (auth, admin, chat, recommendations, taste, calibration, services, claude, openai, search, stats, tracks, playlists, session) |
| `api/recommendations/service.py` | `RecommendationService.get_recommendations` (DB-read path with cold-start fallback), `get_scoring_breakdown` (6 dims + 6 modifiers) |
| `api/recommendations/fetch.py` | The 4 discovery strategies (`discover_similar_artists`, `_genre_adjacent`, `_editorial`, `_chart_filter`), `allocate_seed_budget`, `_genre_overlap_with_expansion` |
| `api/admin/router.py` | Admin endpoints incl. new `/songs-table` and `/artists-table` for dashboard |
| `api/taste/service.py` | `TasteService.get_profile(force_refresh)` — fetches library, builds snapshot |
| `engine/scorer.py` | `score_candidate` (6 dims + bonuses) + `rank_candidates` (MMR) |
| `engine/profile.py` | `build_artist_affinity` (log1p saturation), `build_genre_vector`, `parse_artists` |
| `engine/weights.py` | `DEFAULT_WEIGHTS` + `compute_context_weights` (adaptive redistribution) |
| `engine/cobweb.py` | Shared cobweb ranking (sum+log1p+primary-affinity) + `prefilter_by_centroid_similarity` + `EFFNET_EMBEDDING_DIM` |
| `engine/enrichment/orchestrator.py` | Two-phase pipeline: Essentia → GPU, with min-batch deferral |
| `engine/enrichment/gpu_client.py` | `enrich_bytes_concurrent` / `enrich_urls_concurrent` (25/batch × 3 concurrent), `GPU_MIN_BATCH=3` |
| `engine/enrichment/isrc_lookup.py` | Deezer + MusicBrainz fallback |
| `engine/audio/essentia_extractor.py` | 11 scalar features + EffNet ONNX |
| `db/schema.py` | SQLAlchemy Core — 23 tables |
| `db/engine.py` | Async engine factory |
| `alembic/versions/` | 25 migrations, latest `025_add_artwork_url_global.py` |
| `auth/router.py` | signup/login/refresh (atomic transactions since v6.200) |
| `security/encryption.py` | Fernet for BYOK + service tokens |

### `frontend/src/`
| Path | Purpose |
|---|---|
| `app/(auth)/login`, `/signup` | auth pages |
| `app/(app)/dashboard/recommendations/page.tsx` | main recommendations feed |
| `app/(app)/dashboard/taste/page.tsx` | taste profile view |
| `app/(app)/chat/page.tsx` | Claude BYOK chat (SSE) |
| `app/(app)/onboarding/page.tsx` | 3-step calibration wizard |
| `app/(app)/admin/page.tsx` | admin dashboard (queries `/api/admin/*`) |
| `components/recommendations/recommendation-feed.tsx` | feed; strategy/mood selectors removed in v6.320 |
| `components/recommendations/score-breakdown.tsx` | renders 6 weighted dims + 6 modifiers |
| `components/recommendations/recommendation-card.tsx` | reads `item.artwork_url` |
| `components/onboarding/calibration-wizard.tsx` | 3-step playlist/artist/song picker |
| `hooks/use-recommendations.ts` | TanStack Query; no strategy/mood params post-v6.320 |
| `lib/api.ts` | `apiFetch` with CSRF + 401-refresh |
| `lib/sse.ts` | custom SSE parser for chat stream |
| `types/api.ts` | shared response shapes |

### Supporting services
| Path | Purpose |
|---|---|
| `worker/Dockerfile` | Railway worker image — shares backend code, entry `python -m musicmind.worker` |
| `backend/Dockerfile` | Railway backend image — entry `uvicorn musicmind.app:app` |
| `gpu-worker/handler.py` | Modal serverless (A100) — CLAP HTSAT-tiny + MERT-95M |
| `gpu-worker-local/server.py` | FastAPI on Mac MPS, same API shape as Modal; exposed via ngrok when `GPU_MODE=LOCAL` |
| `admin/` | Legacy standalone Railway proxy, being deprecated in favour of `frontend/admin` |
| `scripts/reset-staging-db.sql` | Guarded staging DB reset |
| `.github/workflows/deploy-modal.yml` | Auto-deploy Modal on `gpu-worker/` changes |

---

## File relationships (change-one-affects-another)

- `scorer.py` ↔ `weights.py` ↔ `similarity.py` — scoring engine triangle
- `scorer.py` ↔ `profile.py` — profile provides centroids consumed by scorer
- `indexer.py` ↔ `worker.py` — both call into `engine/cobweb.py` + `engine/enrichment/orchestrator.py`. Return types (e.g. `_get_ranked_artists` → `list[tuple[str, float]]`) must stay aligned across both
- `worker.py:_unlink_excess_discoveries` ↔ `indexer.py:AFFINITY_INCLUDE_THRESHOLD` — cleanup uses indexer's threshold so it never deletes what indexer just enriched
- `orchestrator.py` ↔ `gpu_client.py` ↔ `gpu-worker/handler.py` / `gpu-worker-local/server.py` — keep response-key contract (`clap_512` / `mert_768`) consistent; a past bug had `audio_embeddings_global` reading `clap_embedding` instead
- `db/schema.py` ↔ `alembic/versions/NNN_*.py` — every schema change needs a migration; add table names to `backend/tests/test_schema.py::ALL_TABLE_NAMES` + update the count
- `worker/Dockerfile` ↔ `backend/Dockerfile` — keep Python version + system deps identical (both need Python 3.12 + ffmpeg + Essentia)
- `api/recommendations/service.py:_load_candidates_from_db` ↔ `global_song_cache` schema — adding a column means updating the SELECT and the result-dict mapping
- `frontend/src/types/api.ts` ↔ backend response shapes — types are hand-maintained; if backend returns a new field, update the TS interface

---

## Hosting & deployment

| Service | Platform | Entry point | Branch → env |
|---|---|---|---|
| Frontend | Vercel | `next build` | `main` → prod (music.menghi.dev), `staging` → preview |
| Backend API | Railway | `uvicorn musicmind.app:app --port $PORT` | `main` → prod, `staging` → staging |
| Worker | Railway (separate service) | `python -m musicmind.worker` | same branches |
| Main DB | Railway Postgres | — | shared between backend + worker |
| Logs DB | Railway Postgres (optional) | — | enrichment/error log stream |
| GPU (cloud) | Modal | `modal deploy gpu-worker/handler.py` via GitHub Actions on push to `gpu-worker/**` | always targets `smartaste-gpu-worker` |
| GPU (local) | Mac + ngrok | `python gpu-worker-local/server.py` | pair with `GPU_MODE=LOCAL` + `MUSICMIND_LOCAL_GPU_ENDPOINT_URL=<ngrok https url>` |
| Legacy admin | Railway | `admin/app.py` | deprecated; prefer `frontend/(app)/admin` |

**Deploy command**: `git push origin staging` (Railway watches the branch, rebuilds both services, runs alembic automatically via backend startup). `git push origin main` for production.

**Environment variables** (all live in Railway dashboard, staging copy in `.staging-debug.md` gitignored at repo root):
- `MUSICMIND_DATABASE_URL` — Postgres. Also reads `DATABASE_URL` (Railway convention).
- `MUSICMIND_FERNET_KEY` — encrypts service OAuth tokens + BYOK API keys.
- `MUSICMIND_JWT_SECRET_KEY` — session tokens.
- `MUSICMIND_APPLE_TEAM_ID`, `_KEY_ID`, `_PRIVATE_KEY_B64` — MusicKit.
- `MUSICMIND_SPOTIFY_CLIENT_ID`, `_CLIENT_SECRET`, `_REDIRECT_URI`.
- `MUSICMIND_GPU_MODE` = `CLOUD` | `LOCAL` (when LOCAL, `modal_endpoint_url` is swapped with `local_gpu_endpoint_url` in `config.py:model_post_init`).
- `MUSICMIND_MODAL_ENDPOINT_URL`, `MUSICMIND_LOCAL_GPU_ENDPOINT_URL`.
- `MUSICMIND_LOGS_DATABASE_URL` — optional.
- `MUSICMIND_ADMIN_SECRET` — header `x-admin-secret` on `/api/admin/*`.
- `MUSICMIND_OPENAI_API_KEY` — optional, AI captions.
- `MUSICMIND_FRONTEND_URL` — OAuth redirect origin.
- `NEXT_PUBLIC_API_URL` (frontend, on Vercel) — backend URL.
- `MUSICMIND_STAGING=true` + `MUSICMIND_CONFIRM_RESET=yes` → auto-reset DB on boot (staging-only safety).

---

## Scoring architecture (6 dimensions + modifiers)

| Dimension | Default weight | Source |
|---|---|---|
| CLAP cosine (512) | 0.30 | Modal/local GPU — semantic audio-text |
| MERT cosine (768) | 0.25 | Modal/local GPU — musical structure |
| EffNet cosine (1280) | 0.15 | Essentia ONNX — timbral fingerprint |
| Genre cosine | 0.15 | Apple/Spotify metadata, regional-prioritized |
| Scalar audio | 0.10 | Essentia tempo/energy/danceability euclidean |
| Artist affinity | 0.05 | Library presence (log1p) + calibration |

**Modifiers** (additive after weighted sum):
- `discovery_bonus` (+0…0.04) — from `_discovery_weight` set in `discover_similar_artists`
- `cross_strategy_bonus` (+0…0.10) — strategies that surfaced this track
- `calibration_boost` (+0…0.20) — for artists in `user_calibration` (zeroed if genre_score < 0.15)
- `mood_boost` (+0…0.10) — when mood filter active (no-op post-v6.320)
- `diversity_penalty` (−0…0.05) — MMR, recomputed per greedy selection step
- `staleness` (−0…0.03) — recently recommended (indexed for O(1) lookup)

**Adaptive weights** (`compute_context_weights`): weights shift when embeddings missing (redistribute to genre + scalar), when calibration exists (artist +0.05), when mood active (CLAP dominates). With ≥10 feedback ratings, blends 60% context + 40% feedback-optimized.

**Regional genre prioritization**: original tag ("Italian Hip-Hop/Rap") gets weight 1.0, expanded parents ("Hip-Hop/Rap") get 0.3. Prevents a heavy Italian listener being shown American drill that happens to share the parent genre.

**Artist-in-wrong-genre penalty**: known artist with genre_score < 0.2 has `artist_match` capped at 30%. Calibration boost zeroed if genre_score < 0.15.

---

## Enrichment pipeline (post-April 2026 rebuild)

All external feature APIs (ReccoBeats, SoundStat, Last.fm, AcousticBrainz) removed. Per-track flow:

1. **Preview URL**: from Apple/Spotify metadata; Deezer ISRC fallback if missing.
2. **Essentia** (CPU on Railway worker): 11 scalar features + 1280-dim EffNet ONNX embedding + classifier heads (mood/voice/acoustic).
3. **GPU** (Modal A100 serverless or local Mac MPS): 512-dim CLAP + 768-dim MERT. Batched 25 at a time, up to 3 concurrent. Defers if < 3 pending (`GPU_MIN_BATCH`).
4. **OpenAI captions** (optional): AI-generated track description from Essentia tags.

**Min-batch deferral** (v6.321): orchestrator Phase 2 skips GPU if <3 items pending; worker's periodic backfill picks them up with more items later. Prevents the "1 item processed" log spam.

**ISRC backfill retry-loop fix** (v6.311): tracks where Deezer + MusicBrainz both miss get `isrc='__NO_ISRC__'` sentinel; existing `IS NULL OR = ''` queries naturally skip them.

---

## Onboarding taste calibration

3-step wizard shown after service connection (compensates for Apple Music's lack of play counts):
1. **Playlists** (max 5, weight 5x) — picked from user's library.
2. **Artists** (hierarchical drag-to-reorder, top-3 weight 5→1x descending) — top 3 trigger background full-discography enrichment.
3. **Songs** (weight 3x) — favorites from the selected playlists' combined tracks.

Weights applied as song duplication in profile-builder input. Frontend: `CalibrationWizard` + `CalibrationManager` (settings). DB: `user_calibration(user_id, calibration_type, item_id, item_name, weight)`. Types: `top_artist`, `artist_rank`, `playlist`, `playlist_song`.

---

## Active DB tables (23 total, latest migration 025)

**Per-user** (user_id in PK or FK):
`users`, `user_api_keys`, `service_connections`, `refresh_tokens`, `song_metadata_cache`, `artist_cache`, `taste_profile_snapshots`, `audio_features_cache`, `sound_classification_cache`, `chat_conversations`, `chat_messages`, `audio_embeddings`, `generated_playlists`, `user_calibration`, `user_indexing_status`, `artist_cobweb`.

**Global / shared** (ISRC or catalog_id keyed):
`audio_features_global`, `audio_embeddings_global`, `global_song_cache` (+ artwork_url since mig 025), `playlist_items`, `preview_audio_cache`, `recommendation_candidates` (worker-populated per-user discovery pool), `worker_status`.

---

## Code conventions

- Python 3.12 (Essentia wheel constraint), `from __future__ import annotations` everywhere.
- Ruff, line-length 100, rules `E, F, I, N, W, UP`.
- Modern generics (`dict[str, Any]`, `str | None`).
- Async everywhere for I/O (httpx, asyncpg/SQLAlchemy async, orchestrator semaphores).
- Logging via stderr. The `musicmind` logger is forced to INFO in `app.py` (uvicorn doesn't configure it otherwise — without this, startup orchestration + worker logs disappear on Railway).
- Keyword-only arguments after `*` for optional scoring config.
- Return dicts (not dataclasses) from engine functions so the `_score`/`_breakdown`/`_explanation` augmentation pattern works.
- SQLAlchemy Core only (no ORM). JSON columns for arrays/dicts.
- Tests are pure: `_rank_artists_by_affinity`, `rank_cobweb_candidates`, `prefilter_by_centroid_similarity`, `allocate_seed_budget`, etc. are extracted pure helpers so they unit-test without DB fixtures.
- Frontend: Next.js 16 App Router; Tailwind 4; shadcn/ui on top of Base UI; Zustand + TanStack Query. Types in `src/types/api.ts` hand-maintained.

---

## Quick commands

```bash
# Backend (local)
cd backend && uv run uvicorn musicmind.app:app --reload

# Worker (local)
cd backend && uv run python -m musicmind.worker

# Frontend (local)
cd frontend && npm run dev

# Tests (full suite, deselect known flaky auth tests)
cd backend && uv run pytest tests/ \
  --deselect tests/test_auth.py::test_csrf_protection \
  --deselect tests/test_auth.py::test_csrf_with_valid_token \
  --deselect tests/test_auth.py::test_me_returns_user_info \
  --deselect tests/test_encryption.py::test_decrypt_wrong_key_raises -q

# Lint
cd backend && uv run ruff check src/ tests/

# Staging DB (creds in .staging-debug.md, gitignored)
psql "$STAGING_DATABASE_URL"

# Trigger admin actions
curl -X POST -H "x-admin-secret: $ADMIN_SECRET" \
  https://musicmind-staging.up.railway.app/api/admin/rebuild-taste-profiles

curl -H "x-admin-secret: $ADMIN_SECRET" \
  "https://musicmind-staging.up.railway.app/api/admin/songs-table?limit=20"

# Local GPU (Mac)
cd gpu-worker-local && uv run python server.py
# then in another terminal: ngrok http 8765
# then set MUSICMIND_GPU_MODE=LOCAL + MUSICMIND_LOCAL_GPU_ENDPOINT_URL=<ngrok https> on Railway
```

---

## Workflow

- Branch: `staging` is the working branch; push-to-deploy on staging Railway env. Merge to `main` when ready to ship to production. Never create ad-hoc feature branches.
- Every change: commit + push + CHANGELOG entry + VERSION bump + README badge.
- Versioning: `V X.YZA` — A = bugfix, Z = minor refinement, Y = small logic change, X = major.
- For file-changing work, start through a GSD command (`/gsd:quick`, `/gsd:debug`, `/gsd:execute-phase`) when one applies. Bypass only when the user asks directly.

---

## Bugs history (reference for future regressions)

Apr 2026 pipeline-rebuild sprint fixes — if a similar pattern shows up, check the fix first:

- `uuid.uuid7` is 3.14+ only → `_uuid7 = getattr(uuid, "uuid7", uuid.uuid4)` shim.
- Essentia wheels cap at Python 3.12 → `backend/Dockerfile` + `worker/Dockerfile` pin 3.12.
- Worker/Backend Dockerfile drift → both need identical system deps, different entry points only.
- EffNet ONNX input shape → rank 3 `[batch, time, mel]`, not rank 4.
- EffNet patch size → 128 frames, not full spectrogram (~1875 frames for 30s).
- `beats_confidence` polymorphism → scalar float in 2.1b6; use `hasattr(bc, "mean")`.
- `_merge_features` must overwrite stale `reccobeats`/`soundstat` sources, not just fill NULLs.
- Embedding JSON via `sa.text()` silently fails → use SQLAlchemy Core with column types.
- Stale `user_indexing_status` step < 7 blocks gap-fill forever → force-complete after 5 min.
- `__global__` user's cache deleted by orphan cleanup → create system user, skip in cleanup.
- Deezer preview URLs expire (~24h 403) → auto-refresh via fresh ISRC lookup + `preview_audio_cache` bytes.
- CLAP `HTSAT-base` vs `HTSAT-tiny` channel mismatch → use tiny (matches checkpoint).
- `torch.load` defaults `weights_only=True` in 2.6+ → monkey-patch to False for CLAP checkpoint.
- `_get_ranked_artists` return-type change (from `list[str]` to `list[tuple[str, float]]`) broke `_unlink_excess_discoveries` — callers of shared helpers need coordinated updates.
- `_backfill_gpu_embeddings_global` had swapped args (`enrich_batch_bytes_via_gpu(modal_url, b64_items)`) + wrong response keys (`clap_embedding` instead of `clap_512`) — both silently failed, leaving global CLAP/MERT at 0. Fixed in v6.321 via the concurrent helper.
- Uvicorn doesn't configure the `musicmind.*` logger → INFO messages dropped. v6.322 forces INFO + stderr handler in `app.py` module init.
