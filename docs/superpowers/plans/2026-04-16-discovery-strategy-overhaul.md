# Discovery Strategy Overhaul — Affinity-Weighted Selection

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the "single-dimensional ranking with hard caps" antipattern across the recommendation pipeline. Replace rank-based truncation with affinity-weighted continuous allocation so users with peaked listening distributions get concentrated recommendations and users with flat distributions get spread-out ones — without hand-tuned percentages.

**Architecture:** The profile already computes `top_artists[i].score` (normalized 0–1 affinity) and `genre_vector[g]` (normalized probabilities). Today, `indexer.py`, `_build_user_cobweb`, and the four discovery strategies in `fetch.py` all throw these scores away and use rank index or fixed truncation. This plan threads the affinity score end-to-end: from profile → indexer depth → cobweb priority → discovery budget → cross-source boosting. It also adds embedding-similarity pre-filtering so GPU compute concentrates on candidates that can actually score well.

**Tech Stack:** Python 3.12, SQLAlchemy async Core, pytest, numpy. No new dependencies.

**Critique source:** `docs/superpowers/plans/2026-04-16-discovery-strategy-overhaul.critique.md` (inline in user message, Problems 1–16).

**Scope constraint:** All changes are backend-only in `backend/src/musicmind/`. No frontend changes. No new DB migrations. Existing `GPU_MODE=LOCAL` + `MUSICMIND_LOCAL_GPU_ENDPOINT_URL` env switching already routes the rebuilt worker through the user's ngrok tunnel — no config changes needed here.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/musicmind/engine/profile.py` | Modify | Log-saturate library presence in `build_artist_affinity` |
| `backend/src/musicmind/indexer.py` | Modify | Affinity-score-based depth; multiplicative calibration × frequency |
| `backend/src/musicmind/worker.py` | Modify | Cobweb: sum + log1p + primary-affinity weighting; embedding pre-filter |
| `backend/src/musicmind/api/recommendations/fetch.py` | Modify | Proportional budget in `discover_similar_artists`; `expand_genres` in `discover_chart_filter` |
| `backend/src/musicmind/api/recommendations/service.py` | Modify | Pass `(name, score)` tuples to discovery; positional weight plumbing |
| `backend/tests/test_artist_affinity.py` | Create | Unit tests for log1p library saturation |
| `backend/tests/test_indexer_ranking.py` | Create | Unit tests for `_get_ranked_artists` affinity return + depth calculation |
| `backend/tests/test_cobweb_ranking.py` | Create | Unit tests for sum+log1p cobweb aggregation |
| `backend/tests/test_discovery_budget.py` | Create | Unit tests for proportional budget distribution |
| `backend/tests/test_chart_filter_genres.py` | Create | Unit tests for expand_genres in chart filter |

**Commit strategy:** 6 atomic commits, one per task, each with format `fix|feat|refactor(<area>): <one-line summary>`. Per memory: push to `staging` after each commit so Railway worker picks up changes incrementally. Merge to `main` only after full manual smoke test on staging.

---

## Task 1: Log-saturate library presence in build_artist_affinity

**Problem addressed:** Critique Problem 15. "Library presence 0.3 × many songs overwhelms actual plays." A user with 50 library songs by an artist but zero plays gets score 15.0 — higher than 3 recent plays (9.0). Presence should saturate.

**Files:**
- Create: `backend/tests/test_artist_affinity.py`
- Modify: `backend/src/musicmind/engine/profile.py:182-264` (`build_artist_affinity`)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_artist_affinity.py
"""Test build_artist_affinity saturation and play-dominance invariants."""
from __future__ import annotations

from musicmind.engine.profile import build_artist_affinity


def test_library_presence_saturates_with_log1p() -> None:
    """50 library songs by an artist should NOT exceed contribution from ~3 plays."""
    songs = [{"artist_name": "Heavy Library", "catalog_id": f"s{i}"} for i in range(50)]
    history = [
        {"artist_name": "Actually Played", "song_id": "p1"},
        {"artist_name": "Actually Played", "song_id": "p2"},
        {"artist_name": "Actually Played", "song_id": "p3"},
    ]

    result = build_artist_affinity(songs, history)
    by_name = {r["name"]: r["score"] for r in result}

    # "Actually Played" has 3 plays (9.0 raw) and should outrank "Heavy Library"
    # whose 50-song presence gets log-saturated to ~1.18 (0.3 * log1p(50)).
    assert by_name["Actually Played"] > by_name["Heavy Library"], (
        f"Plays should dominate presence. Got {by_name}"
    )


def test_single_library_song_still_counts() -> None:
    """log1p(1) ≈ 0.69, so a single library song still contributes non-trivially."""
    songs = [{"artist_name": "Solo", "catalog_id": "s1"}]
    result = build_artist_affinity(songs, [])
    assert len(result) == 1
    assert result[0]["name"] == "Solo"
    assert result[0]["score"] > 0.0


