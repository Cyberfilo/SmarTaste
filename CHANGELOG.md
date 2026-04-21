# Changelog

All notable changes to SmarTaste are documented here.

Versioning: **V X.YZA** where X=major, Y=small logic, Z=minor, A=bugfix.
When A reaches 10 → Z+1 (A resets to 0). When Z reaches 10 → Y+1. Etc.

---

## V 6.396 — 2026-04-21

### Artist discography deepening — 100% library-artist catalog coverage via Deezer

Previously the worker fetched only the top ~50 tracks per library artist (bounded by Apple's top-songs 10-cap and the cobweb's `MAX_COBWEB_SONGS=50` budget). With 174 library artists that means at most 8,700 globally-cached tracks — but most Italian hip-hop artists have 80-150+ released tracks each, so the recommender was working with a partial catalog.

**New worker phase `artist_deepening`** runs after cobweb expansion (Phase 3, gated on `_count_global_gaps <= 50` so primary enrichment drains first). For up to 5 library artists per cycle:
1. Pick artists from `song_metadata_cache` whose normalized name isn't yet in `artist_discography_state`.
2. Call the new `fetch_artist_full_discography(artist_name, limit=500)` in `engine/enrichment/deezer.py` — walks `/search/artist` → `/artist/{id}/albums` → `/album/{id}/tracks` with no discography cap. Unauthenticated (Deezer-only), so it works globally regardless of user token state.
3. Upsert tracks into `global_song_cache` via `ON CONFLICT (catalog_id) DO UPDATE` — existing richer metadata is preserved, only NULL/empty columns get filled.
4. Record outcome in `artist_discography_state (artist_name_norm, artist_name, service_source='deezer', tracks_found, deepened_at, last_error)`. Errors mark the row "attempted" so we don't hot-loop retry.

Schema: new `artist_discography_state` table (migration 029). Fallback-primary choice: Deezer was picked over Apple's album-walk because (a) no auth state to manage, (b) no per-artist cap (Apple's album endpoint paginates but regional content can still be incomplete), (c) independent catalog means additional coverage vs Apple for the same artists.

**Overview query** (run any time to see progress):
```sql
SELECT
  ads.artist_name,
  ads.tracks_found AS found_by_deezer,
  ads.deepened_at,
  (SELECT count(*) FROM global_song_cache gsc
     WHERE LOWER(TRIM(gsc.artist_name)) = ads.artist_name_norm) AS in_global,
  (SELECT count(DISTINCT catalog_id) FROM song_metadata_cache smc
     WHERE LOWER(TRIM(smc.artist_name)) = ads.artist_name_norm) AS in_library
FROM artist_discography_state ads
ORDER BY ads.tracks_found DESC;
```

No breaking changes. New phase starts draining on the next worker cycle after migration 029 applies.

---

## V 6.395 — 2026-04-21

### Fix: GPU backfill was stalling indefinitely on orphan AEG rows + stalled cobweb gate

Prod diagnostic showed 184 tracks reported as "eligible for GPU backfill" but 167 were orphan `audio_embeddings_global` rows with no corresponding `global_song_cache` entry — the resolver silently skips them (no catalog_id → no URL, no cache). Actual actionable pool was 17 tracks, of which only 13 had cached bytes that `_get_cached_audio` would serve. Below `GPU_MIN_BATCH=15` indefinitely → GPU never fired. Zero global GPU embeddings stored in the last 4+ days despite 301 visible gaps.

**Three fixes:**

1. **`_backfill_gpu_embeddings_global` now JOINs `global_song_cache`** on `isrc` at query time. Orphan AEG rows are excluded at source so the `rows_both` / `rows_mert_only` counts match what the resolver can actually act on.

2. **`GPU_MIN_BATCH` dropped 15 → 8.** The 15-floor assumed URL freshness + un-polluted queries. Post-fix both assumptions hold, but 8 is still a well-amortised cold-start batch (A100 decode + CLAP + MERT) and unblocks the steady-state drain for a catalog this size. Revisit when the catalog grows past ~10 k tracks.

3. **Added main-loop global Essentia phase (5a)** — 284 GSC tracks had no AEG row at all and needed Essentia/EffNet before they could become GPU candidates. Previously `_enrich_global_songs` only ran during cobweb expansion (gated on `user_work_remaining == 0`) or the startup drain (which declared "permanent failures" after a single pass with stale URLs). Now it runs every cycle between library-GPU backfill (phase 5) and global-GPU backfill (phase 5b), so new discovery work isn't stranded.

**One-shot cleanup (executed inline on prod)**: `DELETE FROM audio_embeddings_global WHERE isrc NOT IN (SELECT isrc FROM global_song_cache ...)` removed 167 orphan rows. Post-deletion, AEG is 2,070 rows and `eligible_for_gpu_both` drops from 184 → 17 (the true number).

No schema change. No breaking change. Cobweb gate (`user_work_remaining > 0` blocks cobweb) left as-is for this patch — worth revisiting as a "softer gate" after observing whether the main-loop global-Essentia phase drains the backlog cleanly.

---

## V 6.394 — 2026-04-21

### Psychology research integration — Tier B: discovery disposition + taste shape + Wundt stretch

Three *personality-adaptive* layers that sit on top of the V 6.393 content-based scorer. Both scalars are derived from existing library + history data; no new enrichment or schema work.

**Discovery disposition (§7.6, §3.2)** — per-user scalar ∈ [0, 1] measuring the fraction of last-30-day library additions that introduce previously-unseen artists. Pure exploiters (every new add from an existing favorite) score ≈0; pure explorers (every new add is a new artist) score ≈1. Falls back to 0.30 when `date_added_to_library` is missing on too many rows to judge. Plumbed onto `profile["discovery_disposition"]`.

**Taste shape (§7.7)** — univore / mixed / omnivore classification built from a Gini coefficient over per-artist play counts (library weight 1×, history weight 2× per plays-are-stronger-than-adds). Thresholds calibrated so a user with ~80% of plays on their top-3 artists lands in `univore`; evenly spread across 20+ artists lands in `omnivore`. Profile carries `taste_shape: {shape, gini, top_artist_share}`.

**Wundt-curve stretch target (§9.6)** — the re-ranker now actively rewards candidates whose CLAP distance from the user centroid falls in a disposition-calibrated band. Stretch target = `0.15 + disposition × 0.35` (conservatives ≈0.15 near-centroid, explorers ≈0.50 firmly outside comfort zone). Gaussian bonus (σ=0.15, peak ±0.025) peaks on that target — candidates too similar OR too alien both lose points. Implements the research's "principled novelty" directive: novelty is a *specific distance*, not "as diverse as possible."

