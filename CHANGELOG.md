# Changelog

All notable changes to SmarTaste are documented here.

Versioning: **V X.YZA** where X=major, Y=small logic, Z=minor, A=bugfix.
When A reaches 10 → Z+1 (A resets to 0). When Z reaches 10 → Y+1. Etc.

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