def test_library_presence_caps_at_3() -> None:
    """Saturation cap: even 1000 library songs by one artist shouldn't exceed 3.0 raw."""
    songs = [{"artist_name": "Overflow", "catalog_id": f"s{i}"} for i in range(1000)]
    # Plus a very active artist as calibration
    history = [{"artist_name": "Active", "song_id": f"h{i}"} for i in range(20)]
    result = build_artist_affinity(songs, history)
    by_name = {r["name"]: r["score"] for r in result}
    # Active has ~100 raw (20 plays × ~5), Overflow capped at ≤3 raw → ratio ≥ 30x
    assert by_name["Active"] / max(by_name["Overflow"], 0.001) > 20.0, (
        f"Cap not effective: {by_name}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_artist_affinity.py -v
```
Expected: FAIL — `Heavy Library` currently outranks `Actually Played` because 50 × 0.3 = 15.0 > 9.0.

- [ ] **Step 3: Write minimal implementation**

Modify `backend/src/musicmind/engine/profile.py`. Replace the library-songs loop in `build_artist_affinity` (lines 204–226):

```python
    # Library songs: log-saturated presence (prevents 50 unplayed songs
    # from swamping 3 recent plays). Cap at 3.0 raw so plays always dominate.
    library_counts: dict[str, int] = Counter()
    library_decay: dict[str, float] = {}
    library_ratings: dict[str, int] = {}

    for song in songs:
        raw_artist = song.get("artist_name", "")
        if not raw_artist:
            continue
        decay = 1.0
        if use_temporal_decay:
            ts = song.get("date_added_to_library") or song.get("fetched_at")
            decay = temporal_decay_weight(ts, now, half_life_days)

        parsed = parse_artists(raw_artist)
        for name, weight in parsed:
            # Aggregate raw counts first; saturate after the loop.
            library_counts[name] += 1
            # Keep the max decay observed for this artist (most recently added).
            if decay > library_decay.get(name, 0.0):
                library_decay[name] = decay
            artist_song_counts[name] += 1

        rating = song.get("user_rating")
        primary_name = parsed[0][0] if parsed else raw_artist
        if rating is not None:
            library_ratings[primary_name] = rating

    # Apply log1p saturation to accumulated library presence.
    for name, count in library_counts.items():
        saturated = min(3.0, 0.3 * math.log1p(count))
        artist_scores[name] += saturated * library_decay.get(name, 1.0)

    # Apply explicit ratings (still strong signal).
    for name, rating in library_ratings.items():
        decay = library_decay.get(name, 1.0)
        if rating == 1:
            artist_scores[name] += 4.0 * decay
        elif rating == -1:
            artist_scores[name] -= 3.0 * decay
```

`math` is already imported at the top of the file. Confirm with:

```
grep "^import math" backend/src/musicmind/engine/profile.py
```

- [ ] **Step 4: Run test to verify it passes**

```
cd backend && uv run pytest tests/test_artist_affinity.py -v
```
Expected: PASS (3 tests).

Also run the existing engine tests to confirm no regressions:

```
cd backend && uv run pytest tests/test_engine_models.py tests/test_engine_perf.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/musicmind/engine/profile.py backend/tests/test_artist_affinity.py
git commit -m "$(cat <<'EOF'
fix(profile): log-saturate library presence in build_artist_affinity

Replaces linear 0.3-per-song accumulation with min(3.0, 0.3*log1p(count)).
Prevents 50 unplayed library songs from outranking 3 recent plays. Plays
remain the dominant signal as documented in the function's docstring.

Addresses critique Problem 15.
EOF
)"
git push origin staging
```

---

## Task 2: Rewrite _get_ranked_artists with affinity scores + multiplicative calibration + use for depth

**Problem addressed:** Critique Problems 1, 2, 3, 4.
- P1: STEP_DEPTHS cliff from rank 3 (50%) to rank 4 (30%), then flat for ranks 4–30.
- P2: `_get_ranked_artists` discards the affinity score (returns `list[str]`).
- P3: Calibration weights concatenated on top of frequency (Simba calibrated=5 ranks above Baby Gang played 200×).
- P4: `max_other = clamp(library*0.2, 5, 30)` cliff cuts off real listeners.

**Files:**
- Create: `backend/tests/test_indexer_ranking.py`
- Modify: `backend/src/musicmind/indexer.py:31-248, 347-400, 484-538`

This task changes the return type of `_get_ranked_artists` and all callers. Callers: `run_indexing` (line 170), `_suggest_and_enrich_artists` (line 490).

- [ ] **Step 1: Write the failing test**

Note: To keep tests DB-free, the plan extracts the pure ranking logic into a helper `_rank_artists_by_affinity(freq_map, cal_weights)` that takes plain dicts. `_get_ranked_artists` becomes a thin DB-query wrapper that delegates the math to this helper. Only the pure helper is unit-tested; the DB wrapper is covered by existing integration tests.

```python
# backend/tests/test_indexer_ranking.py
"""Tests for indexer pure ranking math (no DB)."""
from __future__ import annotations

import pytest

from musicmind.indexer import (
    _rank_artists_by_affinity,
    compute_depth_fraction,
)


def test_ranked_artists_returns_tuples_with_normalized_scores() -> None:
    freq_map = {"Baby Gang": 200, "Simba La Rue": 3}
    cal_weights = {"simba la rue": 5.0}
    ranked = _rank_artists_by_affinity(freq_map, cal_weights)
    assert isinstance(ranked, list)
    assert ranked, "expected at least one artist"
    for entry in ranked:
        assert isinstance(entry, tuple)
        name, score = entry
        assert isinstance(name, str) and name
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
    # Top score must be exactly 1.0 after normalization.
    assert ranked[0][1] == pytest.approx(1.0)


def test_frequency_dominates_calibration_when_mismatched() -> None:
    """200 plays of Baby Gang beats calibration-5 Simba with 3 songs."""
    freq_map = {"Baby Gang": 200, "Simba La Rue": 3}
    cal_weights = {"simba la rue": 5.0}
    ranked = _rank_artists_by_affinity(freq_map, cal_weights)
    names = [n for n, _ in ranked]
    assert names.index("Baby Gang") < names.index("Simba La Rue"), (
        f"Baby Gang should rank above Simba La Rue. Got: {names}"
    )


def test_calibration_boosts_without_replacing() -> None:
    """Calibration gives Simba a non-trivial score but doesn't flip ranking vs 200-song Baby Gang."""
    ranked = _rank_artists_by_affinity(
        freq_map={"Baby Gang": 200, "Simba La Rue": 3},
        cal_weights={"simba la rue": 5.0},
    )
    scores = dict(ranked)
    assert scores["Simba La Rue"] > 0.0
    assert scores["Baby Gang"] > scores["Simba La Rue"]


def test_calibration_only_artist_gets_seeded_frequency() -> None:
    """A calibrated artist with zero library songs still appears in the ranking."""
    ranked = _rank_artists_by_affinity(
        freq_map={"Only Library": 10},
        cal_weights={"calibrated only": 5.0},
    )
    names = {n.lower() for n, _ in ranked}
    assert "calibrated only" in names


def test_empty_inputs_return_empty() -> None:
    assert _rank_artists_by_affinity({}, {}) == []


def test_compute_depth_fraction_continuous() -> None:
    assert compute_depth_fraction(1.0) == pytest.approx(1.0)
    assert compute_depth_fraction(0.5) == pytest.approx(0.6)
    assert compute_depth_fraction(0.1) == pytest.approx(0.15)
    assert compute_depth_fraction(0.0) == pytest.approx(0.15)