**Re-rank modulation by disposition + shape:**
- `diversity_weight` scales 0.7× (exploiter) → 1.5× (explorer) of base
- DPP kernel sigma widens 0.85× → 1.25× with disposition (explorers tolerate more-similar already-selected tracks)
- Steck calibration weight × 0.5 for univores (don't force genre breadth on a deep-niche listener), × 1.2 for omnivores (keep them spread), × 1.0 for mixed

Breakdown additions: `wundt_stretch` per track. No breaking changes — pre-6.394 profiles get sensible defaults (disposition=0.30, shape="mixed").

**Combined effect (Tier A + Tier B):** a 15-year-old Milanese user whose library is 60% halftime drill and concentrated on 4 artists (Shiva, Capo Plaza, Baby Gang, Artie 5ive) gets:
- Tempo-band dim rewarding 130-160 BPM candidates (fast_motor zone)
- Halftime bonus +0.02-0.03 on drill-signature candidates
- BRECVEMA explanation: "sounds like Parigi by Shiva (145 BPM in your driving zone, halftime feel you tend to favor); Baby Gang is in your top artists"
- Univore taste shape → softer genre-calibration penalty (doesn't force spread away from Italian hip-hop)
- Moderate disposition (0.4) → stretch target ≈0.29 — prefers candidates *slightly* outside the centroid, not hugging it

---

## V 6.393 — 2026-04-21

### Psychology research integration — Tier A: tempo bands + halftime feel + BRECVEMA explanations

Three content-based scorer upgrades drawn from the music-psychology literature review (see `/Users/filippomattiamenghi/Downloads/music-pshycology-research.md`). All use data we already compute — no new enrichment work.

**Tempo-band dimension (§2.1, §9.3 item 3)** — the research is unanimous that tempo is the single strongest predictor of arousal response, but that raw BPM euclidean is the wrong similarity function. Tempo groups psychologically into five zones anchored on bodily entrainment: `resting` (60-80), `walking` (80-110), `brisk` (110-130), `fast_motor` (130-160), `driven` (160+). New module `engine/tempo.py` computes a soft Gaussian-membership vector across these bands. Profiles now carry `tempo_band_distribution` (engagement-weighted histogram over the user's library); candidates are scored via `tempo_band_similarity` (dot-product match against the user's distribution). Added as a dedicated scorer dim (default weight 0.08) — `scalar` drops from 0.10 to 0.06 since tempo is no longer in it, and `clap` / `mert` each lose 0.02 to make room. Total still sums to 1.0.

**Halftime-feel detection (§2.1 + §2.2)** — drill and trap's kick-on-1 / snare-on-3 / double-time hi-hats on a 130-160 BPM grid create a body-pulse vs surface-activity dissociation the scorer previously couldn't see. `halftime_feel_score(tempo, beat_strength, danceability)` combines three signals that co-vary with the feel: tempo Gaussian peaked at 142 BPM, beat_strength > 0.35 (audible pulse), danceability in a 0.45-0.80 sweet spot (groove with suspended motion, not peak-dance). Profiles carry `halftime_ratio` (weighted fraction of library scoring ≥0.35). When a user's library is >25% halftime *and* a candidate reads halftime, an additive bonus up to +0.03 fires. Specifically unlocks Italian drill / trap / UK drill discrimination for listeners whose top plays live in that pocket.

**BRECVEMA-channel explanations (§8.2, §9.5)** — Juslin & Västfjäll's eight mechanisms collapse into three operational channels for a content-based scorer: `acoustic` (CLAP + MERT + EffNet + tempo_band + scalar), `scene` (artist + genre), `affective` (mood_match). The new `_render_brecvema_explanation` renderer computes each channel's weighted contribution and speaks about the top two that pull ≥5% of the final score. Language is concrete: "sounds like X by Y (142 BPM in your driving zone, halftime feel you tend to favor); Shiva is in your top artists". Replaces the prior threshold-based paste-up ("sounds like your taste; similar musical structure; genre match") with attribution that mirrors the scorer's actual decision.

Profile schema additions: `tempo_band_distribution` (dict), `halftime_ratio` (float). Scorer breakdown additions: `tempo_band_similarity`, `halftime_bonus`. No breaking changes — graceful fallback when fields are absent on pre-6.393 profiles.

---

## V 6.392 — 2026-04-21

### Logs DB: reset on deploy + skip 200 OK noise

Two changes to the separate PostgreSQL logging instance (`request_logs` / `enrichment_logs` / `error_logs`).

**Reset on every deploy** — `reset_logs_tables` in `db/logs.py` runs `TRUNCATE ... RESTART IDENTITY` on all three log tables right after schema init, before the batched writer starts. Every Railway container boot wipes prior deploy traces and restarts BigInt PK sequences at 1. Tolerant of missing tables (first deploy, partial schema). The main DB is untouched — only the logs DB resets. This replaces the never-realized intention of main-DB reset-on-startup, which the prior 400+ commits never actually executed on staging and which we're intentionally leaving off.

**Skip 200 OK request writes** — `RequestLoggingMiddleware._write_to_db` now gates on `status_code != 200`. Unchanged: 500s from unhandled exceptions still write (separate code path before the gate), 3xx redirects, 4xx client errors, 5xx server errors, 201/204 state-change successes all persist. Typical usage was producing ~40 200s per user per 5 min — now those rows stay out of the DB, making 4xx/5xx grep actually useful.

No schema change. No breaking change. Python-logging `DatabaseLogHandler` still gates at WARNING+ (unchanged).

---

## V 6.391 — 2026-04-21

### Proactive dead-link check + artwork backfill + global preview cache

Post-merge investigation of prod showed **1614/2480 (65%) cached Deezer preview URLs were expired** (`hdnea=exp=` tokens decoded to 2–4 days in the past). Enrichment had been effectively stalled for days — every cycle attempted audio downloads, hit 403s, and wasted the round-trip before reactively re-resolving one URL at a time. With `min_batch=15` the GPU never fired.

**Dead-link check (`_preview_url_is_fresh`)** in `engine/enrichment/orchestrator.py` parses the `exp=` token server-side with a 60 s clock-skew grace. URLs without the token (iTunes, etc.) are treated as stable. `_download_preview` short-circuits to `None` on stale input so callers refresh BEFORE the HTTP call instead of after the 403.

**Audio-bytes backfill** now runs in both `_download_all_previews` (library / `song_metadata_cache`) and a new `_download_all_previews_global` (discovery / `global_song_cache` — where the 1614 stale rows lived and were previously unreachable). Pre-flight freshness check → proactive Deezer re-resolve by ISRC/search → write fresh URL back to the source table → download + cache bytes. Still falls back to reactive refresh if a URL dies before its claimed expiry.

**Artwork backfill** is a new worker phase between ISRC backfill and audio-bytes backfill. New `artwork_cache` table (migration `028_add_artwork_cache`) stores image bytes keyed by `catalog_id`, shared between SMC and GSC. Apple `{w}x{h}bb.jpg` templates are resolved to 640×640 at fetch time; sizes clamped to 500 B–5 MB sanity range. No TTL cleanup needed (artwork URLs don't sign).

**New worker phase order** (per cycle + both startup drains):
1. ISRC backfill
2. **Artwork backfill + caching** (new)
3. **Audio-bytes backfill + URL refresh** (extended — now covers GSC)
4. Essentia → EffNet → GPU → mood (unchanged)

Schema touches: +`artwork_cache` table (5 cols). No breaking changes.

---

## V 6.390 — 2026-04-20

### Fix: "Similar to X" cited library tracks the user didn't remember

User reported the contextual explanation saying "Similar to *Fare chiasso* by Night Skinny" when they didn't think of that track as being in their library. DB check confirmed the track was in Apple Music's library scope (`library_id` set) but almost certainly auto-added — the user has 2 Night Skinny tracks total vs 31 Capo Plaza / 31 Shiva / 24 Artie 5ive / 19 Kero / 16 Vale Pain. Apple Music gives us no way to distinguish "explicitly added" from "added via album / pulled in via playlist sync", so the row passes the boolean library filter.

`_load_user_library_claps` now takes an optional `known_artist_names` set and filters library tracks to ones whose primary artist is in the user's top-30 by affinity. On staging, that cuts the reference pool from 466 songs across 174 artists to 291 songs across 30 artists (62% of library retained — the actual core taste; 175 long-tail one-offs dropped). Night Skinny with 2 library songs falls out of top-30 → Fare chiasso can no longer be cited.

Primary artist is extracted via the existing `parse_artists` helper so "Night Skinny feat. X, Y" matches correctly. No schema change; no perf impact (same query, filtered in Python before building the result dict).

---

## V 6.389 — 2026-04-20

### Hybrid mood classifier — discrete tags + sparse score vector

V 6.388 shipped the right V1: 1-3 discrete mood tags per track with positional weights (0.5 / 0.3 / 0.2) driving the `mood_match` cosine. But tag quantization throws away intensity — a song the model "felt" as `{reflective: 0.85, melancholic: 0.55}` and one it felt as `{reflective: 0.35, melancholic: 0.25}` both get collapsed to `["reflective", "melancholic"]` and score identically. That's exactly the precision gap for the Vale Pain example: two tracks with the same primary tag but very different intensity profiles.

**Upgrade to hybrid output** — one GPT-5.4 call returns both:

- `primary` — single enum tag (for UI display, admin debugging, backward compat)
- `moods` — complete 12-key score object, `0.0-1.0` per mood (drives the cosine)

JSON schema is strict: all 12 keys required, primary must match the highest score. System prompt includes a calibration guide ("0.2-0.3 = faint undertone, 0.7-0.8 = strong supporting mood, 0.9-1.0 = defining") and two concrete few-shot examples — a hype trap banger and a reflective rap track — so the model anchors to consistent cross-track calibration instead of binarizing.

**Schema changes** (migration 027):

- New column `global_song_cache.mood_scores JSONB` (sparse dict, only non-zero moods stored).
- `mood_tags` kept unchanged for UI / backward compat.

**Scoring upgrade:**

- `mood_similarity` now prefers `candidate_scores` (richer cosine) with `candidate_tags` as fallback for V 6.388-tagged rows not yet re-classified.
- `aggregate_mood_distribution` sums per-song score vectors for the user's library centroid when scores are available, positional tag weights otherwise.
- User's profile `mood_distribution` is now a true mood centroid in the 12-dim taxonomy space.

**Backfill:** worker re-classifies V 6.388 rows (`mood_scores IS NULL`) on the main cycle + startup drain, prioritising them ahead of never-tagged rows. One more pass over the catalog (~$1-2 at GPT-5.4 pricing).

---

## V 6.388 — 2026-04-20

### Mood classifier + new `mood_match` scoring dimension (GPT-5.4)

Closes the affect/valence gap: CLAP/MERT/EffNet capture timbre and production style well, but a reflective Italian rap track ("Estate in città" by Vale Pain) was being scored as a neighbor of a hype rap banger ("Shotta 2") because both share instrumentation, tempo, and vocal character. The scorer had no dedicated emotion signal with enough weight to separate them.

**What shipped:**

- **Fixed 12-mood taxonomy** in `engine/mood_tagger.py`:
  `happy, sad, melancholic, reflective, chill, nostalgic, romantic, energetic, hype, anthemic, aggressive, dark`. Defined once in code; both the OpenAI system prompt AND the structured-output JSON schema enforce this as an enum.
- **GPT-5.4 batch classifier** via structured outputs. `MUSICMIND_OPENAI_API_KEY` (internal, not BYOK). Batch size 30 tracks per call, 5 concurrent calls, strict JSON schema (`{results: [{catalog_id, moods: [tag,...]}]}`). Prompt is explicit about Italian rap: "distinguish hype (confident, flex, adrenaline) from reflective (introspective, narrative, low-valence) — do not default to hype just because it's a rap track." ~$1-2 to tag the full catalog.
- **Migration 026**: adds `global_song_cache.mood_tags JSONB` and `taste_profile_snapshots.mood_distribution JSONB`.
- **New worker phase** `_backfill_mood_tags_global`: drains untagged tracks with basic metadata, runs every cycle AND during the startup drain. Picks tracks with scalar features first so the classifier has tempo/energy/valence context.
- **New scoring dimension** `mood_match` (weight 0.10): cosine similarity between the user's library-aggregated mood distribution and the candidate's mood tags, with positional weighting (primary 0.5, secondary 0.3, tertiary 0.2). CLAP drops 0.30→0.25, MERT 0.25→0.22, EffNet 0.15→0.13 to make room.
- **Graceful degradation**: tracks without mood_tags don't penalize — `compute_context_weights` redistributes the 0.10 mood slot to genre+scalar.
- **Explanations**: `mood_match` surfaces in the admin breakdown view alongside the other 6 weighted dims.

Backfill order on next deploy:

1. Startup drain includes the mood phase — any already-enriched tracks get classified immediately.
2. Main loop keeps classifying as new tracks land in `global_song_cache`.
3. V 6.387 auto-rebuild kicks in after the first batch: user's profile picks up `mood_distribution` on the next recommendations request.

---

## V 6.387 — 2026-04-20

### Auto-rebuild taste profile when centroid inputs change

Previously, `taste_profile_snapshots` had a 24h staleness window and only rebuilt on that timer (or on explicit `force_refresh`). If the worker enriched a library song with CLAP/MERT/EffNet mid-window, the centroids would NOT reflect the new embeddings until 24h passed. The existing stale-return path even logged "will refresh in background" without actually firing a background refresh — a latent bug.

Now `TasteService.get_profile` compares the snapshot's `computed_at` against the max timestamp of every centroid-affecting signal:

- `audio_embeddings.analyzed_at` — new CLAP / MERT / EffNet for the user's library
- `audio_features_cache.analyzed_at` — new scalar features (tempo, energy, etc.)
- `user_calibration.created_at` — onboarding calibration updates

If any source is newer than the snapshot, a background rebuild is fired fire-and-forget; the user still gets the cached snapshot immediately. A module-level `_IN_FLIGHT_REBUILDS` set with an `asyncio.Lock` dedups concurrent triggers so two simultaneous requests don't double-build. The stale-return path now always fires a rebuild (closing the latent bug).

Single cheap query checks all three sources; typical latency is sub-10ms. No DB schema change — reuses existing `analyzed_at` / `created_at` columns.

---

## V 6.386 — 2026-04-20

### Re-ranker: DPP-style CLAP diversity + Steck genre calibration

Two post-hoc re-ranking upgrades grounded in the 2024–2026 music-recs literature (see Wilhelm et al. 2018 on YouTube's DPP, Steck 2018 on Calibrated Recommendations). Both reuse existing enrichment outputs — no new signals required.

**1. DPP-style quality-weighted diversity.** Replaces the raw-cosine MMR diversity penalty with a gaussian kernel on CLAP cosine distance, weighted by the quality scores of both candidate and already-selected tracks:

```
penalty = q_cand · q_sel · exp(−(1 − cos_sim)² / (2σ²))
```

With σ=0.5, near-duplicate pairs (cos≈0.9) produce a strong penalty (≈0.98×q_cand·q_sel), while moderately-similar pairs (cos≈0.5) penalize much more mildly (≈0.61×). Quality weighting prevents low-scoring candidates from "buying" diversity credit by being audio-weird. Falls back to the existing metadata-based `song_similarity` when CLAP isn't available.

**2. Steck-style genre-distribution calibration.** New post-hoc penalty on the greedy re-rank: KL divergence between the running list's genre distribution and the user's target distribution (their `genre_vector`). Expanded-genre weighting matches the per-track scorer (1.0 for originals, 0.3 for parents) so the two signals are consistent. Smoothing prevents KL blowup on sparse distributions.

```
adjusted = base_score
         − 0.10 · diversity_penalty (DPP or MMR fallback)
         − 0.08 · KL(running∪cand ‖ target)
```

Adds a `calibration_kl` key to the score breakdown. Preserves the monotonically-decreasing score invariant and the <500ms/500-candidate performance budget.

The research's top-priority changes (iALS collaborative retrieval, skip-signal rewards, contextual bandits) are deferred — they require user-behavior signals we don't yet persist, or a scale SmarTaste doesn't reach. The DPP + calibration layer is the highest-leverage improvement we can ship today using only the CLAP/genre data that already lives in the enrichment pipeline.

---

## V 6.385 — 2026-04-20

### Fix 502 on /api/recommendations — stop running Essentia at request time

V 6.384 bumped `max_to_enrich` to 200 in the single-pass scoring path, so `enrich_candidates` was attempting synchronous Essentia EffNet extraction for up to 200 candidates per recommendation request. Each call is ~1-2s CPU-bound and blocks the asyncio event loop; 200 of them kept the backend unresponsive for minutes and Railway's load balancer returned 502.

Replaced the call with a new pure-loader `_load_audio_features` that reads from `audio_features_cache` (per-user) then falls back to `audio_features_global` via ISRC. No Essentia at recommendation time — enrichment stays on the worker. Candidates missing features degrade gracefully via `compute_context_weights` weight redistribution, which is exactly the design intent.

---

## V 6.384 — 2026-04-20

### Recommendation engine: multi-centroid profiling, contextual explanations, recency boost

Six improvements to the scoring and recommendation pipeline:

1. **Multi-centroid taste profiling** — k-means clustering (k=min(4, n/5)) on CLAP/MERT/EffNet embeddings captures diverse taste clusters instead of averaging everything into a single centroid. Scorer uses nearest-centroid similarity (max cosine across clusters).
2. **Contextual explanations** — recommendations now say "Similar to X by Y" by finding the 2 nearest library tracks via CLAP cosine, replacing generic dimension-based descriptions.
3. **Single-pass scoring** — loads embeddings for ALL candidates upfront instead of the previous 2-pass (metadata→narrow→audio) approach. Simpler and avoids discarding good candidates before embedding comparison.
4. **Recency boost** — +0.02 for tracks released <30 days ago, +0.01 for <90 days. Surfaces fresh releases without dominating the score.
5. **Feedback-adjusted centroids** — `build_taste_profile` accepts per-track weights (thumbs_up=2.0×, thumbs_down=0.2×) for centroid computation. Wiring ready for when feedback persistence is re-introduced.
6. **User library CLAP loader** — new `_load_user_library_claps` method on `RecommendationService` joins `song_metadata_cache` with `audio_embeddings` to provide the nearest-track context for explanations.

Multi-centroids are computed on fresh profile builds; cached snapshots gracefully fall back to single centroid (no migration needed yet).

---

## V 6.383 — 2026-04-20

### Concurrent Deezer pre-resolution for global GPU backfill

Global GPU backfill was stuck: out of ~1,425 rows needing CLAP/MERT, only 6 had cached audio and 82 had expired Deezer URLs. The sequential per-row resolution resolved ~10/cycle via iTunes, falling under `GPU_MIN_BATCH=15` and deferring every cycle (zero progress for hours).

Rewrote the pipeline as two phases: (1) concurrent pre-resolution of ALL pending tracks — cached audio → stored URL download → fresh Deezer URL via ISRC endpoint (`/track/isrc:{ISRC}`) → iTunes last resort — resolves ~1,500 tracks in minutes instead of hours; (2) dispatch the full queue to GPU with timeout scaled per item (`8s × batch_size`). Removed the `LIMIT 300` row cap so the entire backlog drains in one cycle.

---

## V 6.382 — 2026-04-17

### Fix MERT float16/float32 mismatch on MPS local GPU

MERT inference on the local GPU server (`gpu-worker-local/server.py`) failed on every item with `Input type (MPSFloatType) and weight type (MPSHalfType) should be the same`. The startup validation converted the model to float16 for speed but tested with float32 inputs (MPS auto-casted during the test but not during real inference). Every MERT result returned `None`, which the worker silently skipped — contributing to global MERT = 0.

Fix: disable the float16 path entirely. MERT runs in float32 on MPS. Slight RAM increase (~200MB) but MERT actually works now.

This was the third and final layer blocking global MERT:
1. V 6.381 — COALESCE type mismatch in DB write-back
2. V 6.380 — dead/missing preview URLs for 387/390 rows
3. **V 6.382 — MERT float16 crash on local GPU server**

---

## V 6.381 — 2026-04-17

### Fix COALESCE type mismatch killing global GPU write-back

`_apply_results` used `sa.func.coalesce(json_column, json.dumps(list))` to write CLAP/MERT embeddings. `json.dumps()` returns a Python `str` which asyncpg sends as `varchar` — PostgreSQL rejects `COALESCE(json, varchar)` with `DatatypeMismatchError`. This was the **actual** blocker for global MERT (and CLAP) embeddings: the GPU call succeeded (233 items!) but every `UPDATE` failed silently.

Fix: pass Python lists directly to the `sa.JSON` column — SQLAlchemy handles serialization. COALESCE was unnecessary anyway since the query already filters for NULL columns.

---

## V 6.380 — 2026-04-17

### iTunes re-resolution for global MERT backfill

Global MERT embeddings were stuck at 0 because the 390 CLAP-only rows in `audio_embeddings_global` had dead or missing preview URLs: 273 had no URL at all, 114 had expired Deezer CDN links (403), and only 3 had working iTunes URLs. The `_backfill_gpu_embeddings_global` helper in `worker.py` tried to download from the stored URL but never attempted to re-resolve a fresh one — so the `mert_queue` was always empty or below `GPU_MIN_BATCH`, deferring forever.

**Fix:** `_resolve_bytes` now falls back to `search_preview_url` from `itunes.py` when the stored URL is missing or download fails. On success, the fresh iTunes URL is written back to `global_song_cache.preview_url` and the audio bytes are cached in `preview_audio_cache`, so subsequent cycles skip the CDN round-trip entirely.

Rate-limited to ~17 req/min (3.5s sleep) to stay under iTunes' ~20 req/min IP cap. Capped at 50 iTunes attempts per worker cycle (~3 min wall time) so the main loop isn't blocked. With 390 rows, full drain takes ~8 cycles.

---

## V 6.375 — 2026-04-17

### Sound Space plots every library song, excludes worker-discovered tracks

Two related fixes to `get_library_distributions` (`backend/src/musicmind/api/taste/insights.py`):

**1. Library-only scope.** The query now filters on `library_id IS NOT NULL OR date_added_to_library IS NOT NULL` — the same condition the enrichment-status endpoint uses to identify user-added songs. Previously all enriched rows under the user's user_id were included, which mixed in cobweb-discovered discography tracks (songs the worker pulled in because a library artist was related, but the user never actually added). Those are candidate recommendations, not the user's listening taste, so they no longer pollute the tempo histogram, key distribution, acousticness / valence histograms, and scatter.

**2. Full scatter, not a sample.** The `scatter_limit` default flipped from `200` (stride-sampled) to `None` (include every library song). Performance guard remains — if the library exceeds the cap, stride-sampling kicks back in — but for 99% of users every library song now gets its own dot, which is what the user actually expected when looking at "my library's sound space."

Verified on Filippo's staging DB: 429 library songs now plot (vs. the previous 200-stride subsample of 603 mixed rows).

No backend contract change (the response shape is identical; only the rows included differ). No frontend changes required — the existing `useLibraryDistributions` consumer renders whatever the endpoint returns.

---

## V 6.374 — 2026-04-17

### Dashboard v3 hotfix #2 — layout shell had the same `flex-1` trap, and `hsl(var(--…))` is invalid on hex vars

V 6.372 fixed the grid-level horizontal scroll but the root cause was actually one level *up*: the AppLayout's main content wrapper `<div className="flex flex-1 flex-col lg:pl-64">` (frontend/src/app/(app)/layout.tsx:168) has no `min-w-0`. When the dashboard's Recently Analyzed strip or a Recharts SVG is wider than the viewport minus the 256px sidebar, `flex-1` without `min-w-0` lets that main div grow to the content size, and the whole page overflows. Even my fixes inside the dashboard couldn't prevent it because the overflow was happening one flex level up.

**Fix:** added `min-w-0 overflow-x-hidden` to that shell div. Now no matter what the dashboard (or any future app page) renders, it's clipped to `viewport - sidebar`.

**Why the Sound Signature radar + heading text rendered pitch black on the dark card:**

`globals.css` defines SmarTaste's palette as concrete hex / rgba values:
```
--muted-foreground: #9B8FBB;
--card: #1A1530;
--border: rgba(255, 255, 255, 0.08);
--foreground: #FFF5EB;
```

My Recharts inline styles used `fill: "hsl(var(--muted-foreground))"` and `stroke: "hsl(var(--border))"`. That expands to `hsl(#9B8FBB)` — **invalid CSS** (the `hsl()` function expects three numeric arguments, not a hex string). Browsers fall back to the initial color, which is black. The earlier `--border: hsl(var(--border))` for CartesianGrid had the same issue.

The surrounding SmarTaste Tailwind classes like `text-muted-foreground` work fine because Tailwind compiles them to `color: var(--color-muted-foreground)` — no `hsl()` wrapper.

**Fix on `frontend/src/app/(app)/dashboard/page.tsx`:**
- Sound Signature radar: `PolarGrid stroke`, `PolarAngleAxis tick fill`, and Tooltip `contentStyle` all replaced with concrete palette hexes (`#a855f733` for grid, `#FFF5EB` for tick fills, `#1A1530` for tooltip background, `#a855f755` for tooltip border, explicit `color: "#FFF5EB"` added to tooltip contentStyle so Recharts' inner text nodes render cream not black).
- Hero paragraph "Your library sounds ..." : added explicit `text-[#FFF5EB]` to the `<p>`. Without an explicit color, the text inherited from the Card's `text-card-foreground` class which should resolve to `--card-foreground` (`#FFF5EB`), but Tailwind 4's `@theme inline` directive has been flaky for that specific token in some browsers — explicit hex is defensive.
- Trait-label spans (Energy / Brightness / Beat / Tempo / etc.): added explicit `text-[#FFF5EB]` for the same reason.

No backend changes. Foundation: when a value goes into an inline `style={}` or a Recharts prop, don't use `hsl(var(--…))`; either use `var(--…)` directly (when the CSS var contains a full color value) or use the concrete color hex. Tailwind utilities are the only place the `color: var(--…)` translation happens for you.

---

## V 6.373 — 2026-04-17

### GPU backfill preserves existing embeddings — stop the CLAP overwrite

When `_backfill_gpu_embeddings_global` revisits a row that has CLAP populated but MERT NULL (the 143-row backlog V 6.367 targeted), the GPU worker always returns both embeddings — there's no "MERT-only" inference mode. The DB writer was then overwriting the existing `clap_embedding` with a freshly-computed (functionally identical) value on every pass. Pointless write bandwidth, pointless update-trigger fire, and it also reset any analyzed-at ordering signal.

**Fix:**
- `_backfill_gpu_embeddings_global._apply_results`: wrap each column's new value in `sa.func.coalesce(existing_column, new_value)`. Order matters — `COALESCE(existing, new)` preserves existing; the reverse would always overwrite when new is non-NULL, which is what the old code did.
- `_backfill_gpu_embeddings` (per-user, raw SQL): swapped argument order, `COALESCE(clap_embedding, :clap)` instead of `COALESCE(:clap, clap_embedding)`.

**GPU-side waste still present:** the Modal / local GPU worker endpoint has no "compute MERT only" path, so inference still runs both models. Fixing that requires a GPU-worker protocol change (`models=['mert']` request parameter, short-circuit the unused forward pass). Not in this commit — DB-side waste was the easy half.

No migration. Tests: 20/20 pass.

Code commit: `48cf463`.

---

## V 6.372 — 2026-04-17

### Dashboard v3 hotfix — contained layout, readable tooltips, zoomed scatter

V 6.370 shipped the new layout but three visual problems remained in the wild:

**1. Horizontal page scroll still happened.** Tailwind `grid md:grid-cols-2` creates two-column tracks where each child defaults to `min-width: auto` — which means any child whose content is wider than the column (Recharts ResponsiveContainer that hasn't measured yet, or Musical Keys bars at `flex-1`) can push the column out and the whole page with it.

**Fix:** replaced every `md:grid-cols-2` with `md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]`, which explicitly declares that each column can shrink to 0 min-width. Wrapped every grid child in a `<div className="min-w-0">` as a guard for grandchildren. Added `min-w-0 overflow-hidden` to all three chart cards (SoundSpace / TempoProfile / MusicalKeys).

**2. Custom tooltips had no explicit text color** — text inherited from the SVG container and rendered black on a dark card background. Invisible.

**Fix:** hardcoded `bg-[#0f0a1f]` (deep purple-black, matches palette regardless of how `--card` resolves) and explicit `text-white` / `text-neutral-300` / `text-purple-300` on every tooltip row.

**3. Sound Space wasted the 0-50% danceability half** for libraries that cluster above 50% — Filippo's library has every song above 50% danceability, so the left half of the plot was dead space and the data looked like a vertical smear.

**Fix:** new `computeAxisDomain(values, pad=0.08)` helper computes `[min-pad, max+pad]` clamped to [0, 1], with a minimum 0.35-wide visible range so a tightly-clustered library still reads as a 2D cloud. Applied to both X (danceability) and Y (energy) axes. Vertical grid lines removed (horizontal only). Axis labels shifted to concrete `#a5adba`.

Other small fixes: Sound Space height 280px → 240px, dot size 28 → 36, opacity 0.55 → 0.65; Tempo Profile YAxis width 28, tighter left margin; CartesianGrid uses concrete `#a855f733` instead of CSS variables.

No backend changes. No new endpoints. No dependency changes.

---

## V 6.371 — 2026-04-17

### Startup orchestration: backend plans, worker executes — strict USER → DISCOVERED drains

The old startup flow ran five parallel phases on the worker AND a fire-and-forget chain on the backend lifespan — both services racing on the same tables, both calling `_backfill_isrcs`, `_fill_library_gaps`, `_backfill_gpu_embeddings` concurrently. Postgres serialized writes so nothing corrupted, but the doubled work wasted API quota + GPU spend, and the global/discovered side kicked off in parallel with user-linked work, so a signed-up user whose library still had ISRC gaps saw discovery-only tracks getting GPU-processed ahead of their own library songs.

**New design:**

1. **Backend (`app.py` `_dispatch_startup_work`):**
   - Counts `lib_gaps`, `isrc_gaps`, `gpu_gaps` in the DB.
   - Builds the natural-language plan, logs it.
   - Writes the plan as JSON to `worker_status.detail` with `phase='startup_plan_ready'` so the admin dashboard shows the expected work before the worker has reported any progress.
   - **Returns without dispatching any chain.** Backend is now inspector-only.

2. **Worker (`worker.py main()`):**
   - Phase 0 cleanup unchanged (orphan cleanup, preview cache purge, discography-to-global migration).
   - Phase 1 — **`_drain_user_linked`** — loops `ISRC → preview/audio → Essentia → EffNet → GPU` until `_count_user_linked_gaps` returns 0 or no step makes progress. Max 10 passes with per-pass log deltas.
   - Phase 2 — **`_drain_discovered`** — same loop shape for `ISRC → _enrich_global_songs → _backfill_gpu_embeddings_global` (with V 6.367 URL fallback).
   - Only then enters the main cycle.

3. **New helpers:** `_count_global_gaps` mirrors the existing `_count_user_linked_gaps`. `_drain_user_linked` and `_drain_discovered` are exhaustive drain loops with progress tracking + break-on-stuck guards.

**Why this shape:**
- Strict ordering inside each drain matches pipeline dependency (can't GPU a track that hasn't been Essentia'd; can't Essentia a track without preview URL; preview URL sometimes needs ISRC).
- USER before DISCOVERED maps onto the existing main-loop invariant that cobweb is skipped while any user gap remains — the refactor extends that priority rule to startup.
- Loop-until-zero-or-stuck means a deploy that interrupts enrichment midway no longer wastes a whole main-loop cycle waiting to retry; the drain itself retries.
- Eliminating the backend chain means each backfill function runs on exactly one process per startup.

**Worker log shape post-deploy:**

```
── Startup: USER-LINKED drain ──
User drain pass 1: +120 isrc, +85 prev, +12 ess, +8 eff, +33 gpu  (42 user gaps remain)
User drain pass 2: +40 isrc, +42 prev, +30 ess, +15 eff, +18 gpu  (5 user gaps remain)
User drain pass 3: +3 isrc, +2 prev, +3 ess, +0 eff, +0 gpu  (0 user gaps remain)
✓ User-linked drain complete after 3 pass(es)
── Startup: DISCOVERED drain ──
Discovered drain pass 1: +60 isrc, +50 ess, +125 gpu  (320 global gaps remain)
...
✓ Discovered drain complete after 4 pass(es)
Startup drains complete — entering main loop
```

No migration. No new env vars. No DB schema change (reuses `worker_status.detail`). Tests: 20/20 pass.

**Follow-ups tracked:**
- Move `_backfill_gpu_embeddings_global` into main-loop Phase 5 too (currently only runs during discovered drain + cobweb, so new global MERT gaps wait for next deploy).
- Optional: richer per-phase rows in a `startup_plan` table for step-by-step admin UI without parsing JSON.

---

## V 6.370 — 2026-04-17

### Dashboard v3 — drop discovery noise, chart the enrichment data, fix the missing-artwork root cause

Follow-up to V 6.365. User feedback: Sonic Neighbors pushed the page past the viewport width, Release Era wasn't interesting, artwork was missing everywhere, and the genres pulled from Apple Music were polluted with empty strings and the Italian catch-all "Musica". This release removes the dead weight, fixes artwork at the source, normalizes genres, and replaces Release Era with three interactive charts driven by the Essentia enrichment data that was already sitting unused in `audio_features_cache`.

**Root-cause artwork fix (the most impactful change):**

`backend/src/musicmind/api/taste/fetch.py` was hardcoding `artwork_url_template=""` for both Apple Music library items and Spotify tracks instead of extracting the real URL from the provider response. Confirmed via DB query: 0/679 songs in Filippo's library had an `artwork_url_template` — not because Apple didn't return one but because we were throwing it away. Two-line fix per provider:

- Apple Music library loop now reads `attributes.artwork.url` (the `{w}x{h}bb.jpg` template) and `attributes.artwork.bgColor` from the library-songs response.
- Spotify track mapper now reads `album.images[0].url` (largest available) — Spotify doesn't use an {w}×{h} template, it just ships pre-sized URLs, so the template slot stores the concrete URL and the frontend's artwork expander passes through any string without `{w}` placeholders unchanged.

Existing songs will backfill artwork on the next library sync (happens on next service-connection refresh or whenever the indexer retouches a row). New songs get artwork from first ingest.

**Artist artwork via Apple Music catalog search (lazy + cached):**

User-library album art is the *album* cover; users asked for real artist portraits. Added a three-tier resolution in `get_artist_artworks`:

1. Module-level in-memory cache (`_ARTIST_ARTWORK_CACHE`) — fast path for repeat requests within a process. Empty strings cached too so we don't retry known misses.
2. User-library album art as a fallback (uses the V 6.370 artwork fix above).
3. Apple Music catalog `/v1/catalog/{us|it|gb}/search?types=artists&limit=1` with the app's developer token (no user OAuth needed — catalog search is public data). Tries 3 storefronts in order, accepts only name-matched hits (Apple's fuzzy search can return unrelated artists for short queries like "Shiva"), returns the artwork template. All N top-artist fetches are `asyncio.gather`'d with a 4s per-request timeout.

The /profile endpoint already passes the developer token through; existing callers of `get_artist_artworks` get the third tier for free.

**New `GET /api/taste/distributions` endpoint** (`backend/src/musicmind/api/taste/insights.py`):

Aggregates Essentia enrichment data into dashboard-ready chart data — no intermediate storage, computed on the fly (<50ms for libraries under 5k songs). Returns:

- `tempo_histogram`: 7 fixed BPM buckets (60-80 / 80-100 / 100-115 / 115-130 / 130-150 / 150-180 / 180+). For Filippo: 603 songs, avg 109 BPM, peak in 100-115 (classic modern hip-hop tempo).
- `key_distribution`: 12 pitch classes × {major, minor}. Normalizes Essentia's flat names (`Bb`, `Eb`) to sharp equivalents (`A#`, `D#`) for consistent rendering. For Filippo: heavily minor-keyed (Bb minor, F minor, F# minor dominant — rap's tonal signature).
- `acousticness_histogram` + `valence_histogram`: 10 buckets each, 0-1 normalized.
- `scatter`: up to 200 sampled songs with `{catalog_id, name, artist_name, energy, danceability}` for the interactive Sound Space chart. Stride-sampled (not random) to keep the view stable across requests.

**New `clean_genre_vector()` normalization** (`insights.py`):

- Blocklist: `""`, `"musica"`, `"música"`, `"music"` (Apple's localization-artifact catch-all) stripped before rendering.
- Merge map: `"Rap"` and `"Hip-Hop"` fold into `"Hip-Hop/Rap"` when the compound label is already present (prevents double-counting parent + child on the same track). Same for `"Soul"` / `"R&B"` → `"R&B/Soul"`.

Applied in the `/profile` endpoint response path so existing snapshots benefit without re-computation. Filippo's pre-clean genre vector had 14 entries including `""` (0.88%) and `"Musica"` (10.97%); post-clean it has 11 clean entries dominated by Hip-Hop/Rap.

**Frontend — dashboard layout v3:**

*Removed:*
- `SonicNeighborsCard` — caused horizontal page scroll on the 4-column grid, and was too abstract for users who don't know what a centroid is. Endpoint stays live for future use.
- `ReleaseEraCard` — year distribution didn't answer a question users had.

*Added:*
- `SoundSpaceCard` — **the new hero chart**. Recharts `ScatterChart` plotting every enriched song as a dot on an Energy × Danceability plane. Hovering a dot shows the song name, artist, and exact percentages. Header tag-line auto-classifies the dominant quadrant ("mostly high-energy & danceable" vs. "groove-heavy, low-intensity").
- `TempoProfileCard` — BPM-bucket bar chart with the peak bucket highlighted in bright violet. Header shows the library's mean BPM + peak bucket label.
- `KeyModeCard` — per-key horizontal bars split into a major segment (light violet) and a minor segment (dark violet) for all 12 pitch classes. Immediately reveals the library's tonal bias (hip-hop users see a wall of minor, pop users see the opposite). Header shows overall major/minor ratio.

*Fixed:*
- `RecentlyAnalyzedStrip` now wraps the scroll region in a `Card` with `overflow-hidden` and an inner `overflow-x-auto` — prevents the 12 × 144px tile row from leaking past the card's right edge and triggering page-level horizontal scroll. Also handles `artwork_url` fallback via `global_song_cache.artwork_url` when the user-library template is empty (pre-fix libraries).
- Top-level page has `min-w-0` on the root container so no child element can force horizontal scroll.

*Layout order (new):*

```
Calibration
Sound Signature hero (full width)
Genre DNA  |  Top Artists            (side-by-side)
Sound Space scatter                   (full width)
Tempo Profile  |  Musical Keys        (side-by-side)
Recently Analyzed                     (full width, scrolls within card)
Library Snapshot                      (full width, 2-column dl)
Tab nav
```

**New frontend types + hook:** `TempoBin`, `KeyBucket`, `DistributionBucket`, `ScatterPoint`, `LibraryDistributionsResponse`. `useLibraryDistributions()` with a 15-min stale time.

**Why all three new charts are genuinely interactive:** Sound Space has per-dot tooltips with track metadata. Tempo Profile highlights the peak bucket and shows "X songs at this BPM" on hover. Musical Keys shows count + major/minor split per-bar on hover via the browser title attribute (lightweight, no extra JS). Every chart uses direct labels or tooltips — no detached legend, no eye travel.

No migration. No new dependencies. No backend dependency additions. The only permanent backend cost is a module-level dict for the artist-artwork cache (bounded by artist names in the system, self-limiting).

---

## V 6.367 — 2026-04-17

### Global GPU backfill: URL fallback + 500-row limit — drain the MERT-only backlog

The V 6.363 WHERE-clause fix made `_backfill_gpu_embeddings_global` *visible* to the 143 CLAP-only / MERT-NULL rows, but it still couldn't *process* them. The function only had a bytes path (`enrich_bytes_concurrent`), reading from `preview_audio_cache` via `_get_cached_audio`. Those 143 rows had gone through GPU once (got CLAP) and then the 7-day `preview_audio_cache` cleanup deleted their audio bytes. Without cached audio, no bytes → skipped. Under this logic the 143-row backlog could only clear if each track's audio was re-downloaded, which the worker doesn't trigger for already-Essentia'd rows.

**Fix:** `worker._backfill_gpu_embeddings_global` rewritten.

- Row limit per call: 200 → 500 (more aggressive drain per cycle).
- Builds **two queues** instead of one:
  1. `bytes_queue`: ISRCs with `_get_cached_audio` hits — preferred path, no CDN round-trip.
  2. `url_queue`: ISRCs without cached audio but with a non-empty `global_song_cache.preview_url` — uses `enrich_urls_concurrent` so the Modal / local GPU worker fetches the preview itself. iTunes URLs don't expire; Deezer URLs might 403 but the GPU worker logs the failure and skips that single row.
- Both queues dispatched independently, each gated by `GPU_MIN_BATCH` (15). Shared `_apply_results` helper writes the `clap_embedding` / `mert_embedding` columns with the existing COALESCE-style merge (preserves whichever field was already populated).
- One structured log line per dispatch shows `%d bytes, %d URL-fallback (of %d missing)` so it's visible in Railway logs why a cycle processed N of M.

**Why this matters beyond the 143 backlog:**

Going forward, every new global track (cobweb + discography-fetched) will benefit from the URL fallback too — if a track's cached audio expires before its first GPU pass, it still gets CLAP + MERT from its preview URL. No more "waits for audio cache" dependency.

**Expected DB effect over the next 5–10 minutes:**
- `audio_embeddings_global.mert_embedding`: 0 → 143+ (the pre-existing CLAP-only rows will MERT-backfill via URL path)
- `audio_embeddings_global.clap_embedding`: 143 → climbing toward 1810 as the combined bytes+URL queues clear the long tail
- GPU worker log shows larger batches (up to 25 per dispatch, two dispatches per cycle)

No migration. No new env vars.

---

## V 6.366 — 2026-04-17

### Fix: column-name typos in `_migrate_discography_to_global` INSERT

Railway worker was logging `UndefinedColumnError: column "loudness_lufs" of relation "audio_features_global" does not exist` on every cycle. The SQL in `worker._migrate_discography_to_global` that promotes per-user discography enrichment to the global table referenced two columns that don't exist in the live schema:

- `loudness_lufs` — both `audio_features_cache` and `audio_features_global` use plain `loudness`
- `enriched_at` — only `audio_features_cache` has it; `audio_features_global` has `analyzed_at` with a `NOW()` default

**Fix:** `backend/src/musicmind/worker.py` `_migrate_discography_to_global`:
- `loudness_lufs` → `loudness` (both INSERT column list and SELECT)
- Dropped `enriched_at` from the column list; the `NOW()` default on `analyzed_at` fills in automatically

No migration. No schema change. No impact on enrichment logic — this was purely a typo in the migration SQL.

---

## V 6.365 — 2026-04-17

### Preview-URL resolution: iTunes Search as the third fallback

The V 6.364 diagnostic made the real problem visible: **0 out of 1296 rows in `global_song_cache` have a `preview_url`**, and the orchestrator was logging "Phase 1 done: 1/16 Essentia enriched (0 skipped, 15 failed)" on every cycle. Every failing track hit the `"no_preview_url"` terminal branch. `_apple_track_to_cache_dict` hard-codes `preview_url=""` for discography-fetched tracks (Apple's search/albums endpoints don't return previews on the anonymous path), Spotify's SimplifiedTrackObject usually has `null`, and Deezer's `search_preview_url` — while it handles ISRC lookups well — doesn't index Italian underground catalogues nearly as densely as Apple.

**New fallback chain in `engine/enrichment/orchestrator._enrich_single_track`:**

1. `track["preview_url"]` (from the fetch path)
2. `song_metadata_cache.preview_url` (cached from a prior user-library entry)
3. `deezer.search_preview_url(name, artist, isrc)` — ISRC-first, name+artist fallback
4. **(new)** `itunes.search_preview_url(name, artist)` — Apple's unauthenticated Search API
5. Give up → mark `"no_data_available" / "no_preview_url"`

**New module: `backend/src/musicmind/engine/enrichment/itunes.py`.**

- `GET https://itunes.apple.com/search?term={name artist}&entity=song&limit=5&media=music`
- No auth, no key. Unofficial per-IP rate limit is ~20 rpm; we only fire when Deezer has already missed, so real call volume is a small fraction of total enrichment work.
- Four-tier match scoring: exact (title, artist) → exact title → title-contains → first-non-empty `previewUrl`. Returns None on any HTTP / rate-limit / decode error — caller treats as "try again next cycle."
- Returns the same 30-second MP3 format as Deezer previews, so Essentia consumes it with zero additional changes.

**Caching helper refactor: `_cache_preview_url(engine, catalog_id, preview_url)`.**

Replaced two inline `UPDATE song_metadata_cache … WHERE catalog_id = :cid` blocks (Deezer and iTunes) with a shared helper that writes to **both** `song_metadata_cache` and `global_song_cache`. Discography-fetched tracks live only in `global_song_cache`, so the original smc-only update was silently a no-op for them and the preview URL was never persisted — every cycle repeated the lookup. The new helper updates both tables; whichever owns the row gets the write, the other is a silent no-op.

**Expected effect:**

- Italian rap tracks with no ISRC and no Deezer match (the majority of the "15 failed" in recent logs) now get a preview URL via iTunes and flow through Essentia → EffNet → GPU CLAP/MERT normally.
- `global_song_cache.preview_url` column starts populating (was 0/1296 → should climb as the worker cycles through the enrichment queue).
- Retry amplification goes down: a catalog_id that needed iTunes once no longer re-queries iTunes on every subsequent cycle because the URL is now persisted.

**Follow-ups tracked:**
- `_apple_track_to_cache_dict` could itself attempt iTunes resolution inline when the discography track has no preview (would save a DB write round-trip), but keeping all preview-URL policy in the orchestrator simplifies the control flow.
- Deezer URLs expire; iTunes URLs use permanent Apple CDN keys — no expiry handling needed for the iTunes path.

No migration. No new env vars. No new dependencies.

---

## V 6.364 — 2026-04-17

### Full discography fetch + compound-name credit + case dedup + ISRC backfill throughput

Four coordinated indexing fixes that finally make "top artists get their full discography analysed" be true in practice.

**1. Full-discography paginator replaces top-tracks.**

`_fetch_artist_top_tracks` hits Spotify `/artists/{id}/top-tracks` and Apple `?views=top-songs` — both hard-capped at ~10 by the service APIs. That's why even Capo Plaza (31 library songs, calibration rank 15) had only 20 entries in `global_song_cache`: the indexer asked for 50 tracks but got 10 and moved on.

New `_fetch_artist_full_discography` in `api/recommendations/fetch.py` paginates `/artists/{id}/albums` → for each album `/albums/{id}/tracks` (Spotify) or `/albums/{id}` with tracks relationship (Apple). Dedups by ISRC, or by `(title, album)` tuple when ISRC is absent (Spotify SimplifiedTrackObject doesn't carry ISRC — those rows rely on `_backfill_isrcs` to resolve later). Stops once `limit` tracks are collected so a 300-album artist doesn't generate 300 API calls.

`indexer._fetch_and_enrich_discography` now uses this. `MAX_TRACKS_PER_ARTIST` raised from 50 → 200 to give the top calibration picks actual headroom.

**2. Credit every co-primary artist, not just `parsed[0][0]`.**

`_get_ranked_artists` was dropping every co-primary except the first: "SadTurs & KIID" with 7 songs credited only SadTurs. Now iterates every `parse_artists` result with weight ≥ 1.0 and credits each one. Feature artists (weight 0.3) continue to flow through the cobweb, not the ranking.

**3. Case-insensitive aggregation for the frequency map.**

"KERO" and "Kero" (same artist, different service metadata casing) were stored as separate keys, splitting their library counts. `_get_ranked_artists` now aggregates by `lower()` key with first-seen casing preserved for display. Artists that appear in both casings now get their combined song counts.

**4. ISRC backfill batch size 100 → 500 per cycle.**

`_backfill_isrcs` was called with `batch_limit=100` from the main worker loop. With 274 library rows still `NULL` at audit time and the library sync continuously adding new rows, that rate was barely keeping pace. 500 per cycle drains the backlog in 1–2 cycles. Startup call was already 500.

**Tests:** 20/20 pass across `test_indexer_ranking`, `test_artist_affinity`, `test_cobweb_ranking`. No test contract changes in this commit.

**Expected effect:**
- Top 3 calibration picks (Nabi, Neima Ezza, Vale Pain) start getting ~200-track discography instead of ~10.
- Co-primary artists like KIID, Bobo no longer lose rank position to their "&" partner.
- Library dedup reduces artist count in the admin dashboard (KERO/Kero collapses into one entry).
- ISRC null count drops from 274 → single digits within a couple of cycles (new library sync entries will still appear NULL briefly).

**Follow-ups not in this commit:**
- Migration to also dedup existing `song_metadata_cache` rows on casing collision (this commit fixes the aggregation; the underlying rows are still separate).
- Full-discography for cobweb artists (currently they still use `_fetch_artist_top_tracks` since 10 tracks is the intended depth for feat-discovered artists).

No migration. No new env vars.

---

## V 6.363 — 2026-04-17

### Recommendation pipeline: MERT global backfill + calibration-dominant ranking + cold-start cleanup

Three independent fixes that together make the recommendation pipeline's outputs actually match the user's taste.

**1. Global MERT backfill was skipping rows with CLAP populated.**

`audio_embeddings_global` had 112 rows with CLAP and **zero** with MERT, while the per-user equivalent (`audio_embeddings`) had 932/940 CLAP and 892/940 MERT — 94% coverage. The per-user path and global path use the same GPU call (`enrich_bytes_concurrent`), so the GPU is producing MERT fine; the global backfill's `WHERE` clause was just excluding those rows from ever being revisited.

Root cause: `_backfill_gpu_embeddings_global` selected rows where `clap_embedding IS NULL AND embedding IS NOT NULL`. The 112 rows had been processed in a pass when MERT wasn't wired up yet; once CLAP was written, the row was invisible to future cycles even though its MERT column stayed NULL.

Fix (`backend/src/musicmind/worker.py`): `WHERE` clause now selects rows where `clap_embedding IS NULL OR mert_embedding IS NULL`. The per-row update logic already uses COALESCE-style field merging, so existing CLAP values are preserved.

**2. Cold-start path was re-introducing the 4 legacy strategies.**

V 6.351 unified worker-side discovery to the cobweb only, but `api/recommendations/service.py:get_recommendations` still had a cold-start fallback: when `recommendation_candidates` was empty and the user had creds, it ran `discover_similar_artists / _genre_adjacent / _editorial / _chart_filter` live and wrote their output back to `recommendation_candidates`. Every new session before the worker had cycled once wrote 108 polluted rows (similar_artist + chart) into the DB — exactly the kind of editorial/chart pollution V 6.351 was supposed to eliminate.

Fix (`backend/src/musicmind/api/recommendations/service.py`): cold-start now kicks off the background populate task (cobweb-based) and returns the empty candidate set. The UI already handles a zero-candidate state (it's what every new user sees for the first minute anyway). The four `discover_*` imports stay for now because `_run_discovery` is still defined; full removal is tracked separately.

**3. Calibration now dominates library frequency in artist ranking.**

The user's wizard explicitly asks them to pick their top 3 artists (weight 5) and rank 5+ more (weights 1–3). For this user the top_artist picks are Nabi, Neima Ezza, Vale Pain — all with modest library counts (8, 9, 12 songs). The old ranking gave Capo Plaza (44 library songs, no calibration pick) the top slot anyway, because `_rank_artists_by_affinity` used `freq × (1 + 0.1 × cal_weight)` — calibration was at most a 50% multiplier on top of frequency.

Fix (`backend/src/musicmind/indexer.py`):
- `CAL_BOOST` raised from `0.1` (multiplicative) to `100.0` (additive). `score = freq + CAL_BOOST × cal_weight` — weight 5 adds +500, weight 1 adds +100, uncalibrated adds 0. Library frequency becomes a within-tier tiebreaker.
- Test contract inverted: `test_frequency_dominates_calibration_when_mismatched` → `test_calibration_dominates_frequency_when_user_picked`. Two other tests rewritten (`test_frequency_breaks_ties_within_calibration_tier`, `test_uncalibrated_high_freq_still_surfaces`) to express the new semantics.
- Expected effect on this user's indexing: top 3 slots now Nabi / Neima Ezza / Vale Pain (or whichever ties break them) at 100% discography depth, then the artist_rank picks (Philip, SEVEN 7oo, Sacky, Shiva…) at 70%+, with Capo Plaza and other non-calibrated library artists further down at ~25% depth.

**Data surgery:** 108 legacy `similar_artist / chart` rows purged from `recommendation_candidates` on staging via psql so the candidate table now contains 242 cobweb-only rows.

**Follow-ups tracked but not in this commit:**
- Full-discography pagination (top-tracks API caps at 10; need to paginate `/artists/{id}/albums`).
- Compound-name splitting for the 30 library artists with zero `global_song_cache` entries ("Kid Yugi, Tony Boy & Artie 5ive", etc.).
- ISRC backfill lag (274 library rows with NULL ISRC).

No migration. No new env vars.

---

## V 6.362 — 2026-04-17

### Discovery: exclude tracks already in the user's library

The cobweb candidate-writer in `worker.py::_populate_candidates_from_cobweb` joined `artist_cobweb` with `global_song_cache` on exact `artist_name` and inserted every match. For feat-sourced cobweb artists, that pulled in their primary tracks — which in many cases were also already sitting in the user's library (a library track's featured artist points back to the library track itself via the cobweb's enrichment of the feat artist's top 50 songs).

**Fix:** added a `NOT EXISTS` subquery to the candidate-write SQL. A `global_song_cache` row is excluded when the user has *any* `song_metadata_cache` row marked as library (`library_id IS NOT NULL OR date_added_to_library IS NOT NULL`) that matches either by `catalog_id` (same-service) or by non-empty `isrc` (cross-service — prevents a Spotify library entry slipping through as an Apple-catalog candidate with a different catalog_id).

Also purged existing library-matching rows from `recommendation_candidates` on staging via psql so the effect shows up immediately, not only on the next worker cycle.

No migration. No new env vars.

---

## V 6.361 — 2026-04-17

### GPU min-batch raised 3 → 15

Modal / local GPU worker logs were still dominated by 3-, 4-, 5-item batches (`Batch-bytes: 3 items processed (3 via batched CLAP)` over and over). Those tiny batches don't amortise the A100 cold-start or the audio-decode pipeline — each round-trip pays the same fixed cost for 3 items as for 25.

**Change:** `backend/src/musicmind/engine/enrichment/gpu_client.py` — `GPU_MIN_BATCH = 3` → `GPU_MIN_BATCH = 15`. No other changes.

The worker's min-batch deferral logic (in `_backfill_gpu_embeddings` / orchestrator Phase 2) already consults this constant; raising the floor means Phase 2 holds 1–14-item queues over to the next cycle until they accumulate to 15+, instead of firing a wasteful small batch. Cap stays at 25 (Modal's per-request limit). Straggler tracks at the tail of a big run will see slightly longer latency, which is acceptable for a background enrichment pipeline.

No migration. No config changes. Effective on next Railway worker redeploy.

---

## V 6.360 — 2026-04-17

### Dashboard v2 — centroids become neighbors, artwork everywhere, fresh-pipeline ticker

Follow-up to V 6.350. The previous redesign derived all insights from what the `/api/taste/profile` response already returned; this one surfaces the data the snapshot was *saving but never serving* — specifically the `clap_centroid` / `mert_centroid` / `embedding_centroid` / `audio_centroid` JSON columns — and threads album + artist artwork through every card where it carries information cheaply.

**New backend module `backend/src/musicmind/api/taste/insights.py`:**

- `format_apple_artwork(template, size)` — resolves Apple Music `{w}x{h}bb.jpg` templates to a concrete thumbnail URL. Pure function, safe on empty input.
- `get_artist_artworks(engine, *, user_id, artist_names, size)` — one query returns `{artist_name_lower: artwork_url}` for a list of artists, picking each artist's most recently added library song as the representative. Filters out rows with null/empty templates.
- `get_recent_enrichments(engine, *, user_id, limit)` — join of `audio_features_cache` × `song_metadata_cache` ordered by `COALESCE(enriched_at, analyzed_at) DESC`. Returns name + artist + album + artwork + Essentia scalars (tempo, energy, danceability) per row.
- `compute_sonic_neighbors(engine, *, user_id, clap_centroid, limit, sample_size)` — samples up to 2000 rows from `audio_embeddings_global` (ISRC-keyed CLAP-enriched global catalog), joins with `global_song_cache` for artist + artwork, filters out artists already in the user's library (case-insensitive), computes pure-Python cosine similarity per row (no numpy dep), groups by artist with best-per-artist, returns top N. Returns empty list if the user has no CLAP centroid yet.
- `compute_breadth_metrics(genre_vector, top_artists)` — three numbers derived from the snapshot dicts the profile already returns: `genre_entropy` (Shannon entropy normalized to 0–1), `artist_concentration` (top-5 artists' share of total artist score), `sonic_breadth` (composite `0.6·entropy + 0.4·(1-concentration)`).

**New backend endpoints on `backend/src/musicmind/api/taste/router.py`:**

- `GET /api/taste/sonic-neighbors?limit=8` — returns `SonicNeighborsResponse { service, neighbors: [{artist_name, similarity, sample_song_name, sample_catalog_id, sample_album_name, artwork_url, genre_names}], note }`. The `note` explains degradation cases (no CLAP centroid yet, empty result).
- `GET /api/taste/recent-enrichments?limit=12` — returns `RecentEnrichmentsResponse { items: [{catalog_id, name, artist_name, album_name, artwork_url, enriched_at, tempo, energy, danceability}], total }`.

**Backend schema additions on `backend/src/musicmind/api/taste/schemas.py`:**

- `ArtistEntry` gains `sample_artwork_url: str | None` (backward-compatible — default None).
- `TasteProfileResponse` gains `breadth: BreadthMetrics | None` (ditto).
- New `SonicNeighbor`, `SonicNeighborsResponse`, `RecentEnrichment`, `RecentEnrichmentsResponse`, `BreadthMetrics` models.
- `TasteProfileResponse.model_rebuild()` called at module bottom to resolve the `BreadthMetrics` forward reference under `from __future__ import annotations`.

**Profile endpoint enrichment (`/api/taste/profile`):**

Response-assembly path now pulls artist artworks and breadth metrics at response time (not stored in the snapshot — stays computed), so the existing 24-hour snapshot cache doesn't need a migration. If `get_artist_artworks` fails, profile still returns with null artwork URLs — never blocks the page.

**Frontend — `frontend/src/app/(app)/dashboard/page.tsx`:**

- **Sonic Neighbors card (new, full-width)** — 8-artist grid with artwork, similarity percentage bar, and the sample song name that earned the match. Uses the new `useSonicNeighbors()` hook (30-min cache). Renders a graceful "still computing" state when the CLAP centroid hasn't been built yet.
- **Recently Analyzed strip (new, full-width, horizontal-scroll)** — up to 12 freshest songs from the pipeline with 144px artwork tiles, BPM from Essentia tempo, and a relative-time label ("12m ago", "3h ago", "2d ago"). Uses a fresher 5-min `staleTime` so the pipeline's output feels alive.
- **Top Artists (upgraded)** — now shows a 40px circular avatar per artist sourced from the backend's `sample_artwork_url`. Graceful fallback to a violet gradient tile with the artist's initials when no artwork exists. The existing affinity bar was kept, narrowed to make room for the avatar without breaking the visual rhythm.
- **Library Snapshot (extended)** — adds two new rows from `profile.breadth`: "Sonic breadth" (composite %) labeled `focused` / `balanced` / `broad` / `eclectic`, and "Top-5 concentration" (%) labeled `artist-focused` / `balanced` / `spread out`. Both rows only render if the backend returns `breadth` (forward-compat with older snapshots).
- **`Artwork` primitive (new, inline)** — shared component used by Sonic Neighbors and Top Artists. Handles missing URLs with an initials gradient tile in 4 sizes × 2 shapes (rounded-md for albums, rounded-full for artists). Uses plain `<img>` (not `next/image`) — no `next.config.js` domain whitelist juggling needed, and artwork URLs from Apple/other sources don't need Next's optimization pipeline.

**New frontend types + hooks:**

- `frontend/src/types/api.ts`: `BreadthMetrics`, `SonicNeighbor`, `SonicNeighborsResponse`, `RecentEnrichment`, `RecentEnrichmentsResponse`. `ArtistEntry.sample_artwork_url` and `TasteProfile.breadth` added as optional.
- `frontend/src/hooks/use-taste.ts`: `useSonicNeighbors(limit)` and `useRecentEnrichments(limit)`.

**Design decisions:**

- Artwork everywhere, but never decoratively — every tile is the shortest-path signal (album art disambiguates "Shiva" the artist from "Shiva" the song, BPM on a tile is more memorable than a line of text).
- Sonic-neighbors explicitly *excludes* in-library artists so it acts as a discovery surface rather than re-ranking what the user already knows.
- Breadth metrics use server-side math (not client) because the Shannon-entropy calculation depends on the full genre vector the server already has in memory — cheaper than shipping the vector twice.
- Pure-Python cosine (no numpy) in `compute_sonic_neighbors` — the cost of loading numpy into the backend wheels for a 2k × 512 dot product once-per-dashboard-load isn't worth it; the hot path is DB I/O, not vector math.

No migration. No new env vars. No backend dependency additions. No frontend dependency additions. Pydantic `model_rebuild()` covers the forward reference without splitting the module.

---

## V 6.351 — 2026-04-17

### Unify all discovery through the cobweb — stop surfacing unrelated songs

The admin dashboard was showing candidates like Dua Lipa's "New Rules" and Ezra Collective's "Chapter 7" for an Italian-rap-dominant listening profile. Root cause: `_run_cobweb_cycle` in `worker.py` was running *two independent* discovery systems per cycle — the cobweb (feat-parsed related artists, provably tied to the library) and `_run_discovery_for_user` (four parallel strategies — similar-artist / genre-adjacent / editorial / chart — that wrote to `recommendation_candidates` without consulting the user's genre or artist profile). Editorial playlists and chart filters in particular pulled in whatever the platform was currently promoting in Italy, regardless of the user's actual taste.

**Fix:**

- `_run_cobweb_cycle` in `worker.py` now calls a new `_populate_candidates_from_cobweb` after `_build_user_cobweb`, instead of `_run_discovery_for_user`. The new function does a single `INSERT … SELECT` joining `artist_cobweb` (user-scoped, enriched=true) with `global_song_cache` on exact `artist_name` match, writing rows with `strategy_source='cobweb'` and a discovery_weight derived from the cobweb's own priority (normalised to [0.1, 1.0]). `ON CONFLICT` keeps the max weight and refreshes `fetched_at`.
- `_run_discovery_for_user` is retained as dead code for now — `api/recommendations/service.py` still imports `discover_similar_artists / _genre_adjacent / _editorial / _chart_filter` for its cold-start fallback path. Ripping those imports out cascades into the recommendations pipeline and belongs in a follow-up.

**Data surgery (run before deploy):**

- `user_indexing_status`: force-completed one row that had been stuck at `step=5, step_name='artist_31_of_33'` since 2026-04-12. The compound library-artist name "Keta & Manny Troublez" isn't resolvable by Apple/Spotify artist-search, so the indexer loop cycled on it indefinitely while `updated_at` kept refreshing. `step=7, completed_at=NOW()` unblocks the cobweb cycle (which skips actively-indexing users). Proper compound-name splitting in the indexer is tracked for a later fix.
- `recommendation_candidates`: truncated (254 rows across 4 strategy_sources deleted). The cobweb cycle will repopulate on the next worker tick with clean cobweb-sourced rows only.

**Expected effect on the admin dashboard:**

- Songs table will shrink then rebuild with every row tracing back to a real library collaboration.
- The source badge on the artists table was already "feat" for cobweb entries; no change there.
- Top artists' discography depth is unchanged by this commit — that's a separate concern (indexer step-5 compound-name handling) to be addressed next.

No migration. No new env vars. No frontend changes.

---

## V 6.350 — 2026-04-17

### User dashboard redesign — enrichment-derived taste insights replace stale proxy metrics

The user-facing `/dashboard` overview (not the admin dashboard) hadn't been touched since before the April-2026 pipeline rebuild, so it surfaced metrics that are either known to be wrong (`listening_hours_estimated`, extrapolated from added-at timestamps), conceptually stale (`familiarity_score`, a coarse proxy that no longer maps to the 6-dim scoring engine), or design-incorrect (a pie chart with 8 slices — the ui-ux-pro-max ruleset and Tufte both flag this). The enrichment pipeline computes seven aggregated Essentia scalar traits per user (tempo, energy, brightness, danceability, acousticness, valence_proxy, beat_strength) plus genre/artist/release-year distributions; none of that was being translated into plain-English insight.

**What's new on `frontend/src/app/(app)/dashboard/page.tsx`:**

- **Sound Signature hero card** — the new anchor of the page. Generates a natural-language headline from the user's audio_trait_preferences ("Your library sounds high-energy, rhythm-forward, and electronic — rooted in Hip-Hop/Rap."), renders a radar of the seven traits alongside a stat column with per-trait percentage bars + one-line descriptions ("Rhythmic stability", "Positive / uplifting feel", etc.). Descriptor generation lives in a local `describeSound()` helper keyed on energy / danceability / valence_proxy / acousticness / tempo / beat_strength thresholds. Gracefully degrades when fewer than 3 traits are populated (new accounts, pre-enrichment state).
- **Genre DNA card** — replaces the 8-slice donut with direct-labeled horizontal bars (rank · genre · bar · %). Preserves the existing regional-prioritized genre names from `profile.genre_vector` but presents them in a layout that scales linearly with category count. No legend, no eye travel.
- **Top Artists** — kept, restyled with consistent bar gradient (`from-purple-600 to-purple-400`) to match the rest of the page. Unchanged data contract.
- **Release Era card** — the year distribution is now an `AreaChart` with a vertical purple gradient fill, and a header-right callout that states the range and peak year ("2020–2026 · Peak: 2023 · 18% of library") so the insight is readable without reading the axis.
- **Library Snapshot card** — replaces the four top-row stat cards. A compact definition list showing songs-analyzed, genres-detected (with "regional-weighted" hint), ranked-artist count, source service(s), and a relative-time "last updated". No more Listening Hours. No more Familiarity.

**What was removed:**

- The four-card row at the top (Songs Analyzed / Listening Hours / Familiarity / Service)
- The Top Genres pie chart
- The Audio Traits radar (now absorbed into the Sound Signature hero)
- The Release Years bar chart (now an area chart inside its own card with a narrative callout)

**What was kept:**

- The Calibration banner (incomplete state) + Calibration summary (completed state), unchanged — still the only primary CTA on the page.
- The tab navigation at the bottom (`/dashboard/taste`, `/dashboard/stats`, `/dashboard/recommendations`).

**Data contract:** no backend changes. All insights are derived client-side from the existing `/api/taste/profile` response (`audio_trait_preferences`, `genre_vector`, `top_artists`, `release_year_distribution`, `services_included`, `computed_at`, `total_songs_analyzed`). The now-unused `listening_hours_estimated` and `familiarity_score` fields are still returned by the API for back-compat but silently ignored by the dashboard.

**Design decisions (ui-ux-pro-max ruleset):** pie avoided for >5 categories (→ horizontal bars); radar preserved for the multi-variate signature (its canonical use case); area chart preferred over bars for a 15-point time series (editorial feel, smoother reading); one primary CTA per screen (calibration); `tabular-nums` on every numeric cell; per-trait descriptions below each bar to make the fingerprint legible without a glossary; `bg-gradient-to-br from-purple-500/[0.04]` on the hero only, to anchor visual hierarchy without overloading the page with gradients.

No new dependencies. No backend changes. No API contract changes. Existing `useTasteProfile()` hook unchanged.

---

## V 6.341 — 2026-04-17

### Admin dashboard: fix TypeError crash on first render

The relocated dashboard (V 6.340) crashed on hydration with `TypeError: undefined is not an object (evaluating 'e.global_isrc_cache.toLocaleString')`. The cards briefly rendered their skeletons, then React tried to apply the real data and threw — producing the Safari "page couldn't load" chrome because the error propagated past React's (absent) error boundary.

**Root cause:** the hand-maintained `SystemStatus` TypeScript interface in `admin/ui/app/page.tsx` was copied verbatim from the V 6.330 main-frontend version, which had drifted from the backend. Backend `/api/admin/status` returns `total_users / connected_users / gpu_embeddings`; the interface expected `users / connections / global_isrc_cache / listening_history_entries`. TypeScript doesn't enforce this at runtime, so undefined-access through a `status ? ...` branch crashed once the fetch resolved.

**Fix:** `admin/ui/app/page.tsx`
- `SystemStatus` interface aligned to the actual backend response
- "Users" card: uses `total_users`, sub-label now "N connected · M calibrated"
- "Total Songs" card: sub-label now shows `gpu_embeddings` as "N GPU embeds" (the field that actually exists), replacing the phantom `global_isrc_cache` reference

Same bug existed latent in the main-frontend dashboard before V 6.340's removal, masked there by the outer `AppLayout` error boundary. No backend changes. No endpoint contract changes.

---

## V 6.340 — 2026-04-17

### Admin dashboard relocated from main frontend to the standalone admin Railway service

The V 6.330 React dashboard (songs/artists drill-down tables + overview bar + diagnostics) was originally built inside the user-facing Next.js frontend at `frontend/src/app/(app)/admin/`. It now lives on its own Railway service (`admin.music.menghi.dev`, same URL as before), built into the existing FastAPI container as a static Next.js export.

**Why the move:**
- Keep admin JS out of the user-facing bundle (smaller bundle for normal users, reduced attack surface)
- Decouple admin deploy cadence from the main frontend
- Dedicated domain/auth surface — admin goes through the single-password HMAC cookie gate, not the main app's JWT flow

**What changed:**

- `admin/ui/` — new Next.js 16 app (static-export mode, `output: "export"`)
  - `app/page.tsx` — ex-frontend admin page, with `useAuthStore` gate + `useRouter` redirect removed (the Python gate already redirects to `/login` before the page ever loads). Adds a tiny log-out link in the header.
  - `app/layout.tsx` + `app/providers.tsx` — root layout, DM Sans + Sora fonts, dark theme hardcoded, `QueryClientProvider` wrapper.
  - `components/admin/{songs-table-card,artists-table-card,status-dot}.tsx` — moved verbatim.
  - `components/ui/{card,badge,button,skeleton}.tsx` — copied from main frontend (~270 lines of shadcn primitives).
  - `hooks/use-admin-tables.ts` — moved verbatim.
  - `lib/api.ts` — stripped-down same-origin `fetch` wrapper. No CSRF, no JWT refresh — not needed because the Python proxy handles auth upstream. On 401 it redirects to `/login`.
  - `lib/utils.ts` — just `cn`.
  - `app/globals.css` — full SmarTaste token system copied so the admin UI matches the main app visually.

- `admin/Dockerfile` — now two-stage:
  1. `node:20-slim` builds the Next.js static export (`npm install && npm run build` → `/ui/out`)
  2. `python:3.14-slim` serves FastAPI and the bundled `out/` copied to `/app/static`

- `admin/app.py`:
  - `GET /` now returns `FileResponse(static/index.html)` after the admin cookie check, instead of reading `templates/dashboard.html`.
  - `/_next/*` mounted via `StaticFiles` (hashed assets, public — shell only, no data).
  - `/favicon.ico` fallback added.
  - Login page (`/login`), proxy (`/api/*`, SSE `/api/admin/logs/stream`), and all auth endpoints are unchanged.

- `admin/templates/dashboard.html` — **deleted** (1178 lines of vanilla JS). `login.html` kept.

- `frontend/` — admin code removed entirely:
  - `frontend/src/app/(app)/admin/` (deleted)
  - `frontend/src/components/admin/` (deleted)
  - `frontend/src/hooks/use-admin-tables.ts` (deleted)

**Unchanged:**
- Backend CORS, cookie domain, JWT, CSRF — all untouched. The admin service reverse-proxies `/api/*` server-to-server, so the browser only ever talks to `admin.music.menghi.dev`. No cross-origin complications.
- Env var contract on the admin service: `ADMIN_PASSWORD`, `ADMIN_SECRET`, `BACKEND_URL`, `NOCODB_URL`, `PORT` — same as before.
- `admin.music.menghi.dev` URL.
- `/api/admin/*` endpoint surface on the backend.

**Decommissioning note:** the main frontend no longer exposes `/admin`. Visiting `music.menghi.dev/admin` will 404 from the Next.js app. Use `admin.music.menghi.dev` exclusively.

---

## V 6.330 — 2026-04-17

### Admin dashboard: songs + artists tables replace stale aggregate cards

The old admin page showed aggregate rollups (Pipeline Stages, Per-User Table, Recent Worker Activity) that didn't answer "which specific song / artist is missing what". Two new drill-down tables consume the `/api/admin/songs-table` and `/api/admin/artists-table` endpoints shipped in `67be61c` (V 6.322).

**Songs table** (`components/admin/songs-table-card.tsx`)
- Paginated (25 / 50 / 100 per page, prev/next controls)
- Columns: artwork (40×40 thumbnail) · title/artist · ESSENTIA · CLAP · MERT · ISRC · CACHED AUDIO · USER-LINKED
- Each status column renders an emerald (true) or red (false) dot via the new `StatusDot` primitive
- Click any row → inline details section with catalog_id + per-field booleans. Click again to collapse.
- Artwork rendered directly from `global_song_cache.artwork_url` (already pre-expanded to 300×300 server-side)

**Artists table** (`components/admin/artists-table-card.tsx`)
- Same pagination shape
- Columns: artist · source (library / cobweb / feat with icon + colored badge) · owning user email · stacked library/discovered bar (emerald = library, amber = discovery) · discovery %
- Source badges: 👤 library (emerald, `User` icon) · 🕸 cobweb (purple, `Network` icon) · 🤝 feat (amber, `Handshake` icon)

**Removed** (rolled into the new tables' drill-down):
- Pipeline Stages card — per-song dots answer the same question more granularly
- Per-User Enrichment table — artists table groups by artist (more useful than "user has X of Y")
- Recent Worker Activity card — superseded by V 6.322's natural-language orchestration logs in Railway

**Kept:**
- System Overview metric bar (5 cards: Users / Songs / Enriched % / Worker / Today)
- Diagnostic Insights panel
- Failure Analysis (today's enrichment failures by stage)

**New frontend files:**
- `src/hooks/use-admin-tables.ts` — TanStack Query hooks with `placeholderData` for smooth pagination
- `src/components/admin/status-dot.tsx` — reusable red/green/grey 2.5px dot
- `src/components/admin/songs-table-card.tsx` — songs table + expandable row
- `src/components/admin/artists-table-card.tsx` — artists table + source badge + stacked bar

Net: +3 files, ~268 lines removed from `page.tsx` (3 aggregate sections + dead imports + dead sub-components `PipelineStage` + `CellWithPct`).

---

## V 6.322 — 2026-04-17

### Natural-language startup logs + songs/artists admin endpoints

Uvicorn leaves `musicmind.*` loggers at WARNING, silently dropping backend orchestration narration on Railway. V 6.322 forces the `musicmind` namespace to INFO with a stderr handler on module import — startup logs now appear.

Orchestration messages rewritten as readable narration:

```
── Backend booted. Telling worker to: ──
  1. enrich 12 library songs missing audio features
  2. fill 995 global CLAP/MERT embeddings on the GPU
  3. run a cobweb + discovery cycle to grow the candidate pool
▶ Worker: starting library enrichment for 12 tracks
✓ Worker: library enrichment done — 12 tracks got audio features
...
```

New admin endpoints feeding V 6.330's dashboard rebuild:
- `GET /api/admin/songs-table?limit=N&offset=M` — per-song enrichment grid with has_essentia / has_clap / has_mert / isrc_ok / has_cached_audio / user_linked booleans. Joins per-user and global tables so a song enriched either way reads green.
- `GET /api/admin/artists-table?limit=N&offset=M` — per-artist source (library / cobweb / feat) + owning user + library/discovered/global counts + discovery_pct. Ordered by library_tracks DESC so top artists land first.

---

## V 6.321 — 2026-04-17

### GPU batching: bigger, concurrent, and two long-standing bugs fixed

Local Mac GPU logs showed a lot of `1 items processed` lines — trickle tracks getting their own 3-second GPU round-trip. Fixed in three ways:

#### Bigger + concurrent batches
- `gpu_batch_size` raised from 10 → 25 everywhere (per-user backfill, global backfill, orchestrator Phase 2). With CLAP-tiny + float16-MERT on MPS this is well within memory.
- New `enrich_bytes_concurrent` / `enrich_urls_concurrent` helpers in `gpu_client.py` split a flat list of items into chunks and dispatch up to `GPU_MAX_CONCURRENT=3` in parallel via `asyncio.gather` + semaphore. The local server pipelines audio decode of one batch with GPU inference of another, cutting wall time ~2-3x on larger queues.

#### Min-batch deferral
- `GPU_MIN_BATCH=3` sentinel: if the pending pool has fewer than 3 tracks, the GPU call is skipped and deferred to the next worker cycle where more tracks will likely be ready. Applies to orchestrator Phase 2, worker per-user backfill, and worker global backfill. Eliminates 1-item round-trips entirely.

#### Two latent bugs in `_backfill_gpu_embeddings_global`
- **Arg order**: `enrich_batch_bytes_via_gpu(modal_url, b64_items)` had the arguments swapped — it was being called with `modal_url` as the audio list and `b64_items` as the endpoint URL. Every global GPU call failed silently, which is why `audio_embeddings_global.clap_embedding` stayed at 0 for all 995 rows even when the GPU endpoint was up.
- **Key names**: the same function was reading `res.get("clap_embedding")` but the GPU handler actually returns `clap_512` / `mert_768`. Both keys are fixed in the refactor.

Expected impact on next redeploy:
- No more "1 items processed" log lines
- Existing 995 global tracks without CLAP/MERT will now actually get them (concurrent batches of 25)
- Tracks with cached audio bytes saturate the GPU: 75 items in flight per round-trip cycle vs 10 before

---

## V 6.320 — 2026-04-17

### Backend-orchestrated worker + unified discovery + artwork + score breakdown

Four coordinated fixes that make the recommendation pipeline coherent end-to-end. The backend now acts as the coordinator and the worker as the executor; discovery is unified into a single broader pass; artwork actually renders; the scoring breakdown matches the engine.

#### Artwork pipeline (migration 025)
- Added `artwork_url` column to `global_song_cache` — the shared song pool had no artwork field, so anything sourced from the worker (cobweb + discovery) returned an empty string to the frontend. All four write sites now populate it: `_run_discovery_for_user`, `_fetch_artist_songs_globally`, `_migrate_discography_to_global`, and `indexer._fetch_and_enrich_discography`.
- `_load_candidates_from_db` now selects and maps `artwork_url` → `artwork_url_template` on the result dict so `get_recommendations` forwards it to the API response.
- Migration backfills `artwork_url` from `song_metadata_cache.artwork_url_template` by catalog_id for the 995 existing global songs that lost artwork when the `_migrate_discography_to_global` ran in V 6.310.

#### Score breakdown (aligned with V 6.300 scoring)
- `get_scoring_breakdown` rewritten to match the actual scorer's 6 weighted dimensions (CLAP/MERT/EffNet/genre/scalar/artist) plus 6 modifiers (discovery bonus, cross-strategy bonus, calibration boost, mood boost, diversity penalty, staleness penalty).
- Falls back to `global_song_cache` + ISRC-joined global embeddings when a song isn't in the user's per-user tables, so discovery candidates can be inspected too.
- Uses `compute_context_weights` so reported weights reflect the user's effective scoring (e.g. redistributed when embeddings are missing), not the static `DEFAULT_WEIGHTS`.
- Frontend `ScoreBreakdown` component rewritten: separate "Weighted dimensions" section (6 bars) and "Modifiers" section (6 bars with +/- signed values scaled against their absolute cap).

#### Unified discovery (lifted caps, strategies internal)
- Worker's `_run_discovery_for_user` now uses `top_artists[:15]` (was 5), `top_genres[:10]` (was 5), `total_budget=200` for similar-artist crawl (was 40), and `limit=50` per-genre for genre-adjacent/editorial/chart (was 20-30).
- Removed the hard-coded `top_genres[:3]` cap inside `discover_genre_adjacent` and `discover_editorial` — the caller controls how many genres to walk.
- Expected steady-state candidate pool: ~1000–1500 per user per 6h cycle (vs ~200 before).
- Recommendations API: `/api/recommendations` no longer sends strategy/mood from the frontend (params remain accepted as no-ops for backward compat). Response still carries `strategy_source` per item as internal metadata.
- Frontend: `StrategySelector` and `MoodFilter` components removed from `RecommendationFeed`.

#### Backend startup orchestration (app.py lifespan)
- On backend boot, FastAPI lifespan inspects DB state and dispatches worker tasks as background work. Chain: `_fill_library_gaps` → `_backfill_isrcs` → `_backfill_gpu_embeddings` (+ `_global`) → `_run_cobweb_cycle`.
- Logs gap counts upfront so you can tell from the boot log whether there's work to do: `library_gaps=N, isrc_gaps=M, gpu_gaps=K`.
- `asyncio.create_task` — boot never blocks on enrichment. The worker process continues polling in parallel; both share the DB, so duplicate work is serialized by Postgres transactions.

---

## V 6.310 — 2026-04-16

### Worker-driven discovery (recommendations API becomes a read)

Previously the four discovery strategies (`similar_artist`, `genre_adjacent`, `editorial`, `chart`) ran live on every `/api/recommendations` request — burning Apple Music / Spotify rate limit per-user, blocking the response on network calls, and operating on tracks that hadn't been audio-enriched yet so most embedding dimensions silently fell back to genre-only scoring. Discovery now runs in the background worker and persists to a per-user candidate pool; the API just reads + scores.

#### New table: `recommendation_candidates` (migration 024)
- `(user_id, catalog_id, strategy_source)` PK so the same track surfaced by multiple strategies still drives the cross-strategy bonus
- Stores `discovery_weight` (positional × seed_affinity from Task 5 of the discovery overhaul) so it actually influences ranking
- Indexed on `(user_id, fetched_at)` for the refresh cadence query

#### Worker: `_run_discovery_for_user`
- Runs after each user's cobweb pass in `_run_cobweb_cycle`
- Reads latest `taste_profile_snapshots` for seed_scored + top genres
- Calls all 4 discover_* strategies × all connected services in parallel
- Upserts `global_song_cache` (metadata) + `recommendation_candidates` (per-user attribution)
- 6-hour cadence guard: skips if any candidate row was fetched in the last 6h
- Tagged log: `Discovery refreshed for <user>: N candidates upserted`

#### `RecommendationService.get_recommendations` rewritten
- Step 4 changed from "run live discovery" to "SELECT recommendation_candidates JOIN global_song_cache"
- Aggregates per catalog_id: `_strategy_count = #distinct strategies`, `_discovery_weight = max across strategies`, `_strategy_source = highest-weight strategy name`
- **Cold-start fallback:** if a user has no candidates yet AND has connected services → run live discovery for THIS request and fire `asyncio.create_task(_populate_candidates_background)` so the next request is instant
- Hard-fails 400 only when no candidates AND no connected service (test contract preserved)

#### Why this is better
- Recommendations endpoint latency drops from "live API roundtrip × 4 strategies × N services" to "one SELECT + score"
- Every candidate is fully enriched by the time the API sees it (worker has time to run Essentia + GPU between cobweb cycles), so `_discovery_weight`, embedding cosines, and the cross-strategy bonus all actually shape rankings instead of degrading to genre-only fallback
- API rate limit pressure is smoothed across worker cycles, not bursty per request
- Discovery and cobweb now share a single per-user processing model — one queue, one cadence

#### Admin endpoint
- `POST /api/admin/rebuild-taste-profiles` — bulk recompute snapshots for all users (also added in this release). After engine changes, stored snapshots reflected old math; this endpoint rebuilds them in the background without re-running indexer.

---

## V 6.300 — 2026-04-16

### Discovery Strategy Overhaul (affinity-weighted candidate selection)

The recommendation pipeline previously selected candidates via single-dimensional ranking with hard caps, throwing away the affinity scores the profile already computed. A user with 200 plays of one artist and a calibrated weight=5 on another saw the calibrated artist swamp the heavy-listened one. A user with 5 evenly-loved artists got their #4 and #5 enriched at 30% depth while #1 got 100%. This rebuild threads the affinity score end-to-end.

#### Indexer (artist depth and ranking)
- `_get_ranked_artists` now returns `list[tuple[str, float]]` — each artist paired with its normalized affinity score
- Pure helper `_rank_artists_by_affinity(freq_map, cal_weights)` extracted for unit testing without a DB fixture
- Calibration combined as a multiplicative modifier (`freq * (1 + 0.1 * cal_weight)`), not concatenated above frequency
- Calibration-only artists (no library plays yet) get a baseline frequency=1 so they still surface
- Original artist casing preserved through ranking — no more `SZA` → `Sza` mangling
- `STEP_DEPTHS` rank-cliff replaced with continuous `compute_depth_fraction(score)` clamped to [0.15, 1.0]
- Hard cap `max_other = clamp(library*0.2, 5, 30)` replaced with continuous `AFFINITY_INCLUDE_THRESHOLD = 0.05` (with min-3 fallback for sparse profiles)
- `_unlink_excess_discoveries` aligned with the new threshold so cleanup never deletes songs the indexer just enriched

#### Cobweb (suggested artist mining)
- New shared module `engine/cobweb.py` consolidates ranking previously duplicated in worker.py and indexer.py
- Aggregation switched from `max()` to `sum()` so a 10× featured artist correctly outranks a 1× one (`log1p` damps to ~4× ratio so super-collaborators don't swamp)
- Each feat contribution weighted by the primary artist's affinity score — feats on top-tier tracks count ~10× more than feats on tail-artist tracks
- Cap is now feat density (unique candidate count), not `library * 0.5`
- Co-primary artists in `A & B feat. C` properly excluded from the cobweb (was treating B as a feat of itself)

#### Profile (artist affinity computation)
- `build_artist_affinity` library presence now log-saturates: `min(3.0, 0.3 * log1p(count))`
- Prevents 50 unplayed library songs from outranking 3 recent plays
- `parse_artists` weight (1.0 primary, 0.3 feat) preserved through accumulation

#### Discovery strategies
- `discover_similar_artists` now accepts `list[tuple[name, affinity]]` and allocates per-seed track budget proportionally — affinity 1.0 seed gets ~20 candidates, affinity 0.2 seed gets ~4
- Each track tagged with `_discovery_weight = positional_weight * seed_affinity`
- Scorer consumes `_discovery_weight` as an additive bonus capped at +0.04
- `discover_chart_filter` now uses `expand_genres` for parent/regional near-match — "Hip-Hop/Rap" chart tracks no longer rejected by an "Italian Hip-Hop/Rap" profile

#### Worker compute savings (helps local-Mac GPU mode)
- Cobweb tracks pre-filtered by EffNet embedding cosine to user centroid before enrichment dispatch
- Top 70% kept, bottom 30% skipped — reduces GPU calls when global EffNet embeddings already exist
- User centroid loaded once per cobweb cycle (was up to 5 redundant queries)
- EffNet embedding validation pinned to the actual 1280 dim, not a permissive `> 10` guard

#### ISRC backfill (retry loop fix)
- Worker no longer retries the same ~115 unfindable tracks every cycle
- Tracks whose Deezer + MusicBrainz lookups both miss are marked with sentinel `'__NO_ISRC__'` and excluded from future backfill queries
- Transient lookup exceptions still leave rows for next-cycle retry
- Manual recovery: `UPDATE <table> SET isrc = NULL WHERE isrc = '__NO_ISRC__';`

#### Test coverage
- 8 new test files covering each layer: artist affinity, indexer ranking, cobweb ranking, cobweb prefilter, discovery budget, discovery weight bonus, chart filter genres
- 34 new unit tests, all pure (no DB fixtures required)

#### Plan + execution artifacts
- Implementation plan saved at `docs/superpowers/plans/2026-04-16-discovery-strategy-overhaul.md`
- 9 atomic commits on `staging`, plus a clean revert + re-apply of one stale-checkout commit (incident captured here as a reminder to verify branch state before applying follow-up commits)

---

## V 6.200 — 2026-04-15

### Pipeline Reliability & Worker Hardening

#### GPU Pipeline Fix (CLAP + MERT were at 0% coverage)
- **Root cause**: Phase 2 (Modal GPU) used stale in-memory preview URLs from expired Deezer CDN links. Phase 1 (Essentia) refreshed them in the DB, but Phase 2 never re-read
- **Fix**: Phase 2 now sends cached audio bytes directly to Modal instead of URLs — completely eliminates URL expiry failures
- **Bytes-first GPU enrichment**: `preview_audio_cache` bytes → base64 → Modal. URL fallback only for uncached tracks
- **New Modal endpoint**: `enrich_track_from_bytes` / `enrich_batch_from_bytes` — accepts base64-encoded audio, skips download
- **GPU batching**: Split from single mega-batch into chunks of 10 (avoids Modal timeout on large catalogs)
- **GPU client logging**: Failures now log as WARNING (was debug — completely invisible in production)

#### IntegrityError Infinite Loop Fix
- **Problem**: 13 discovery tracks failed with IntegrityError every 60s cycle, generating ~300 log entries/day
- **Fix**: Module-level `_failed_tracks` dict tracks failures per track. After 3 failures, track is marked `permanently_failed` in DB and skipped. Resets every 50 cycles to allow retries after code fixes
- **Batch exception handlers**: Both `_fill_library_gaps` and `_enrich_global_songs` now catch batch-level failures and increment per-track counters
- **IntegrityError safety net**: `_store_embedding` catches IntegrityError explicitly (logs warning, continues)

#### Preview Audio Cache (new table)
- **`preview_audio_cache`** table (migration 023): stores downloaded 30s preview audio bytes
- Orchestrator checks cache before downloading, caches after download
- Marks `enrichment_complete = true` after all enrichment stages pass
- Worker Phase 0a cleans up completed + stale (>7 day) entries
- Solves Deezer preview URL expiry (URLs expire ~24h, audio bytes persist)

#### ISRC Backfill (free APIs)
- **`isrc_lookup.py`**: Deezer (fast, no key) + MusicBrainz (fallback, 1 req/sec) ISRC resolution
- **Worker Phase 1c**: Backfills missing ISRCs for `song_metadata_cache` and `global_song_cache` each cycle
- ISRC is the global dedup key for sharing enrichment across users

#### Dead Code Cleanup
- Deleted `worker/enrichment_worker.py` (619 lines, old ReccoBeats worker)
- Deleted `worker/requirements.txt` (unused, Dockerfile uses backend/pyproject.toml)
- Deleted `worker/proxy.txt` (proxy list for old worker)

---

## V 6.100 — 2026-04-10

### Full Audio Intelligence Stack
- **Essentia classifier heads**: mood (aggressive/happy/party/relaxed/sad), voice/instrumental, acoustic — run on CPU from EffNet embedding
- **Modal GPU worker**: CLAP 512-dim + MERT 768-dim serverless on A100, scale-to-zero (~$0.018/track)
- **GPU client**: Railway→Modal HTTP integration for Tier 2 enrichment
- **OpenAI GPT-5.4 explanations**: track captions + "why you'll like this" from structured tags
- **Multi-signal similarity**: 0.30 CLAP + 0.25 EffNet + 0.20 MERT + 0.10 mood + 0.10 scalar (adaptive weights)
- **Natural language search**: GET /api/search?q="aggressive drill energy" via CLAP text-to-audio
- **Tag-to-caption pipeline**: AI captions generated during enrichment, stored per-song
- **DB migration 020**: CLAP/MERT embedding columns, ai_caption, ai_tags, profile centroids

---

## V 6.000 — 2026-04-10

### Local Audio Intelligence (Essentia + ONNX)

Major architecture change: audio enrichment moves from external APIs to local processing.

#### Replaced ReccoBeats + SoundStat with Essentia
- **Local analysis**: Essentia + ONNX Runtime replaces the Deezer→ReccoBeats→SoundStat API chain
- **DSP scalar features**: BPM (RhythmExtractor2013), key/scale (KeyExtractor), energy (RMS), danceability, brightness (SpectralCentroid), loudness (EBUR128) — all extracted locally
- **1,280-dim EffNet embeddings**: Discogs-EffNet ONNX model (~18MB) produces rich embeddings trained on 2M+ recordings. Captures genre, mood, timbral qualities that scalar features miss
- **Graceful fallback**: If Essentia unavailable (e.g., CI), ReccoBeats is used as fallback
- **Zero API cost**: No more ReccoBeats uploads or SoundStat charges

#### Hybrid Scoring Pipeline
- **0.7 embedding + 0.3 scalar**: `combined_audio_similarity()` (existed but was unused) now wired into `score_candidate()`
- **Embedding centroid**: `build_taste_profile()` computes L2-normalized mean of all library track embeddings
- **Per-candidate embeddings**: `rank_candidates()` passes embeddings through to scoring
- **Backward compatible**: 128-dim legacy embeddings still work, 1,280-dim preferred

#### New Infrastructure
- **essentia_extractor.py**: Single-responsibility module — audio bytes in, features + embedding out
- **audio_embeddings_global**: ISRC-keyed table for cross-user embedding sharing
- **Migration 019**: embedding_dim column, global embeddings table, taste profile centroid
- **Docker**: ffmpeg + Essentia + ONNX Runtime, model downloaded at build time
- **Optional deps**: `[audio]` group in pyproject.toml

#### GPU Worker Architecture Prepared (Tier 2, future)
- Architecture supports external GPU worker (RunPod Serverless) for CLAP, MERT, Music Flamingo
- ~$0.018/track on A100 40GB, scale-to-zero when idle
- Not implemented in this version — Essentia on CPU is sufficient for current catalog

---

## V 5.300 — 2026-04-09

### Unified Per-Song Enrichment Pipeline
- **Full pipeline per song**: `enrich_tracks()` now runs audio → tags → credits for EACH song before moving to the next. Tags + credits run concurrently within each song. 15 songs processed in parallel.
- **No more sequential stages**: Previously: all audio for all songs, then all tags, then all credits. Now: song1(audio+tags+credits), song2(audio+tags+credits), ..., all concurrent.
- **Tags + credits built into orchestrator**: New `_enrich_tags_single()` and `_enrich_credits_single()` functions with cache-first checks (no duplicate API calls).
- **Worker partial completion simplified**: Uses the same orchestrator functions. Semaphore(15) for high throughput.
- **Indexer updated**: `_enrich_library_songs` and `_fetch_and_enrich_discography` pass `lastfm_api_key` — tags+credits happen during indexing, not as a separate backfill.
- **Removed redundant `_backfill_tags_credits`** calls from indexer (now handled by unified pipeline).

---

## V 5.230 — 2026-04-09

### User-Linked Priority Gate
- **Cobweb only after user work done**: Worker checks `_count_user_linked_gaps()` — counts songs missing audio, tags, or credits across all users. If gaps remain, skips cobweb/global phases and loops back to user-linked work with 10s pause instead of 120s sleep.
- **No sleep while work remains**: Worker only sleeps the full `POLL_INTERVAL` (120s) when all user-linked songs are fully enriched. Otherwise: 10s short pause → retry.
- **Cobweb is NOT user-linked**: Cobweb-discovered artists and their songs are global (no user_id). They only get processed when user work is complete.

---

## V 5.220 — 2026-04-09

### Smart Enrichment Priority + Artist Cap
- **Partial enrichment completion phase**: New worker phase runs BEFORE cobweb expansion. Songs with audio but missing tags/credits get completed first (Semaphore(15) for Last.fm, 200/cycle for MusicBrainz). 880 songs with audio but no tags will now be addressed.
- **Indexer artist cap**: Step 5 ("other library artists") now capped at top 20% (max 30) instead of ALL artists. 182 artists → 3 + ~36 = 39 processed (was 182 creating 2362 discography songs).
- **Worker cycle order**: library gaps → ISRC retry → ISRC backfill → **complete partial tags/credits** → cobwebs → global → backfill → sleep.
- **Tags concurrency 15**: Last.fm allows ~20 req/s. Semaphore raised from 10 to 15 in the partial completion phase.
- **Batch pre-filtering**: All backfill phases check existing cache in single batch query before making any API calls. No duplicate requests.

---

## V 5.210 — 2026-04-09

### Performance + Enrichment Recovery
- **3x concurrency**: Enrichment CONCURRENCY 5→15, BATCH_SIZE 20→50, Last.fm/ISRC backfill Semaphore 5→10, MusicBrainz cap 100→200/cycle. Uses 8 vCPU available on Railway.
- **Deezer ISRC lookup**: New primary lookup strategy — tries `/track/isrc:{ISRC}` first (exact match), falls back to name search. 153 previously-failed songs with ISRC can now be found.
- **Reset failed songs on startup**: Cycle 1 clears `no_data_available` markers for songs that have ISRC, giving them a second chance with the new ISRC lookup.
- **Orchestrator passes ISRC to Deezer**: Both per-user and global enrichment now send ISRC to `fetch_deezer_features()`.
- **Last.fm backfill cap**: 500→1000/cycle (faster with Semaphore(10)).

---

## V 5.200 — 2026-04-09

### Reliability Sprint — 32 Fixes Across 20 Files

#### Enrichment Priority & Smart Worker
- **Library-first enrichment**: Worker now fills user library gaps BEFORE cobweb/global songs. If 218 library songs are missing, exactly those 218 get enriched first.
- **ISRC backfill phase**: 807 library songs had NULL ISRC. New worker phase resolves ISRCs via Deezer search (100/cycle). Deezer track response now extracts ISRC.
- **Cobweb total cap**: Artist cobweb capped at 50% of library artists (was unbounded — grew to 194 for 446 library songs).
- **Worker yields to active indexing**: Checks `user_indexing_status` before processing each user. No more API competition between worker and backend indexer.
- **Skip permanently failed enrichments**: Songs with `no_data_available` marker no longer retried every cycle.
- **Indexer WHERE clause fix**: Last.fm suggested artists query now filters by THIS user's library artists (was querying all users' data globally).
- **Last.fm tags UPSERT**: `ON CONFLICT DO UPDATE` replaces `DO NOTHING` — stale tags now refresh on re-fetch.

#### Scoring & Recommendation Engine
- **Collab boost continuous**: Was binary (0 or 1) due to `*5.0` scaling. Now proportional 0.0-0.20.
- **Weight redistribution renormalized**: When audio/tags unavailable, redistributed weights now renormalize to sum=1.0 (prevented score inflation).
- **Regional weights proportional**: 90% Italian = 40% language weight (was capped at 22.5%). 10% global = 3% language. Continuous scaling via `(strength - 0.3)^1.5`.
- **MMR diversity fix**: Base score was computed without diversity, but adjustment subtracted `diversity_weight * 1.0` — incorrectly penalizing every candidate. Now simply subtracts `diversity_weight * penalty`.
- **Mood genre matching**: Uses word-boundary/slash-split matching instead of substring containment. "Pop" no longer matches "Rock Pop".
- **Audio centroid NaN filtering**: NaN values from bad API data no longer corrupt the weighted centroid.
- **Genre normalize None**: Returns `""` instead of `None` (prevented downstream `.lower()` crashes).
- **Library filter NULL dates**: Songs with NULL `date_added_to_library` treated as old (allow rediscovery) instead of assumed recent (excluded).

#### Spotify Support
- **Token auto-refresh in worker + indexer**: Spotify tokens expire in 1 hour. Both now check `token_expires_at` and refresh via `refresh_spotify_token()` before API calls.
- **Market detection**: Fetches user's `country` from Spotify profile and uses it as `market` parameter. Italian Spotify users get Italian charts.
- **Editorial discovery fix**: Uses `genre:rock year:2024-2026` Spotify search syntax instead of `"best new rock"` (literal text match).
- **Genre backfill**: `discover_genre_adjacent` batch-fetches artist genres via `/artists?ids=` endpoint. Spotify tracks now get proper genre metadata.
- **Top-tracks market parameter**: Required `market` param added per Spotify API spec.

#### Infrastructure
- **DB indexes (Alembic 018)**: Standalone `user_id` indexes on 8 tables. Per-user queries go from O(n) table scan to O(log n) index lookup.
- **Connection pooling**: Shared `httpx.AsyncClient` with `max_connections=20` across all discovery strategies. Was creating 4+ separate TCP+SSL connections per recommendation request.
- **Retry on all API calls**: `_request_with_retry()` (was defined but never called) now wired into all 13 API call sites. Exponential backoff on 429/5xx.
- **Async-safe indexing locks**: `asyncio.Lock` per user replaces plain `dict[str, bool]` (had TOCTOU race condition).
- **Taste rebuild status persisted**: Moved from in-memory `_rebuild_status` dict to `user_indexing_status` table (step=99). Survives server restarts.
- **Token decryption safety**: `decrypt()` raises `ValueError` with clear message. New `decrypt_or_none()` for background tasks. Worker/indexer handle corrupted tokens gracefully.
- **Admin N+1 fix**: `get_enrichment_progress()` replaced 8N queries (8 per user in loop) with 5 batched GROUP BY queries.
- **12+ silent `except: pass`** replaced with `logger.debug/warning` calls.
- **Cobweb stats use rowcount**: Only counts actual INSERTs, not CONFLICT DO NOTHING no-ops.

#### Logging
- **DatabaseLogHandler**: New `logging.Handler` subclass forwards all Python logs to the log DB. WARNING+ from all loggers, INFO+ from `musicmind.*` namespace. Wired into both worker and main app.
- All logs visible on Railway are now also persisted to the `error_logs` table.

#### Admin Dashboard
- **New `/api/admin/diagnostics` endpoint**: Per-user, per-stage enrichment breakdown with failure analysis.
- **Smart Diagnostics UI section**: Shows per-user cards with enriched/failed/pending/missing-ISRC counts. Library vs non-library audio split. Actionable insights (CRITICAL/WARNING/INFO). Today's failure breakdown from logs DB.

#### Frontend
- **localStorage key unified**: `use-chat.ts` now reads/writes `musicmind-preferred-model` (was split between two keys — model selection in Settings didn't persist to Chat).

---

## V 5.110 — 2026-04-08

### Fixed — Tags + Credits Not Completing
- **Root cause**: Gap detection used O(n) individual queries per song. With 3,100 songs = 3,100+ SELECT queries just to find what's missing. Now uses single batch `IN` query.
- **Last.fm backfill now concurrent**: 5 simultaneous API calls via `asyncio.Semaphore(5)` instead of sequential. ~5x faster.
- **Worker backfills ALL songs** (per-user + global) not just 200 global songs per cycle.
- **MusicBrainz gap detection batched**: single `IN` query for all ISRCs instead of per-song check.

---

## V 5.100 — 2026-04-08

### Added
- **Indexer auto-trigger on login**: `/api/auth/me` checks if user's library is fully indexed. If not, triggers `run_indexing()` as background task. Skips if already complete or in progress (in-memory lock per user).
- **Orchestrator respects env vars**: `WORKER_CONCURRENCY` and `WORKER_BATCH_SIZE` now configurable via environment (was hardcoded 5/20). Set to 15/100 on Railway for 8 vCPU.

---

## V 5.000 — 2026-04-08

### Architecture Split — Indexer vs Worker

Major refactor separating enrichment into two independent systems:

#### Per-User Indexer (`indexer.py` — NEW)
Backend-managed, prioritized, user-scoped enrichment pipeline:
1. **Library songs** — enrich all songs in the user's library (100%)
2. **Top artist** — 100% discography enriched
3. **2nd artist** — 70% discography
4. **3rd artist** — 50% discography
5. **Other library artists** — 30% discography each
6. **Suggested artists** — `library_artists * 0.4` new artists from feats + similar tracks, 50% discography

Artists ranked by calibration weight then play frequency. All results stored with user_id in per-user tables.

#### Global Worker (`worker.py` — REWRITE)
Standalone Railway service, always running, global enrichment:
- Builds **artist cobweb** per user (from feats, Last.fm similar, genre overlap)
- Enriches each cobweb artist's **top 50 songs** globally — no user_id
- Stores in `global_song_cache` + `audio_features_global` (ISRC-keyed)
- Promotes featured artists who appear alongside library artists
- Caps discovered artists at `library_artists * 0.4` per user
- Results available to ALL users for recommendation scoring

#### New Tables (migration 017)
- `user_indexing_status` — tracks per-user indexing step/progress
- `artist_cobweb` — per-user artist network (source, priority, enriched status)
- `global_song_cache` — worker-discovered songs without user_id

#### Enrichment Pipeline
- Now **3 stages** (lyrics embeddings removed): audio features → Last.fm tags → MusicBrainz credits
- New `enrich_tracks_global()` in orchestrator — writes only to `audio_features_global`, skips per-user cache
- Both systems check caches before API calls — no duplicate requests

#### Admin Dashboard
- Per-user progress shows indexing step indicator + cobweb stats
- Worker status shows global cobweb building phase
- "Discography" label replaces "Worker" in per-user breakdown

---

## V 4.200 — 2026-04-08

### Added — Worker Heartbeat
- **`worker_status` table** (migration 016): Single-row table the worker updates with current phase, progress, cycle count, and timestamp. Written to the main DB so the admin dashboard can read it directly.
- **Live worker status panel**: Visual status bar showing current phase (cleanup/startup_scan/backfill/discovering/idle) with colored pulse dot, cycle count, detail text, and progress bar when available. Updates every 10s via dashboard polling.
- **Worker heartbeat calls**: `_set_status()` called at every phase transition — cleanup, startup scan, backfill, discovering, idle.

### Fixed
- **`/api/admin/status` 5-7s SLOW**: Was calling `get_enrichment_progress()` (expensive per-user orphan-proof subqueries). Now uses fast direct counts in a single connection. Should be <100ms.
- **Live logs only showed backend** — worker is a separate process, its logs never reached the backend's `AdminLogHandler`. The heartbeat system solves this — worker reports phase/progress to the main DB, dashboard reads it directly.

---

## V 4.140 — 2026-04-08

### Changed — Admin Dashboard
- **Cookie login persistence**: Login survives admin service restarts — uses HMAC-signed cookie derived from ADMIN_PASSWORD instead of in-memory session tokens. Cookie valid for 30 days.
- **Live logs enhanced**: Worker activity highlighted with colored left border (blue for enrichment, yellow for scans). Song list entries indented. Message counter shows total received. Log buffer increased to 500 lines. Log feed height increased to 400px.
- **Auto-refresh on worker events**: Dashboard data auto-refreshes 2s after detecting worker cycle completion or backfill in the live log stream (no more waiting for the 10s poll).
- **Polling reduced**: 30s → 10s for KPI/progress data.

---

## V 4.130 — 2026-04-08

### Changed
- **Removed lyrics embeddings** from enrichment pipeline — 3-stage pipeline now (audio features → Last.fm tags → MusicBrainz credits). Lyrics/Genius/sentence-transformers deferred to future phase.
- **Backfill runs every cycle** — partially enriched songs (have audio but missing tags/credits) get completed on every worker cycle, not just startup. This ensures the 1,193 partially enriched songs get their Last.fm tags and MusicBrainz credits filled.
- **Pipeline breakdown shows 3 stages**: "Fully enriched" now means all 3 stages complete (audio + tags + credits).

---

## V 4.120 — 2026-04-08

### Fixed — Data Integrity
- **Enrichment count mismatch (407% bug)**: `audio_features_cache` had orphaned rows from the 148K song cleanup — songs were deleted from `song_metadata_cache` but their audio feature rows remained. Enriched counts now only count songs that still exist (JOIN with song_metadata_cache). Root cause: the cleanup SQL only targeted one table.
- **Enrichment percentage capped at 100%**: Both backend and frontend now cap at 100% instead of showing impossible values like 407%.
- **Negative unenriched count**: `unenriched` now uses `max(0, ...)` to prevent negative display values.

### Added
- **Orphan cleanup**: Worker runs `cleanup_orphaned_features()` on startup — deletes `audio_features_cache` rows with no matching song. Audio data is already preserved in `audio_features_global` (by ISRC). Also available as `POST /api/admin/cleanup-orphans` with button in admin dashboard.
- **Library vs discovered artists**: Per-user progress now shows "Artists: X library + Y discovered" instead of a single total count.
- **Orphan count display**: Per-user progress shows orphan count in red when > 0.
- **Worker logging**: `_startup_scan` and `_backfill_new_signals` now write to `enrichment_logs` (previously only `_process_user` logged). Every cycle logs even when idle. Worker status should now always show activity.
- **Worker status diagnostics**: When worker status shows "no logs", the dashboard lists possible causes (missing env vars, deployment, connectivity).
- **Cleanup button**: "Clean N orphans" button appears in per-user enrichment section when orphaned rows exist.

---

## V 4.110 — 2026-04-08

### Added — Admin Dashboard Overhaul
- **Enrichment pipeline breakdown**: unenriched / partially enriched / fully enriched counts + percentages. Per-stage progress bars (Audio Features, Last.fm Tags, MusicBrainz Credits, Lyrics Embeddings).
- **Worker status panel**: last activity timestamp, enriched/failed today counts, recent enrichment entries with stage + result + duration.
- **Live log feed**: SSE-powered real-time log stream from backend. Dark terminal theme with color-coded log levels. Auto-scrolls, reconnects on disconnect.
- **DB capacity footer**: PostgreSQL database size for main DB and logs DB, shown as percentage bars (of Railway 5GB plan).
- **SSE proxy in admin service**: dedicated streaming route for `/api/admin/logs/stream` — SSE can't go through the buffered catch-all proxy.

### Backend
- `GET /api/admin/enrichment-breakdown`: pipeline-level counts across 4 enrichment stages
- `GET /api/admin/db-capacity`: `pg_database_size()` for both databases
- `GET /api/admin/worker-status`: enrichment_logs queries for worker activity

---

## V 4.100 — 2026-04-08

### Fixed
- **Worker snowball bug**: queried ALL songs (including worker-discovered) for artist names, causing exponential growth (446 library songs → 148K worker songs → 78K artists). Now only queries library songs (library_id or date_added_to_library).

### Changed
- **Worker featured artist depth**: primary library artists get full depth (25 tracks), featuring artists from parsed names get 3 tracks only. Builds a focused enrichment database instead of infinite crawl.
- **Worker logging**: shows "120 library artists → 85 primary + 35 featured" breakdown

## V 4.000 — 2026-04-08

### Added — Recommendation Engine Overhaul
- **Phase 1 — Last.fm integration**: Tags (crowd-sourced mood/vibe vectors) + track.getSimilar (collaborative filtering from billions of scrobbles). New lastfm_similar_tracks table. Tags cached in lastfm_tags_cache. Worker enriches all tracks.
- **Phase 2 — MusicBrainz producers**: Recording-level credits (producer, songwriter, mixer, engineer) fetched via ISRC → MBID. Stored in kg_artists + kg_relationships. Enables "producer proximity" scoring.
- **Phase 3 — Genius lyrics embeddings**: Scrape lyrics from Genius (free, no OAuth), embed with all-MiniLM-L6-v2 (384-dim, local). Stored in audio_embeddings (model_version="lyrics-minilm-v2"). Captures semantic content for Italian rap/drill.
- **Phase 4 — Smart polling**: Apple Music ratings (Love/Dislike), Spotify saved status check, smart play count delta detection between polling snapshots.

### Changed — Scorer Rebalanced to 6 Dimensions
- genre 25%, tags 15%, collab 10%, audio 20%, artist 15%, language 15%
- Tag similarity: cosine between Last.fm tag vectors
- Collaborative match: +0.20 boost when candidate in Last.fm getSimilar set
- Unused dimensions redistributed proportionally (graceful degradation)
- Context-adaptive weights updated for 6 dimensions
- Mood mode boosts audio (30%) + tags (25%)
- sentence-transformers as optional dependency (`pip install .[lyrics]`)

## V 3.310 — 2026-04-08

### Added
- Standalone admin dashboard service at admin.music.menghi.dev (FastAPI + static HTML)
- Admin password auth (ADMIN_PASSWORD env var, independent from main app accounts)
- X-Admin-Secret header auth for backend admin endpoints
- Per-user library vs worker song breakdown in admin stats
- Worker song enumeration logging before enrichment starts
- Global ISRC audio features cache (audio_features_global table, migration 013)
- Standalone enrichment worker with startup scan + featuring artist parse
- NocoDB at dbmanager.music.menghi.dev for raw database browsing

### Changed
- Dashboard loads instantly (<1s) — stale profile served immediately, refresh in background
- Enrichment bar only visible during calibration indexing (worker enrichment invisible)
- Taste profile staleTime 5min → 30min, services staleTime 0 → 5min
- Enrichment polling 15s → 30s, removed /me polling for enrichment
- enrichment-status counts library songs only (worker songs excluded from user view)
- Admin UI redesigned with light theme, Inter font, proper CSS variables
- Removed is_admin flag dependency from main frontend (admin is separate service)

### Fixed
- 5-minute dashboard load caused by blocking profile rebuild on stale cache
- useServices refetching on every navigation (staleTime was 0)

## V 3.240 — 2026-04-08

### Added
- NocoDB admin UI on Railway for browsing/querying both databases
- Separate PostgreSQL logging DB with batched async writer (request_logs, enrichment_logs, error_logs)
- Request logging middleware — every request logged with method, path, status, duration_ms, user_id
- Frontend error boundary (error.tsx) with retry button
- Global mutation error toast via MutationCache.onError
- 429 retry with exponential backoff in discovery strategies (_request_with_retry)
- Rate limits: taste 20/min, calibration 10/min, profile refresh 5/min

### Fixed
- Deezer call signature crash in analyzer.py (positional arg to keyword-only function)
- Spotify candidates scored 0 on genre/language (empty genre_names never backfilled)
- 18 httpx.AsyncClient() calls missing timeouts (infinite hang risk)
- 3 bare `except: pass` in enrichment pipeline replaced with debug logging
- Enrichment orchestrator unused variable removed

## V 3.130 — 2026-04-08

### Fixed
- Apple Music OAuth popup silently blocked — pre-load MusicKit JS + developer token on mount
- Apple Music OAuth silent failure — removed hard redirect from apiFetch 401 handler
- Per-step error toasts in Apple Music connect flow (token fetch, SDK load, configure, authorize)

## V 3.100 — 2026-04-07

### Added
- Context-adaptive scoring weights — shift based on user profile (regional concentration, audio availability, calibration, mood)
- Play count proxy — Apple Music workaround: seen_count from recently-played polling, up to 10x weight
- Engagement-based profile weighting (songs played recently get 2x recency boost)
- Calibration boost: continuous function min(0.20, weight * 0.03), zeroed on genre mismatch

### Changed
- Scoring weights rebalanced: genre 35%, audio 25%, artist 20%, language 20%
- Language match: non-regional songs score 0.5 (was 0.2 penalty)
- Layout renders immediately with skeleton UI (was blocking auth spinner)
- Enrichment polling 3s → 15s, /me calls 30s → 60s
- Calibration status staleTime 30s → 5min

## V 2.900 — 2026-04-01

### Added
- Onboarding taste calibration wizard: 3-step flow (playlists, artists drag-to-reorder, songs)
- Calibration API: GET /albums, GET /artists, POST /save, GET /status, GET /entries
- user_calibration DB table + Alembic migration 012
- CalibrationManager component in settings (view/edit selections)
- Dashboard calibration summary card
- Calibration-aware recommendation scoring
- Top 3 artists trigger background discography fetch + enrichment

### Fixed
- Calibration save timeout — profile rebuild moved to background task
- Calibration duplicate key crash — dedup by (calibration_type, item_id)
- Calibration redirect race — setQueryData instead of invalidateQueries

## V 2.800 — 2026-04-01

### Added
- Standalone enrichment worker with full artist discography + outlier detection
- 5x faster enrichment via concurrent processing (asyncio.Semaphore)
- Proxy rotation + Retry-After handling in worker
- Auto-load proxies from URLs + Geonode API

### Fixed
- Failed tracks retry on next cycle instead of being permanently skipped
- Split collab artist names for Deezer search (21/140 → ~100+/150)
- ReccoBeats always direct (no proxy), concurrency 10 to avoid 429
- All Deezer requests go direct (proxies killing 138/140 artists)
- Worker handles genre_names stored as scalar strings
- Cast genre_names to text for GROUP BY (json has no equality operator)
- Count only real enrichments + dedup in worker
- Add greenlet to worker requirements

## V 2.500 — 2026-03-31

### Added
- Two-pass scoring: fast metadata pass → lazy audio enrichment → re-score
- User-visible enrichment progress bar with auto-continue
- Indexing status in progress bar with done/total counter
- Listening-based artist affinity (library 0.3, play 3.0, repeat 5.0, love +4.0)
- Library exclusion from recommendations (2-year window for rediscovery)
- Artist discography pre-caching for top 10 artists
- Transparent logos + color favicon

### Fixed
- Enrichment no longer gets stuck on unfindable tracks
- Enrichment batch increased to 50
- OOM crash + ReccoBeats rate limiting

## V 2.200 — 2026-03-31

### Added
- ReccoBeats audio analysis — upload 30s preview, get 9 features (free)
- Auto-enrich library on login and service connection
- Admin system with live SSE log stream, enrichment progress, dev/user toggle
- Sidebar recommendations, playlist scroll + per-playlist recs

### Changed
- Scorer rebalanced: language 45%, audio 32%, genre 13%, artist 10%
- Deezer enrichment via search (ISRC lookup broken)

### Fixed
- Docker build — revert Hatch dynamic version to static
- Stricter genre filtering, distinct chart colors

## V 2.100 — 2026-03-30

### Added
- Audio enrichment pipeline: Deezer → ReccoBeats → SoundStat (3-tier cascade)
- Featuring artist weighting in taste profile (primary 1.0, feat 0.3)
- Dashboard visualizations: genre donut, artist bars, audio radar, release year chart
- Listening timeline with chronological song view and date badges
- On-demand Essentia deep analysis (128-dim Discogs-EffNet embeddings)
- Playlists page with real Apple Music/Spotify playlists + per-playlist recs
- Synesthesia visual identity — purple/violet theme, Sora + DM Sans fonts

### Fixed
- Frontend strategy names aligned with backend validation
- Apple Music listening stats no longer empty
- Recommendations respect user's regional storefront

## V 2.000 — 2026-03-30

### Added
- Rebrand MusicMind → SmarTaste
- Centralized VERSION file
- Comprehensive what.md documentation
- Vercel + Railway deployment support
- Base64-encoded Apple .p8 key support

### Removed
- ~1,350 lines of dead code (bandit, CLAP mood, Last.fm, knowledge graph, extractor)

### Fixed
- Railway PostgreSQL connection failure
- Auto-generate FERNET_KEY and JWT_SECRET on first deploy
- Missing rate_limit.py module crash
- Dockerfile Python 3.14 for uuid7

## V 1.200 — 2026-03-28

### Added
- Multi-LLM support: Claude + OpenAI GPT-4o (Phase 12)
- LLM provider abstraction with tool converter
- OpenAI BYOK key manager + model selector in frontend

### Fixed
- Chat system prompt crash — top_artists is list of dicts
- Nested button hydration error in conversation sidebar
- Model selector dropdown stays open

## V 1.100 — 2026-03-28

### Added
- MusicKit JS Apple Music authorization flow
- BYOK key setup UX with link to Anthropic Console

### Fixed
- CORS, API proxy, sandbox mode, missing migration, empty config guards
- MusicKit JS v3 initialization (musickitloaded event + async configure)
- Comprehensive production fixes for all features
- Hosting/tunnel issues for Cloudflare deployment
- CSRF exemptions for Apple Music connect, Spotify callback, chat
- Disabled CSRF middleware (redundant behind same-origin proxy)

## V 1.000 — 2026-03-27

### Added
- Full webapp: 12 phases of development
- Phase 01: Monorepo structure, Settings, EncryptionService
- Phase 02: Auth system (signup, login, logout, refresh, me)
- Phase 03: Spotify + Apple Music service connections (OAuth PKCE, MusicKit JS)
- Phase 04: Claude BYOK (bring your own key) with Fernet encryption
- Phase 05: Taste profile pipeline (fetch → cache → compute → snapshot)
- Phase 06: Listening stats (top tracks, artists, genres by time period)
- Phase 07: Recommendation engine (4 discovery strategies, MMR scoring)
- Phase 08: Multi-service unification (ISRC dedup, genre normalization)
- Phase 09: Claude chat with agentic loop, SSE streaming, tool registry
- Phase 10: Score breakdown + audio features detail views
- Phase 11: Next.js frontend (dashboard, recommendations, playlists, chat, settings)
- 23 PostgreSQL tables, 12 Alembic migrations, 370+ tests

### Infrastructure
- FastAPI + SQLAlchemy Core + asyncpg
- Next.js 16 + React 19 + Tailwind 4 + shadcn/ui
- Cookie-based JWT auth with automatic token refresh
- Background library sync + enrichment pipeline

## V 0.200 — 2026-03-26

### Added
- Adaptive recommendation engine with feedback, audio analysis, mood filtering
- Regional genre handling, weight rebalancing, discovery noise reduction

## V 0.100 — 2026-03-25

### Added
- Initial MCP server: Apple Music API client, SQLite persistence
- 21 MCP tools for library, catalog, playback, management
- Taste engine with profile builder, scorer, discovery strategies
- Smart recommendation tools (taste, discover, smart playlist)
