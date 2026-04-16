# Pipeline Rebuild — Zero External APIs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Remove all external API dependencies (Deezer, Last.fm, MusicBrainz, ReccoBeats, SoundStat) from the enrichment pipeline and rebuild scoring around Essentia + Modal GPU embeddings.

**Architecture:** 2-phase local enrichment (Essentia CPU → Modal GPU) replaces the 6-phase external API pipeline. Scoring shifts from 6 weighted heuristic dimensions to embedding cosine similarity (CLAP 512-dim + MERT 768-dim + EffNet 1280-dim) as primary signal, with genre metadata and scalar features as secondary.

**Tech Stack:** Essentia (local CPU), Modal (serverless GPU for CLAP/MERT), OpenAI (optional captions), SQLAlchemy, numpy

---

## Task 1: Rewrite Orchestrator — Strip External APIs

**Files:**
- Rewrite: `backend/src/musicmind/engine/enrichment/orchestrator.py`

Remove ALL external API calls (Deezer, ReccoBeats, SoundStat, Last.fm, MusicBrainz).
New 2-phase pipeline:
- Phase 1 (CPU): Essentia → 7 scalar features + 1280-dim EffNet embedding + classifier heads (mood/genre/acousticness)
- Phase 2 (GPU): Modal → 512-dim CLAP + 768-dim MERT embeddings

Preview URLs come from Apple Music/Spotify natively (already in song_metadata_cache.preview_url).
No Deezer search needed.

## Task 2: Delete Dead External API Modules

**Files to delete:**
- `backend/src/musicmind/engine/enrichment/deezer.py`
- `backend/src/musicmind/engine/enrichment/soundstat.py`
- `backend/src/musicmind/engine/enrichment/musicbrainz.py`
- `backend/src/musicmind/engine/enrichment/musicbrainz_credits.py`
- `backend/src/musicmind/engine/enrichment/acousticbrainz.py`
- `backend/src/musicmind/engine/enrichment/lastfm.py`

Clean up ALL imports of these modules across the entire codebase.

## Task 3: Rebuild Scoring Engine

**Files:**
- Rewrite: `backend/src/musicmind/engine/scorer.py`
- Rewrite: `backend/src/musicmind/engine/similarity.py`
- Modify: `backend/src/musicmind/engine/profile.py`
- Modify: `backend/src/musicmind/engine/weights.py`

New scoring dimensions:
| Signal | Weight | Source |
|--------|--------|--------|
| CLAP cosine (512-dim) | 0.30 | Modal GPU |
| MERT cosine (768-dim) | 0.25 | Modal GPU |
| EffNet cosine (1280-dim) | 0.15 | Essentia local |
| Genre match (cosine) | 0.15 | Service metadata |
| Scalar audio match | 0.10 | Essentia |
| Artist affinity | 0.05 | Library + calibration |

## Task 4: Update Worker & Indexer

**Files:**
- Modify: `backend/src/musicmind/worker.py`
- Modify: `backend/src/musicmind/indexer.py`

Remove MusicBrainz credit loop from worker. Remove Last.fm backfill from indexer.
Pass modal_endpoint_url through all enrich_tracks calls.

## Task 5: Rebuild Admin Dashboard

**Files:**
- Rewrite: `backend/src/musicmind/api/admin/router.py`
- Rewrite: `backend/src/musicmind/api/admin/diagnostics.py`
- Rewrite: `backend/src/musicmind/api/admin/progress.py`

New pipeline stages displayed:
1. Audio features (Essentia scalar)
2. Audio embeddings (EffNet 1280-dim)
3. GPU embeddings (CLAP + MERT via Modal)
4. AI captions (OpenAI, optional)
5. Essentia classifier labels (mood/genre/acousticness)