def test_compute_depth_fraction_smooth_at_rank_cliff() -> None:
    """No discontinuity between rank 3 (~0.7 score) and rank 4 (~0.6 score)."""
    score_3 = compute_depth_fraction(0.7)
    score_4 = compute_depth_fraction(0.6)
    assert abs(score_3 - score_4) < 0.15, (
        f"Depth curve should be smooth: {score_3=} {score_4=}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && uv run pytest tests/test_indexer_ranking.py -v
```
Expected: FAIL — `compute_depth_fraction` and `_rank_artists_by_affinity` do not yet exist.

- [ ] **Step 3: Write implementation**

Modify `backend/src/musicmind/indexer.py`. Replace `STEP_DEPTHS` and add `compute_depth_fraction` (near the top after `MAX_TRACKS_PER_ARTIST`):

```python
# Continuous depth fraction (replaces rank-based STEP_DEPTHS).
# score ∈ [0, 1] → depth ∈ [0.15, 1.0].
MIN_DEPTH_FRAC = 0.15
MAX_DEPTH_FRAC = 1.0
DEPTH_SCALE = 1.2  # score * scale, clamped to [floor, cap]
MAX_TRACKS_PER_ARTIST = 50
# Include artists whose normalized affinity is above this in discography enrichment.
AFFINITY_INCLUDE_THRESHOLD = 0.05
# Calibration amplifies frequency multiplicatively (1 + CAL_BOOST * weight).
# With weight=5 ("top"), multiplier is 1 + 0.5*5 = 3.5x.
CAL_BOOST = 0.1


def compute_depth_fraction(affinity_score: float) -> float:
    """Map affinity score to discography enrichment fraction."""
    return max(MIN_DEPTH_FRAC, min(MAX_DEPTH_FRAC, affinity_score * DEPTH_SCALE))
```

Delete the `STEP_DEPTHS` dict.

Add the pure ranking helper (unit-testable without a DB):

```python
def _rank_artists_by_affinity(
    freq_map: dict[str, int],
    cal_weights: dict[str, float],
) -> list[tuple[str, float]]:
    """Rank artists by affinity score (pure function, no DB).

    Score combines frequency (library song count) with calibration as a
    multiplicative modifier: frequency * (1 + CAL_BOOST * calibration_weight).
    Calibration boosts existing listening, rather than replacing it.

    Args:
        freq_map: {artist_name: song_count}. Use primary-artist name (post parse_artists).
        cal_weights: {lowercased_artist_name: calibration_weight}.

    Returns:
        [(name, normalized_score_0_to_1)], sorted descending. Top score = 1.0.
    """
    if not freq_map and not cal_weights:
        return []

    combined: dict[str, float] = {}
    for name, freq in freq_map.items():
        cal = cal_weights.get(name.lower(), 0.0)
        combined[name] = float(freq) * (1.0 + CAL_BOOST * cal)

    # Calibration-only artists (no library songs): seed baseline frequency=1.
    existing_lower = {n.lower() for n in combined}
    for cal_name, cal in cal_weights.items():
        if cal_name not in existing_lower:
            combined[cal_name.title()] = 1.0 * (1.0 + CAL_BOOST * cal)

    if not combined:
        return []

    max_score = max(combined.values())
    if max_score <= 0:
        return []

    return sorted(
        ((name, score / max_score) for name, score in combined.items()),
        key=lambda x: x[1],
        reverse=True,
    )
```

Replace `_get_ranked_artists` (lines 347–400) — now a thin DB wrapper around the pure helper:

```python
async def _get_ranked_artists(engine, *, user_id: str) -> list[tuple[str, float]]:
    """Return (artist_name, normalized_score) tuples ranked by affinity."""
    from musicmind.db.schema import song_metadata_cache, user_calibration
    from musicmind.engine.profile import parse_artists

    async with engine.begin() as conn:
        cal_result = await conn.execute(
            sa.select(
                user_calibration.c.item_name,
                user_calibration.c.weight,
            ).where(
                sa.and_(
                    user_calibration.c.user_id == user_id,
                    user_calibration.c.calibration_type.in_(
                        ["top_artist", "artist_rank"]
                    ),
                )
            )
        )
        cal_weights: dict[str, float] = {
            (row.item_name or "").lower(): float(row.weight or 0.0)
            for row in cal_result
            if row.item_name
        }

        freq_result = await conn.execute(
            sa.select(
                song_metadata_cache.c.artist_name,
                sa.func.count().label("count"),
            ).where(
                sa.and_(
                    song_metadata_cache.c.user_id == user_id,
                    sa.or_(
                        song_metadata_cache.c.library_id.isnot(None),
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                    ),
                )
            ).group_by(song_metadata_cache.c.artist_name)
        )
        # Aggregate by primary artist so "Baby Gang feat. X" folds into "Baby Gang".
        raw_freq: dict[str, int] = {}
        for row in freq_result:
            if not row.artist_name:
                continue
            parsed = parse_artists(row.artist_name)
            if not parsed:
                continue
            primary_name = parsed[0][0]
            raw_freq[primary_name] = raw_freq.get(primary_name, 0) + int(row.count)

    return _rank_artists_by_affinity(raw_freq, cal_weights)
```

Replace the caller in `run_indexing` (lines 170–211):

```python
    ranked_artists = await _get_ranked_artists(engine, user_id=user_id)
    if ranked_artists:
        # Include any artist above the affinity threshold — no hard cap.
        # Always include at least the top 3 even if scores are thin.
        artists_to_process: list[tuple[str, float]] = [
            (n, s) for n, s in ranked_artists
            if s >= AFFINITY_INCLUDE_THRESHOLD
        ]
        if len(artists_to_process) < 3:
            artists_to_process = ranked_artists[:3]
        total_artists = len(artists_to_process)

        logger.info(
            "User %s: processing %d/%d artists above threshold %.2f",
            user_id[:8], total_artists, len(ranked_artists),
            AFFINITY_INCLUDE_THRESHOLD,
        )

        for i, (artist_name, affinity_score) in enumerate(artists_to_process):
            # Step 2,3,4 for top 3 (for dashboard readability), 5 for tail.
            step = min(i + 2, 5)

            depth_frac = compute_depth_fraction(affinity_score)
            limit = max(5, int(MAX_TRACKS_PER_ARTIST * depth_frac))

            step_name = f"artist_{i + 1}_of_{total_artists}"
            await _set_indexing_status(
                engine, user_id, step, step_name,
                current=i + 1, total=total_artists,
            )

            try:
                fetched = await _fetch_and_enrich_discography(
                    engine, settings, creds, user_id=user_id,
                    artist_name=artist_name, limit=limit,
                )
                stats["discography_fetched"] += fetched
            except Exception:
                logger.debug(
                    "User %s: discography fetch failed for '%s'",
                    user_id[:8], artist_name,
                )
```

Update `_suggest_and_enrich_artists` signature and call site (lines 214–229, 484–538):

Call site (line ~218):
```python
        suggested = await _suggest_and_enrich_artists(
            engine, settings, creds, user_id=user_id,
            ranked_artists=ranked_artists,   # now list[tuple[str, float]]
            max_artists=max_suggested,
        )
```

Inside `_suggest_and_enrich_artists`, update the set comprehension:
```python
    library_set = {n.lower() for n, _ in ranked_artists}
```

Suggested-artist loop uses a fixed 50% depth (until Task 4 rewrites cobweb):
```python
    for artist_name in selected:
        limit = max(5, int(MAX_TRACKS_PER_ARTIST * 0.5))  # suggested-artists default
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_indexer_ranking.py -v
```
Expected: PASS.

```
cd backend && uv run pytest tests/ -v -x
```
Expected: all existing tests still PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add backend/src/musicmind/indexer.py backend/tests/test_indexer_ranking.py
git commit -m "$(cat <<'EOF'
feat(indexer): affinity-score-based depth + multiplicative calibration

Replaces rank-based STEP_DEPTHS cliff (rank 3 → 50%, rank 4 → 30%) with
continuous compute_depth_fraction(score) in [0.15, 1.0]. Self-adjusts to
the user's listening distribution without hand-tuned percentages.

_get_ranked_artists now returns list[tuple[name, score]] and combines
frequency * (1 + 0.1 * calibration_weight) instead of concatenating.
A user with 200 Baby Gang plays and calibration-5 Simba still gets
Baby Gang ranked first — correctly.

Hard cap (max_other = clamp(library*0.2, 5, 30)) replaced with continuous
AFFINITY_INCLUDE_THRESHOLD=0.05.

Addresses critique Problems 1, 2, 3, 4.
EOF
)"
git push origin staging
```

---

## Task 3: Rewrite cobweb ranking (sum + log1p + primary affinity weighting)

**Problem addressed:** Critique Problems 5, 6, 7, 8.
- P5: `candidates[key] = (name, max(old_priority, weight * 2))` loses co-occurrence signal — 10× featured artist looks like 1× featured artist.
- P6: Every feat credit contributes the same 0.6 regardless of which library track it came from (feat on top-artist's track vs. feat on rank-50 artist's track).
- P7: `max_total = library*0.5` ignores actual feat density.
- P8: Same cobweb logic duplicated in `worker.py:_build_user_cobweb` and `indexer.py:_suggest_and_enrich_artists`.

**Files:**
- Create: `backend/tests/test_cobweb_ranking.py`
- Create: `backend/src/musicmind/engine/cobweb.py` (new shared module)
- Modify: `backend/src/musicmind/worker.py:595-735` (use shared module)
- Modify: `backend/src/musicmind/indexer.py:484-538` (use shared module)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_cobweb_ranking.py
"""Tests for shared cobweb ranking logic."""
from __future__ import annotations

import math

from musicmind.engine.cobweb import rank_cobweb_candidates


def test_sum_accumulates_co_occurrence() -> None:
    """An artist featured 10 times should outrank one featured once."""
    # Primary artists all have affinity 1.0 here.
    library_rows = [
        {"artist_name": "Main feat. Prolific", "primary_affinity": 1.0}
    ] * 10 + [
        {"artist_name": "Main feat. Rare", "primary_affinity": 1.0}
    ]
    library_names = {"main"}
    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names=library_names,
        existing_cobweb_names=set(),
    )
    names_by_priority = [name for name, _ in ranked]
    assert names_by_priority[0].lower() == "prolific"
    by_name = {n.lower(): p for n, p in ranked}
    # log1p(10*0.6)≈1.96 vs log1p(1*0.6)≈0.47 → ratio ~4x, not 10x (dampened).
    ratio = by_name["prolific"] / max(by_name["rare"], 0.001)
    assert 2.0 < ratio < 6.0, f"Expected log-dampened ratio 2-6x, got {ratio:.2f}"


def test_primary_affinity_weights_contributions() -> None:
    """A feat on the top artist's track should outrank one on a tail-artist track."""
    library_rows = [
        {"artist_name": "TopArtist feat. Alice", "primary_affinity": 1.0},
        {"artist_name": "TailArtist feat. Bob", "primary_affinity": 0.1},
    ]
    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names={"topartist", "tailartist"},
        existing_cobweb_names=set(),
    )
    scores = {n.lower(): p for n, p in ranked}
    assert scores["alice"] > scores["bob"], (
        f"Feat on top artist should outrank feat on tail artist. Got {scores}"
    )


def test_library_artists_excluded() -> None:
    """Don't add library artists back to the cobweb."""
    library_rows = [
        {"artist_name": "A feat. B", "primary_affinity": 1.0},
    ]
    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names={"a", "b"},  # both already in library
        existing_cobweb_names=set(),
    )
    assert ranked == []


def test_cap_based_on_feat_density_not_library_size() -> None:
    """Cap should reflect the number of unique featured names present."""
    library_rows = [
        {"artist_name": f"Main feat. Feat{i}", "primary_affinity": 1.0}
        for i in range(7)
    ]
    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names={"main"},
        existing_cobweb_names=set(),
        max_total=100,  # high cap — actual cap is feat density
    )
    # 7 unique features should give 7 cobweb candidates (density cap).
    assert len(ranked) == 7
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_cobweb_ranking.py -v
```
Expected: FAIL (`musicmind.engine.cobweb` does not exist).

- [ ] **Step 3: Write implementation**

Create `backend/src/musicmind/engine/cobweb.py`:

```python
"""Shared cobweb candidate ranking.

Used by both worker._build_user_cobweb and indexer._suggest_and_enrich_artists
so suggested artists follow the same selection logic everywhere.

Core idea: accumulate co-occurrence evidence (sum, not max), dampen with log1p
to prevent super-collaborators from dominating, weight each contribution by the
primary artist's affinity so feats on top-tier tracks count more than feats on
tail tracks.
"""
from __future__ import annotations

import math
from typing import Any

from musicmind.engine.profile import parse_artists


def rank_cobweb_candidates(
    *,
    library_rows: list[dict[str, Any]],
    library_artist_names: set[str],  # lowercased
    existing_cobweb_names: set[str],  # lowercased
    max_total: int | None = None,
) -> list[tuple[str, float]]:
    """Rank featured-artist candidates for the cobweb.

    Args:
        library_rows: One dict per library track with keys:
            - "artist_name": raw artist string (may contain feat/ft/featuring)
            - "primary_affinity": float in [0, 1] — the primary artist's affinity
              score (from build_artist_affinity). Use 0.1 as fallback for unknown.
        library_artist_names: Lowercased names already in the user's library (excluded).
        existing_cobweb_names: Lowercased names already in the cobweb (excluded).
        max_total: Optional hard cap on returned candidates. If None, cap is
            the number of unique candidates (feat density).

    Returns:
        (name, priority) tuples sorted by priority descending. Priority uses
        log1p-dampened weighted sum: priority = log1p(sum_i (0.6 * primary_affinity_i))
        where the sum is over each library row in which the candidate features.
    """
    # Accumulate raw weighted co-occurrences (sum).
    raw: dict[str, float] = {}
    canonical_name: dict[str, str] = {}

    for row in library_rows:
        raw_name = row.get("artist_name") or ""
        if not raw_name:
            continue
        primary_affinity = float(row.get("primary_affinity", 0.1))
        parsed = parse_artists(raw_name)
        if not parsed:
            continue
        primary_lower = parsed[0][0].lower()
        for name, weight in parsed:
            key = name.strip().lower()
            if not key or len(key) <= 1:
                continue
            if key == primary_lower:
                continue  # primary doesn't "feature" on its own track
            if key in library_artist_names or key in existing_cobweb_names:
                continue
            contribution = weight * 2.0 * max(0.05, primary_affinity)
            raw[key] = raw.get(key, 0.0) + contribution
            canonical_name.setdefault(key, name.strip())

    if not raw:
        return []

    # Log-dampened priority.
    priorities: list[tuple[str, float]] = [
        (canonical_name[k], math.log1p(v))
        for k, v in raw.items()
    ]
    priorities.sort(key=lambda x: x[1], reverse=True)

    # Cap = min(feat density, explicit cap). Never inflate beyond what's present.
    density_cap = len(priorities)
    effective_cap = (
        min(max_total, density_cap) if max_total is not None else density_cap
    )
    return priorities[:effective_cap]
```

Refactor `backend/src/musicmind/worker.py` to use this. Replace the body of `_build_user_cobweb` from line 617 through line 697 (Source 1 block) with:

```python
    # Get library artists + their affinity scores (used to weight feat contributions).
    from musicmind.engine.cobweb import rank_cobweb_candidates

    async with engine.begin() as conn:
        library_result = await conn.execute(
            sa.select(
                song_metadata_cache.c.artist_name,
            ).where(
                sa.and_(
                    song_metadata_cache.c.user_id == user_id,
                    sa.or_(
                        song_metadata_cache.c.library_id.isnot(None),
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                    ),
                )
            )
        )
        library_rows_raw = [row.artist_name for row in library_result if row.artist_name]

    if not library_rows_raw:
        return stats

    library_set = set()
    for raw in library_rows_raw:
        from musicmind.engine.profile import parse_artists
        for name, _ in parse_artists(raw):
            library_set.add(name.lower())

    # Load taste profile to get primary-artist affinity scores.
    from musicmind.db.schema import taste_profile_snapshots
    import json as _json

    async with engine.begin() as conn:
        snap = (await conn.execute(
            sa.select(taste_profile_snapshots.c.top_artists)
            .where(taste_profile_snapshots.c.user_id == user_id)
            .order_by(taste_profile_snapshots.c.computed_at.desc())
            .limit(1)
        )).first()
    affinity_map: dict[str, float] = {}
    if snap and snap.top_artists:
        top_artists_raw = snap.top_artists
        if isinstance(top_artists_raw, str):
            try:
                top_artists_raw = _json.loads(top_artists_raw)
            except (ValueError, TypeError):
                top_artists_raw = []
        for entry in top_artists_raw or []:
            if isinstance(entry, dict):
                n = str(entry.get("name", "")).lower()
                s = float(entry.get("score", 0.0))
                if n:
                    affinity_map[n] = s

    library_rows = [
        {
            "artist_name": raw,
            "primary_affinity": _primary_affinity_lookup(raw, affinity_map),
        }
        for raw in library_rows_raw
    ]

    # Get existing cobweb artists to avoid re-adding.
    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(artist_cobweb.c.artist_name).where(
                artist_cobweb.c.user_id == user_id
            )
        )
        existing_set = {row.artist_name.lower() for row in existing}

    # Per-cycle budget is library_artists * 0.2 (stays as a rate limiter, not a total cap).
    max_per_cycle = max(2, int(len(library_set) * 0.2))
    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names=library_set,
        existing_cobweb_names=existing_set,
        max_total=None,  # cap is feat density, not library size
    )

    to_add = ranked[:max_per_cycle]

    for name, priority in to_add:
        source = "feat"
        try:
            async with engine.begin() as conn:
                result = await conn.execute(
                    sa.text(
                        "INSERT INTO artist_cobweb"
                        " (user_id, artist_name, source, priority)"
                        " VALUES (:uid, :name, :src, :pri)"
                        " ON CONFLICT (user_id, artist_name) DO NOTHING"
                    ),
                    {"uid": user_id, "name": name, "src": source, "pri": priority},
                )
                if result.rowcount > 0:
                    stats["cobweb_artists"] += 1
        except Exception:
            logger.debug("Cobweb insert failed for '%s'", name)
```

Add the helper near the top of worker.py (after imports):

```python
def _primary_affinity_lookup(raw_name: str, affinity_map: dict[str, float]) -> float:
    """Find the primary artist's affinity score for weighting feat contributions."""
    from musicmind.engine.profile import parse_artists
    parsed = parse_artists(raw_name)
    if not parsed:
        return 0.1
    return affinity_map.get(parsed[0][0].lower(), 0.1)
```

Refactor `backend/src/musicmind/indexer.py:_suggest_and_enrich_artists` (lines 484–538) similarly. Load affinity scores from `ranked_artists` (already available as `list[tuple[str, float]]` from Task 2):

```python
async def _suggest_and_enrich_artists(
    engine,
    settings,
    creds: dict,
    *,
    user_id: str,
    ranked_artists: list[tuple[str, float]],
    max_artists: int,
) -> int:
    """Find and enrich suggested artists from featured collaborations."""
    from musicmind.db.schema import song_metadata_cache
    from musicmind.engine.cobweb import rank_cobweb_candidates
    from musicmind.engine.profile import parse_artists

    library_set = {n.lower() for n, _ in ranked_artists}
    affinity_map = {n.lower(): s for n, s in ranked_artists}

    # Fetch raw library artist strings (with feats).
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(sa.distinct(song_metadata_cache.c.artist_name)).where(
                sa.and_(
                    song_metadata_cache.c.user_id == user_id,
                    sa.or_(
                        song_metadata_cache.c.library_id.isnot(None),
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                    ),
                )
            )
        )
        raw_names = [row[0] for row in result if row[0]]

    library_rows = []
    for raw in raw_names:
        parsed = parse_artists(raw)
        primary = parsed[0][0].lower() if parsed else ""
        library_rows.append({
            "artist_name": raw,
            "primary_affinity": affinity_map.get(primary, 0.1),
        })

    ranked = rank_cobweb_candidates(
        library_rows=library_rows,
        library_artist_names=library_set,
        existing_cobweb_names=set(),  # suggester doesn't check cobweb table
        max_total=max_artists,
    )

    enriched_count = 0
    for artist_name, _priority in ranked:
        limit = max(5, int(MAX_TRACKS_PER_ARTIST * 0.5))
        try:
            fetched = await _fetch_and_enrich_discography(
                engine, settings, creds, user_id=user_id,
                artist_name=artist_name, limit=limit,
            )
            enriched_count += 1 if fetched > 0 else 0
        except Exception:
            logger.debug("Suggested artist enrichment failed for '%s'", artist_name)

    return enriched_count
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_cobweb_ranking.py -v
cd backend && uv run pytest tests/ -x
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/musicmind/engine/cobweb.py backend/src/musicmind/worker.py backend/src/musicmind/indexer.py backend/tests/test_cobweb_ranking.py
git commit -m "$(cat <<'EOF'
refactor(cobweb): sum + log1p + primary-affinity weighting; shared module

Replaces max() aggregation with sum() so 10x featured artists correctly
outrank 1x featured ones (log1p dampens prolific collaborators to ~4x,
not 10x). Each feat contribution is now weighted by the primary artist's
affinity score, so a feat on a top-artist track counts ~10x more than one
on a tail-artist track.

Cap is now feat density (unique featured names), not library_size * 0.5.
Shared ranking logic lives in engine/cobweb.py; worker._build_user_cobweb
and indexer._suggest_and_enrich_artists both consume it.

Addresses critique Problems 5, 6, 7, 8.
EOF
)"
git push origin staging
```

---

## Task 4: Pre-filter cobweb enrichment by embedding similarity to user centroid

**Problem addressed:** Critique cross-cutting issue. Cobweb enrichment today does full Essentia + Modal GPU pipeline on every cobweb track regardless of how likely it is to score well. For ISRCs already enriched globally (by another user), we have an EffNet embedding — use it to rank candidates by cosine similarity to the user's embedding centroid and skip the bottom tier.

**Files:**
- Modify: `backend/src/musicmind/worker.py:_fetch_artist_songs_globally` + caller block
- Modify: `backend/src/musicmind/engine/cobweb.py` (add prefilter helper)
- Create: `backend/tests/test_cobweb_prefilter.py`

This is the biggest compute-savings change and also the one most dependent on the ngrok local-GPU worker redeploying — pre-filtering means fewer calls to the worker, so local-Mac is viable.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_cobweb_prefilter.py
"""Tests for embedding-similarity pre-filter before cobweb enrichment."""
from __future__ import annotations

from musicmind.engine.cobweb import prefilter_by_centroid_similarity


def test_keeps_top_fraction_by_cosine() -> None:
    """Keep top 70% by cosine similarity to centroid; drop bottom 30%."""
    centroid = [1.0, 0.0, 0.0]
    tracks = [
        {"catalog_id": "aligned", "effnet_embedding": [1.0, 0.0, 0.0]},
        {"catalog_id": "perpendicular", "effnet_embedding": [0.0, 1.0, 0.0]},
        {"catalog_id": "slight", "effnet_embedding": [0.9, 0.1, 0.0]},
        {"catalog_id": "opposite", "effnet_embedding": [-1.0, 0.0, 0.0]},
    ]
    kept = prefilter_by_centroid_similarity(
        tracks=tracks,
        centroid=centroid,
        keep_fraction=0.5,
    )
    kept_ids = {t["catalog_id"] for t in kept}
    # Two tracks kept; they should be the two with highest cosine.
    assert kept_ids == {"aligned", "slight"}


def test_tracks_without_embedding_pass_through() -> None:
    """Tracks with no embedding are kept (can't filter unknown, must enrich to learn)."""
    centroid = [1.0, 0.0]
    tracks = [
        {"catalog_id": "unknown_a"},  # no embedding
        {"catalog_id": "has_emb", "effnet_embedding": [0.5, 0.5]},
    ]
    kept = prefilter_by_centroid_similarity(
        tracks=tracks, centroid=centroid, keep_fraction=0.5,
    )
    kept_ids = {t["catalog_id"] for t in kept}
    assert "unknown_a" in kept_ids


def test_no_centroid_is_passthrough() -> None:
    """If no centroid (cold-start user), skip filtering entirely."""
    tracks = [{"catalog_id": f"t{i}"} for i in range(5)]
    kept = prefilter_by_centroid_similarity(
        tracks=tracks, centroid=None, keep_fraction=0.7,
    )
    assert len(kept) == 5


def test_keep_fraction_bounds() -> None:
    """Always keep at least 1 track; never more than input."""
    centroid = [1.0]
    tracks = [{"catalog_id": "t1", "effnet_embedding": [1.0]}]
    assert len(prefilter_by_centroid_similarity(tracks=tracks, centroid=centroid, keep_fraction=0.1)) >= 1
    assert len(prefilter_by_centroid_similarity(tracks=tracks, centroid=centroid, keep_fraction=2.0)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_cobweb_prefilter.py -v
```
Expected: FAIL — `prefilter_by_centroid_similarity` does not exist.

- [ ] **Step 3: Write implementation**

Append to `backend/src/musicmind/engine/cobweb.py`:

```python
def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0.0 for empty or mismatched inputs."""
    if not a or not b or len(a) != len(b):
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def prefilter_by_centroid_similarity(
    *,
    tracks: list[dict[str, Any]],
    centroid: list[float] | None,
    keep_fraction: float = 0.7,
) -> list[dict[str, Any]]:
    """Keep the top keep_fraction of tracks by embedding cosine to centroid.

    Tracks without an embedding are kept (we can't rank them yet; enrichment
    is the only way to learn their embedding). If the centroid is None
    (cold-start user), the input is returned unchanged.

    Args:
        tracks: Dicts that may contain "effnet_embedding": list[float].
        centroid: User's L2-normalized embedding centroid, or None.
        keep_fraction: Fraction in (0, 1] to keep among tracks that HAVE embeddings.

    Returns:
        Filtered tracks. Order of non-embedding tracks preserved; ranked tracks
        are returned in descending-similarity order.
    """
    if not tracks:
        return tracks
    if centroid is None:
        return tracks

    keep_fraction = max(0.01, min(1.0, keep_fraction))

    with_emb: list[tuple[float, dict[str, Any]]] = []
    without_emb: list[dict[str, Any]] = []

    for t in tracks:
        emb = t.get("effnet_embedding") or t.get("embedding")
        if isinstance(emb, list) and len(emb) > 10:
            with_emb.append((_cosine(emb, centroid), t))
        else:
            without_emb.append(t)

    if not with_emb:
        return tracks

    with_emb.sort(key=lambda x: x[0], reverse=True)
    keep_n = max(1, int(round(len(with_emb) * keep_fraction)))
    kept_ranked = [t for _, t in with_emb[:keep_n]]
    return kept_ranked + without_emb
```

Now wire it into the worker. In `worker.py:_fetch_artist_songs_globally` (around line 840), after fetching tracks but before enrichment, look up existing global embeddings and run the pre-filter.

Find the return point of `_fetch_artist_songs_globally` — it currently inserts all fetched tracks into `global_song_cache`. Add the prefilter step just before enrichment is triggered (this is in the caller block at worker.py:716–734, where `_fetch_artist_songs_globally` is followed by the enrichment step).

Actually the enrichment is inlined inside `_fetch_artist_songs_globally` today. Search for the exact pattern:

```
grep -n "enrich_tracks\|MAX_COBWEB_SONGS" backend/src/musicmind/worker.py
```

Find where `_fetch_artist_songs_globally` calls the enrichment orchestrator, and add the prefilter there. If the function just stores to `global_song_cache` without enriching, the filter belongs in the enrichment loop called later — locate it by searching for `enrich_tracks` calls triggered by the cobweb path:

```python
# Inside _fetch_artist_songs_globally, after tracks are fetched and before
# they enter the enrichment orchestrator:
from musicmind.engine.cobweb import prefilter_by_centroid_similarity
from musicmind.db.schema import audio_embeddings_global, taste_profile_snapshots
import json as _json

# Look up global embeddings for any ISRC we already know.
isrcs = [t.get("isrc") for t in tracks if t.get("isrc")]
emb_by_isrc: dict[str, list[float]] = {}
if isrcs:
    async with engine.begin() as conn:
        rows = await conn.execute(
            sa.select(
                audio_embeddings_global.c.isrc,
                audio_embeddings_global.c.embedding,
            ).where(audio_embeddings_global.c.isrc.in_(isrcs))
        )
        for row in rows:
            emb = row.embedding
            if isinstance(emb, list) and len(emb) > 10:
                emb_by_isrc[row.isrc] = emb
for t in tracks:
    isrc = t.get("isrc")
    if isrc and isrc in emb_by_isrc:
        t["effnet_embedding"] = emb_by_isrc[isrc]

# Load user centroid.
async with engine.begin() as conn:
    snap = (await conn.execute(
        sa.select(taste_profile_snapshots.c.embedding_centroid)
        .where(taste_profile_snapshots.c.user_id == user_id)
        .order_by(taste_profile_snapshots.c.computed_at.desc())
        .limit(1)
    )).first()
centroid = None
if snap and snap.embedding_centroid:
    raw = snap.embedding_centroid
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if isinstance(raw, list) and len(raw) > 10:
        centroid = raw

# Pre-filter: keep top 70% by embedding similarity (tracks without
# embeddings pass through since we can't rank them yet).
tracks = prefilter_by_centroid_similarity(
    tracks=tracks, centroid=centroid, keep_fraction=0.7,
)
```

If the function has already stored tracks to `global_song_cache` via earlier INSERT, do the filter BEFORE the INSERT (we still insert metadata for everything but only enrich the filtered set). If the only enrichment trigger is the orchestrator called after this function, the filter is fine here.

**Important:** verify the placement by reading the current shape of `_fetch_artist_songs_globally` before editing — it may pass the full list to `enrich_tracks`, in which case filter right before that call.

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_cobweb_prefilter.py -v
cd backend && uv run pytest tests/ -x
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/musicmind/engine/cobweb.py backend/src/musicmind/worker.py backend/tests/test_cobweb_prefilter.py
git commit -m "$(cat <<'EOF'
feat(worker): pre-filter cobweb enrichment by embedding similarity to user centroid

Before dispatching cobweb tracks through the Essentia + Modal GPU pipeline,
look up existing global EffNet embeddings by ISRC and rank by cosine
similarity to the user's embedding_centroid. Enrich the top 70%; skip
the bottom 30% — they were unlikely to surface in recommendations anyway.

Reduces GPU enrichment compute by ~30% on artists with well-enriched
discographies, concentrating work on candidates that will score well.
Particularly valuable now that the worker points at a local-Mac GPU
via the ngrok tunnel.

Tracks without a known embedding pass through (we need enrichment to
learn the embedding). Cold-start users (no centroid) skip the filter
entirely.

Addresses critique cross-cutting issue.
EOF
)"
git push origin staging
```

---

## Task 5: Distribute similar_artist budget proportionally + positional discovery weight

**Problem addressed:** Critique Problems 9, 11.
- P9: All four strategies use hard truncation (`top_genres[:3]`, `seed_artist_names[:5]`) — seed #5 (affinity 0.2) contributes as many candidates as seed #1 (affinity 1.0).
- P11: `discover_similar_artists` takes related[:5] but loses positional similarity downstream — similar-artist #1 weighted same as #5.

**Files:**
- Create: `backend/tests/test_discovery_budget.py`
- Modify: `backend/src/musicmind/api/recommendations/fetch.py:discover_similar_artists`
- Modify: `backend/src/musicmind/api/recommendations/service.py:_run_discovery`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_discovery_budget.py
"""Tests for proportional budget distribution across seed artists."""
from __future__ import annotations

from musicmind.api.recommendations.fetch import allocate_seed_budget


def test_budget_proportional_to_affinity() -> None:
    seeds = [("Top", 1.0), ("Mid", 0.5), ("Tail", 0.1)]
    allocation = allocate_seed_budget(seeds, total_budget=40)
    # Weights sum to 1.6; allocation rounds to ≥1 each.
    assert allocation["Top"] > allocation["Mid"] > allocation["Tail"]
    assert sum(allocation.values()) <= 40
    assert all(v >= 1 for v in allocation.values())


def test_uniform_seeds_get_equal_budget() -> None:
    seeds = [("A", 1.0), ("B", 1.0), ("C", 1.0)]
    allocation = allocate_seed_budget(seeds, total_budget=30)
    assert allocation["A"] == allocation["B"] == allocation["C"] == 10


def test_empty_seeds() -> None:
    assert allocate_seed_budget([], total_budget=20) == {}


def test_minimum_one_per_seed() -> None:
    """Even a seed with tiny affinity gets at least 1 slot."""
    seeds = [("Big", 1.0), ("Tiny", 0.001)]
    allocation = allocate_seed_budget(seeds, total_budget=10)
    assert allocation["Tiny"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_discovery_budget.py -v
```
Expected: FAIL (`allocate_seed_budget` does not exist).

- [ ] **Step 3: Write implementation**

Add to `backend/src/musicmind/api/recommendations/fetch.py` (top of file, after imports):

```python
def allocate_seed_budget(
    scored_seeds: list[tuple[str, float]],
    *,
    total_budget: int,
) -> dict[str, int]:
    """Allocate track-fetch budget across seeds proportional to affinity.

    Every seed gets at least 1 slot (prevents tiny-affinity seeds from being
    silently dropped, which also keeps the crawl diverse). Unallocated slots
    from rounding are distributed to highest-affinity seeds.
    """
    if not scored_seeds or total_budget <= 0:
        return {}
    # Filter out zero/negative affinity (they'd get 0 shares).
    positive = [(n, max(0.001, s)) for n, s in scored_seeds]
    total_weight = sum(s for _, s in positive)
    allocation: dict[str, int] = {}
    for name, score in positive:
        share = int(total_budget * score / total_weight)
        allocation[name] = max(1, share)
    # Redistribute leftover due to floor().
    spent = sum(allocation.values())
    leftover = total_budget - spent
    if leftover > 0:
        # Give to highest-affinity first.
        for name, _ in sorted(positive, key=lambda x: x[1], reverse=True):
            if leftover <= 0:
                break
            allocation[name] += 1
            leftover -= 1
    return allocation
```

Modify `discover_similar_artists` signature to accept `scored_seeds` and apply positional weight:

```python
async def discover_similar_artists(
    service: str,
    access_token: str,
    scored_seeds: list[tuple[str, float]],   # was: seed_artist_names: list[str]
    *,
    developer_token: str | None = None,
    storefront: str = "us",
    depth: int = 1,
    total_budget: int = 40,
) -> list[dict[str, Any]]:
    """Crawl similar artists with affinity-proportional budget + positional weights."""
    candidates: list[dict[str, Any]] = []

    if not scored_seeds:
        return candidates

    allocation = allocate_seed_budget(scored_seeds, total_budget=total_budget)

    # Resolve each seed and crawl. Budget per seed becomes "related × songs_per".
    for seed_name, _affinity in scored_seeds:
        per_seed_budget = allocation.get(seed_name, 1)
        # Distribute as ceil(sqrt) related artists × songs_per_artist so a
        # budget of 16 → 4 related × 4 songs, budget of 4 → 2 × 2.
        import math
        related_count = max(1, min(5, int(math.ceil(math.sqrt(per_seed_budget)))))
        songs_per_artist = max(1, per_seed_budget // related_count)

        aid = await _search_artist_id(
            service, access_token, seed_name,
            developer_token=developer_token, storefront=storefront,
        )
        if not aid:
            continue

        client = _get_shared_client()
        try:
            related = await _fetch_related_artists(
                client, service, access_token, aid,
                developer_token=developer_token,
                storefront=storefront,
                limit=related_count,
            )
        except (httpx.HTTPStatusError, httpx.HTTPError):
            logger.warning("Error crawling seed %s on %s", seed_name, service)
            continue

        for position, (rid, artist_genres) in enumerate(related):
            # Positional weight: 1st related = 1.0, 2nd = 0.77, 3rd = 0.63, ...
            positional_weight = 1.0 / (1.0 + position * 0.3)
            try:
                tracks = await _fetch_artist_top_tracks(
                    client, service, access_token, rid,
                    developer_token=developer_token,
                    storefront=storefront,
                    limit=songs_per_artist,
                )
            except (httpx.HTTPStatusError, httpx.HTTPError):
                continue
            if service == "spotify" and artist_genres:
                for t in tracks:
                    if not t.get("genre_names"):
                        t["genre_names"] = artist_genres
            for t in tracks:
                t["_discovery_weight"] = positional_weight * _affinity
            candidates.extend(tracks)

    logger.info(
        "Discovered %d tracks via similar_artists on %s (budget=%d)",
        len(candidates), service, total_budget,
    )
    return candidates
```

Update the caller in `service.py:_run_discovery` (line ~658) to pass `(name, score)` tuples:

```python
# In RecommendationService.get_recommendations, replace:
#   seed_artist_names = [a["name"] for a in top_artists_raw[:5] if isinstance(a, dict)]
# with:
seed_scored: list[tuple[str, float]] = [
    (a["name"], float(a.get("score", 0.0)))
    for a in top_artists_raw[:5]
    if isinstance(a, dict) and a.get("name")
]

# And replace the _run_discovery signature + call to pass seed_scored:
#   Pass seed_scored into _run_similar_artists,
#   keep seed_artist_names derived for the other strategies that still take names.
seed_artist_names = [n for n, _ in seed_scored]
```

Inside `_run_discovery`, update the similar_artists runner:

```python
async def _run_similar_artists() -> list[dict[str, Any]]:
    results = await discover_similar_artists(
        service, access_token, seed_scored,
        developer_token=developer_token,
        storefront=storefront,
        total_budget=40,
    )
    results = _filter_by_genre_overlap(results)
    for c in results:
        c["_strategy_source"] = "similar_artist"
    return results
```

Plumb `seed_scored` through `_run_discovery` (add parameter) and propagate from `get_recommendations`.

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_discovery_budget.py tests/test_recommendations.py tests/test_unified_recommendations.py -v
cd backend && uv run pytest tests/ -x
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/musicmind/api/recommendations/fetch.py backend/src/musicmind/api/recommendations/service.py backend/tests/test_discovery_budget.py
git commit -m "$(cat <<'EOF'
feat(recommendations): proportional similar_artist budget + positional discovery_weight

discover_similar_artists now accepts list[tuple[name, affinity_score]] and
allocates per-seed budget proportional to affinity. Seed with affinity 1.0
gets ~20 candidates; seed with 0.2 gets ~4. Candidate pool no longer
dominated by mid-tail seeds.

Adds _discovery_weight = positional_weight * seed_affinity to each track
so downstream scoring can boost tracks from the 1st related artist over
the 5th.

Addresses critique Problems 9 and 11.
EOF
)"
git push origin staging
```

---

## Task 6: Fix chart_filter genre matching with expand_genres

**Problem addressed:** Critique Problem 13. `chart_filter` uses exact-string set intersection on raw genre tags. Apple Music returns `["Hip-Hop/Rap"]` for a drill track; user's top-5 has `["Italian Hip-Hop/Rap", "Drill", ...]` — intersection empty → drill track rejected. `expand_genres` already solves this by extracting parents.

**Files:**
- Create: `backend/tests/test_chart_filter_genres.py`
- Modify: `backend/src/musicmind/api/recommendations/fetch.py:discover_chart_filter`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_chart_filter_genres.py
"""Tests that chart_filter uses expand_genres for near-match genre overlap."""
from __future__ import annotations

from musicmind.api.recommendations.fetch import _genre_overlap_with_expansion


def test_parent_matches_regional() -> None:
    """'Hip-Hop/Rap' chart track matches 'Italian Hip-Hop/Rap' profile."""
    track_genres = ["Hip-Hop/Rap"]
    profile_genres = ["Italian Hip-Hop/Rap", "Drill"]
    assert _genre_overlap_with_expansion(track_genres, profile_genres) is True


def test_regional_matches_parent() -> None:
    """'Italian Hip-Hop/Rap' chart track matches 'Hip-Hop/Rap' profile."""
    track_genres = ["Italian Hip-Hop/Rap"]
    profile_genres = ["Hip-Hop/Rap"]
    assert _genre_overlap_with_expansion(track_genres, profile_genres) is True


def test_no_overlap() -> None:
    """'Country' chart track does not match 'Italian Hip-Hop/Rap' profile."""
    track_genres = ["Country"]
    profile_genres = ["Italian Hip-Hop/Rap", "Drill"]
    assert _genre_overlap_with_expansion(track_genres, profile_genres) is False


def test_case_insensitive() -> None:
    assert _genre_overlap_with_expansion(["POP"], ["pop"]) is True


def test_empty_inputs() -> None:
    assert _genre_overlap_with_expansion([], ["Pop"]) is False
    assert _genre_overlap_with_expansion(["Pop"], []) is False
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_chart_filter_genres.py -v
```
Expected: FAIL — `_genre_overlap_with_expansion` does not exist.

- [ ] **Step 3: Write implementation**

Add helper to `fetch.py` (below the existing helpers):

```python
def _genre_overlap_with_expansion(
    track_genres: list[str],
    profile_genres: list[str],
) -> bool:
    """Check genre overlap after expanding both sides to include parent genres."""
    if not track_genres or not profile_genres:
        return False
    from musicmind.engine.profile import expand_genres
    track_set = {g.lower() for g in expand_genres(track_genres)}
    profile_set = {g.lower() for g in expand_genres(profile_genres)}
    return bool(track_set & profile_set)
```

Modify `discover_chart_filter` to use it. Replace the Apple Music branch filter at line 568–573:

```python
            for chart in chart_data:
                for item in chart.get("data", []):
                    track = _apple_track_to_cache_dict(item)
                    # Use expand_genres for parent/regional near-match.
                    if _genre_overlap_with_expansion(
                        track.get("genre_names", []),
                        list(profile_genres)[:5],
                    ):
                        candidates.append(track)
```

Also update the Spotify branch (line 523–553) so new-release tracks are filtered similarly. After the existing loop that builds `candidates`, add a post-filter:

```python
        if service == "spotify":
            # ... existing fetch logic ...
            # Post-filter by expanded genre overlap.
            profile_genres_list = list(profile_genres)[:5]
            candidates = [
                c for c in candidates
                if _genre_overlap_with_expansion(
                    c.get("genre_names", []),
                    profile_genres_list,
                )
            ]
```

Note: `discover_chart_filter`'s signature already accepts `profile_genres: list[str]` — no signature change needed.

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_chart_filter_genres.py -v
cd backend && uv run pytest tests/ -x
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/musicmind/api/recommendations/fetch.py backend/tests/test_chart_filter_genres.py
git commit -m "$(cat <<'EOF'
fix(chart_filter): use expand_genres for parent/regional near-match

Previously chart_filter did raw string set intersection on genre tags,
rejecting 'Hip-Hop/Rap' chart tracks when the user's profile contained
only 'Italian Hip-Hop/Rap' (and vice versa). Expand both sides to
include parent genres before intersection — matches 'Italian Hip-Hop/Rap'
to 'Hip-Hop/Rap' via shared parent.

Addresses critique Problem 13.
EOF
)"
git push origin staging
```

---

## Post-Execution Verification

After all 6 commits land on `staging`:

- [ ] Railway worker redeploys (auto-trigger from push) and picks up new code
- [ ] Worker logs show `GPU_MODE=LOCAL` route going through `https://8dc8-93-51-254-36.ngrok-free.app` (pre-existing env config)
- [ ] Trigger a manual re-index for the test user; observe:
  - Indexing logs show "processing N/M artists above threshold 0.05" (Task 2)
  - Enrichment count drops ~30% vs previous cycle (Task 4 pre-filter)
  - Cobweb additions reflect log1p-dampened priorities (Task 3)
- [ ] Call `/api/recommendations?strategy=all&limit=20`; verify recommendations now skew toward the user's top-affinity scene rather than mid-tail artists (Task 5)
- [ ] Spot-check a "Hip-Hop/Rap" chart track appears for an "Italian Hip-Hop/Rap" user (Task 6)

**Rollback plan:** each commit is atomic; revert any single one with `git revert <sha>` and push to staging. No DB migration to reverse.

**Deferred (NOT in this plan):**
- Critique Problem 12 (genre search being a string hack — needs Spotify/Apple genre vocabulary mapping, larger scope)
- Critique Problem 14 (editorial year filter — minor, can widen post-hoc)
- Critique Problem 16 (repeat-play detection off-by-one — minor, needs play_count persistence which was dropped in mig 022)
- Explicit multiplicative `score *= (1 + 0.15 * (signal_count - 1))` (current additive cross_bonus in `scorer.py:218-219` already handles this; see analysis note below)

**Analysis note:** scorer.py already implements cross-strategy boost via `cross_bonus = min(0.10, (strategy_count - 1) * 0.05)`. The critique proposes a multiplicative version; the additive is functionally equivalent for the 1–3 strategy-count range we see in practice. Skipping in this plan to stay scoped.

---

## Execution

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration, catches problems early.

**2. Inline Execution** — I execute tasks in this session with checkpoints between each for you to sanity-check.

Given the scope (6 commits touching the core recommendation engine) and the fact that the worker will auto-redeploy after each push, I recommend **Option 2 (Inline with checkpoints)** so you can eyeball the staging logs after each commit before I move on.
