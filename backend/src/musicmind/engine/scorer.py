"""Candidate scoring — rank catalog songs against a taste profile.

Uses 6 weighted dimensions: CLAP embedding cosine, MERT embedding cosine,
EffNet embedding cosine, genre cosine, scalar audio similarity, and artist
affinity. Plus additive bonuses (calibration, cross-strategy, mood) and
penalties (diversity, staleness).

Re-ranker (V 6.386): the greedy selection uses a DPP-inspired
quality-weighted gaussian diversity kernel on CLAP embeddings plus a
Steck-style genre-distribution calibration penalty. Both are post-hoc
and degrade to legacy metadata MMR when embeddings are unavailable.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import numpy as np

from musicmind.engine.profile import expand_genres
from musicmind.engine.weights import DEFAULT_WEIGHTS

# ── Re-ranker constants (V 6.386) ────────────────────────────────────────────
# DPP gaussian kernel bandwidth on CLAP cosine distance. σ=0.5 gives a sharp
# penalty for near-duplicates (cos≈0.9 → ~0.98) while fading quickly for
# moderately-similar pairs (cos≈0.5 → ~0.61).
_DPP_SIGMA = 0.5
# How strongly to pull list composition toward the user's genre distribution.
# Applied as a subtractive penalty on the adjusted score during greedy
# selection. 0.08 tuned to be noticeable without dominating quality signal.
_CALIBRATION_WEIGHT = 0.08
# Smoothing added to both distributions in KL(P||Q) so zero-probability
# genres don't blow up. Small enough to preserve relative differences.
_KL_SMOOTHING = 0.01


def _genre_cosine(
    song_genres: list[str],
    genre_vector: dict[str, float],
) -> float:
    """Cosine similarity between a song's genres and the taste profile genre vector.

    Regional genre prioritization: original genre names get full weight (1.0),
    expanded parent genres get reduced weight (0.3). This ensures a song tagged
    "Italian Hip-Hop/Rap" scores much higher against a profile dominated by
    "Italian Hip-Hop/Rap" than a song tagged just "Hip-Hop/Rap".
    """
    if not song_genres or not genre_vector:
        return 0.0

    originals = set(song_genres)
    expanded = expand_genres(song_genres)
    all_genres = set(genre_vector.keys()) | set(expanded)
    profile_vec = np.array([genre_vector.get(g, 0.0) for g in all_genres])

    # Build song vector: original genres get 1.0, expanded parents get 0.3
    raw_weights = {}
    for g in expanded:
        if g in originals:
            raw_weights[g] = 1.0
        else:
            raw_weights[g] = 0.3
    total_w = sum(raw_weights.values())
    song_vec = np.array([
        raw_weights.get(g, 0.0) / total_w if total_w > 0 else 0.0
        for g in all_genres
    ])

    dot = np.dot(profile_vec, song_vec)
    norm_a = np.linalg.norm(profile_vec)
    norm_b = np.linalg.norm(song_vec)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def _build_staleness_index(
    recent_recommendations: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Pre-index recent recommendations by catalog_id for O(1) lookup."""
    index: dict[str, dict[str, Any]] = {}
    for rec in recent_recommendations:
        cid = rec.get("catalog_id")
        if cid and cid not in index:
            index[cid] = rec
    return index


def _compute_staleness(
    catalog_id: str,
    staleness_index: dict[str, dict[str, Any]],
) -> float:
    """Compute staleness penalty based on recent recommendations.

    Returns 0.0 (no penalty) to 0.8 (recently recommended).
    Uses pre-built index for O(1) lookup instead of linear scan.
    """
    rec = staleness_index.get(catalog_id)
    if rec is None:
        return 0.0

    created = rec.get("created_at")
    if created is None:
        return 0.0
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except (ValueError, TypeError):
            return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)

    now = datetime.now(tz=UTC)
    age_days = (now - created).total_seconds() / 86400.0
    if age_days < 7:
        return 0.8
    elif age_days < 30:
        return 0.4
    return 0.0


def _build_user_genre_distribution(
    genre_vector: dict[str, float] | None,
) -> dict[str, float]:
    """Normalize the user's genre_vector into a probability distribution.

    The profile's genre_vector already carries relative weights; this just
    L1-normalizes them so sum == 1.0. Empty input → empty dict (calibration
    will noop).
    """
    if not genre_vector:
        return {}
    total = sum(float(v) for v in genre_vector.values() if v > 0)
    if total <= 0:
        return {}
    return {g: float(v) / total for g, v in genre_vector.items() if v > 0}


def _genres_with_parents(genre_names: list[str]) -> dict[str, float]:
    """Return weighted genre counts: 1.0 for originals, 0.3 for expanded parents.

    Mirrors the weighting used in `_genre_cosine` so calibration tracks the
    same signal the per-track scorer uses.
    """
    if not genre_names:
        return {}
    originals = set(genre_names)
    out: dict[str, float] = {}
    for g in expand_genres(genre_names):
        out[g] = out.get(g, 0.0) + (1.0 if g in originals else 0.3)
    return out


def _kl_divergence(
    p: dict[str, float],
    q: dict[str, float],
    smoothing: float = _KL_SMOOTHING,
) -> float:
    """KL(P || Q) with additive smoothing on both distributions.

    Used by the Steck-style calibration re-ranker. Low values = P aligned
    with Q. Smoothing prevents ∞ from zero-probability genres; it also
    makes the metric less twitchy on small lists.
    """
    if not p or not q:
        return 0.0
    keys = set(p.keys()) | set(q.keys())
    kl = 0.0
    for k in keys:
        pk = p.get(k, 0.0) + smoothing
        qk = q.get(k, 0.0) + smoothing
        if pk > 0 and qk > 0:
            kl += pk * math.log(pk / qk)
    return max(0.0, kl)


def _calibration_penalty(
    candidate_genres: list[str],
    running_genre_counts: dict[str, float],
    target_dist: dict[str, float],
) -> float:
    """Return KL divergence of the list-including-candidate vs user target.

    Smaller = better. Caller subtracts `weight × this` from the adjusted
    score. `running_genre_counts` is a running sum (weighted by parent-of
    expansion rules) across the already-selected tracks; this function
    virtually adds the candidate without mutating it.
    """
    if not target_dist:
        return 0.0
    cand = _genres_with_parents(candidate_genres)
    if not cand and not running_genre_counts:
        return 0.0
    merged = defaultdict(float, running_genre_counts)
    for g, w in cand.items():
        merged[g] += w
    total = sum(merged.values())
    if total <= 0:
        return 0.0
    running_dist = {g: w / total for g, w in merged.items()}
    return _kl_divergence(running_dist, target_dist)


def _dpp_diversity_penalty(
    candidate_clap: list[float] | None,
    candidate_score: float,
    selected: list[dict[str, Any]],
    clap_map: dict[str, list[float]] | None,
    *,
    sigma: float = _DPP_SIGMA,
) -> float | None:
    """Quality-weighted gaussian diversity kernel on CLAP embeddings.

    Returns the max penalty across already-selected tracks, or None when
    the candidate lacks CLAP (caller should fall back to metadata MMR).

    Kernel: `q_cand × q_sel × exp(−(1−cos_sim)² / (2·σ²))`
      - high-similarity pairs produce strong penalty (kernel ≈ 1)
      - quality weighting ensures low-quality candidates can't "buy"
        diversity credit by being far from everyone
    """
    if not candidate_clap or not selected or not clap_map:
        return None
    cand_vec = np.asarray(candidate_clap, dtype=np.float32)
    cand_norm = float(np.linalg.norm(cand_vec))
    if cand_norm == 0:
        return None

    max_penalty = 0.0
    any_clap = False
    for s in selected:
        s_clap = clap_map.get(s.get("catalog_id", ""))
        if not s_clap:
            continue
        any_clap = True
        s_vec = np.asarray(s_clap, dtype=np.float32)
        s_norm = float(np.linalg.norm(s_vec))
        if s_norm == 0:
            continue
        sim = float(np.dot(cand_vec, s_vec) / (cand_norm * s_norm))
        sim = max(-1.0, min(1.0, sim))
        dist = 1.0 - sim
        kernel = math.exp(-(dist * dist) / (2.0 * sigma * sigma))
        q_sel = float(s.get("_score", 0.5))
        penalty = candidate_score * q_sel * kernel
        if penalty > max_penalty:
            max_penalty = penalty

    return max_penalty if any_clap else None


def _best_centroid_similarity(
    candidate_emb: list[float] | None,
    centroids: list[list[float]] | None,
    sim_fn,
) -> float:
    """Return the highest cosine similarity across multiple taste centroids."""
    if not candidate_emb or not centroids:
        return 0.0
    return max(sim_fn(c, candidate_emb) for c in centroids)


def score_candidate(
    candidate: dict[str, Any],
    profile: dict[str, Any],
    already_selected: list[dict[str, Any]] | None = None,
    *,
    weights: dict[str, float] | None = None,
    audio_features: dict[str, Any] | None = None,
    user_audio_centroid: dict[str, float] | None = None,
    candidate_embedding: list[float] | None = None,
    user_embedding_centroid: list[float] | None = None,
    candidate_clap: list[float] | None = None,
    user_clap_centroid: list[float] | None = None,
    candidate_mert: list[float] | None = None,
    user_mert_centroid: list[float] | None = None,
    recent_recommendations: list[dict[str, Any]] | None = None,
    staleness_index: dict[str, dict[str, Any]] | None = None,
    calibration_artists: dict[str, float] | None = None,
    nearest_tracks: list[dict[str, Any]] | None = None,
    candidate_mood_tags: list[str] | None = None,
    candidate_mood_scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Score a single candidate song against a taste profile.

    Six weighted dimensions:
    - CLAP cosine (0.30) — holistic audio-text similarity
    - MERT cosine (0.25) — musical structure (pitch, rhythm)
    - EffNet cosine (0.15) — genre/style granularity
    - Genre match (0.15) — Apple Music / Spotify genre cosine
    - Scalar audio (0.10) — normalized euclidean on tempo/energy/danceability
    - Artist affinity (0.05) — library presence + calibration

    Multi-centroid: when the profile contains clap_centroids / mert_centroids /
    effnet_centroids (lists of cluster centroids), similarity is computed
    against the *nearest* cluster. Falls back to the single centroid.

    Returns the candidate dict augmented with:
    - _score: overall score (0-1)
    - _breakdown: per-dimension scores
    - _explanation: human-readable explanation
    """
    from musicmind.engine.similarity import (
        clap_similarity,
        effnet_similarity,
        mert_similarity,
        scalar_audio_similarity,
    )

    w = weights or DEFAULT_WEIGHTS
    genre_vector = profile.get("genre_vector", {})
    top_artists = profile.get("top_artists", [])

    # ── 1. Embedding similarities (multi-centroid aware) ──────
    clap_centroids = profile.get("clap_centroids")
    mert_centroids = profile.get("mert_centroids")
    effnet_centroids = profile.get("effnet_centroids")

    clap_score = 0.0
    has_clap = candidate_clap is not None and (
        user_clap_centroid is not None or clap_centroids
    )
    if has_clap:
        if clap_centroids:
            clap_score = _best_centroid_similarity(
                candidate_clap, clap_centroids, clap_similarity,
            )
        else:
            clap_score = clap_similarity(user_clap_centroid, candidate_clap)

    mert_score = 0.0
    has_mert = candidate_mert is not None and (
        user_mert_centroid is not None or mert_centroids
    )
    if has_mert:
        if mert_centroids:
            mert_score = _best_centroid_similarity(
                candidate_mert, mert_centroids, mert_similarity,
            )
        else:
            mert_score = mert_similarity(user_mert_centroid, candidate_mert)

    effnet_score = 0.0
    has_effnet = candidate_embedding is not None and (
        user_embedding_centroid is not None or effnet_centroids
    )
    if has_effnet:
        if effnet_centroids:
            effnet_score = _best_centroid_similarity(
                candidate_embedding, effnet_centroids, effnet_similarity,
            )
        else:
            effnet_score = effnet_similarity(
                user_embedding_centroid, candidate_embedding,
            )

    # ── 2. Genre match (cosine similarity) ────────────────
    genre_score = _genre_cosine(
        candidate.get("genre_names", []), genre_vector
    )

    # ── 3. Scalar audio match ─────────────────────────────
    scalar_score = 0.0
    has_scalar = (
        audio_features is not None
        and user_audio_centroid is not None
    )
    if has_scalar:
        scalar_score = scalar_audio_similarity(user_audio_centroid, audio_features)

    # ── 3a. Tempo-band match (V 6.393) ────────────────────
    # Psychologically-calibrated tempo-zone match vs raw-BPM euclidean.
    # A 140-BPM user-library baseline rewards 145-BPM candidates even if
    # overall scalar distance is moderate, while docking a 95-BPM
    # candidate that scalar would score as closer on non-tempo axes.
    tempo_band_score = 0.0
    user_band_dist = profile.get("tempo_band_distribution")
    cand_tempo = (audio_features or {}).get("tempo") if audio_features else None
    has_tempo_band = bool(user_band_dist) and cand_tempo is not None
    if has_tempo_band:
        from musicmind.engine.tempo import tempo_band_similarity
        tempo_band_score = tempo_band_similarity(user_band_dist, cand_tempo)

    # ── 3b. Halftime-feel bonus (V 6.393) ─────────────────
    # Drill/trap halftime signature: kick-on-1 / snare-on-3 / fast hats
    # on a 130-160 BPM grid. When the user's library baseline >30%
    # halftime (check on the scored profile) and the candidate reads
    # halftime, apply a small additive bonus — NOT a dim, since the
    # signal is binary-ish and sparsely present. Capped at +0.03.
    halftime_bonus = 0.0
    user_halftime_ratio = float(profile.get("halftime_ratio") or 0.0)
    if user_halftime_ratio >= 0.25 and audio_features:
        from musicmind.engine.tempo import halftime_feel_score
        cand_halftime = halftime_feel_score(
            audio_features.get("tempo"),
            audio_features.get("beat_strength"),
            audio_features.get("danceability"),
        )
        if cand_halftime >= 0.35:
            # Scale bonus by min(user_ratio, cand_halftime_score) so a
            # user whose library is 60% halftime + candidate scoring 0.8
            # gets the full +0.03; weaker signals on either side fade.
            halftime_bonus = min(0.03, user_halftime_ratio * cand_halftime * 0.05)

    # ── 3b. Mood match (V 6.388 → V 6.389 hybrid) ─────────
    # Cosine similarity between the user's library-aggregated mood
    # distribution and the candidate's mood signal. Prefers the V 6.389
    # sparse score vector (carries intensity — "strongly reflective" vs
    # "faintly reflective"); falls back to the V 6.388 tag list with
    # positional weights for tracks classified before the upgrade.
    mood_score = 0.0
    user_mood_dist = profile.get("mood_distribution") or None
    has_mood = bool(
        user_mood_dist and (candidate_mood_scores or candidate_mood_tags)
    )
    if has_mood:
        from musicmind.engine.mood_tagger import mood_similarity
        mood_score = mood_similarity(
            user_mood_dist,
            candidate_scores=candidate_mood_scores,
            candidate_tags=candidate_mood_tags,
        )

    # ── 4. Artist affinity — three-tier match, wrong-genre penalised ─────
    # Tier 1 (primary match): candidate's primary artist is in user's top —
    #         full score, e.g. "Capo Plaza" on a Capo Plaza track.
    # Tier 2 (feat match): candidate's primary isn't in top, but one of its
    #         featured artists is — score discounted by FEATURING_WEIGHT=0.3.
    #         e.g. a "Lacrim feat. Simba La Rue" track where the user has
    #         Simba La Rue in their library but not Lacrim.
    # Tier 3 (no match): neither → 0.
    # The "candidate's primary was feat'd on library tracks" case is already
    # handled by build_artist_affinity, which injects feat artists into
    # top_artists with a 0.3 weight during profile computation.
    from musicmind.engine.profile import parse_artists
    raw_artist_str = candidate.get("artist_name", "")
    # Primary artist lowercase — used by the calibration-boost lookup below.
    # V 6.377 removed the direct `artist_name` binding when rewriting the
    # match loop; the cal branch still needed it and broke at runtime.
    _parsed_for_primary = parse_artists(raw_artist_str)
    artist_name = (
        _parsed_for_primary[0][0].lower() if _parsed_for_primary else ""
    )
    artist_scores = {a["name"].lower(): a["score"] for a in top_artists}

    artist_match = 0.0
    matched_as = ""
    for name, component_weight in parse_artists(raw_artist_str):
        score = artist_scores.get(name.lower(), 0.0)
        if score <= 0:
            continue
        weighted = score * component_weight  # primary 1.0, feat 0.3
        if weighted > artist_match:
            artist_match = weighted
            matched_as = "primary" if component_weight >= 1.0 else "feat"

    if artist_match > 0 and genre_score < 0.2:
        artist_match *= 0.3

    # ── 5. Diversity penalty (MMR-style) ──────────────────
    diversity_penalty = 0.0
    if already_selected:
        from musicmind.engine.similarity import song_similarity

        max_sim = max(
            song_similarity(candidate, s) for s in already_selected
        )
        diversity_penalty = max_sim * 0.3

    # ── 6. Staleness penalty ──────────────────────────────
    if staleness_index is not None:
        staleness = _compute_staleness(
            candidate.get("catalog_id", ""), staleness_index
        )
    else:
        recs = recent_recommendations or []
        idx = _build_staleness_index(recs) if recs else {}
        staleness = _compute_staleness(candidate.get("catalog_id", ""), idx)

    # ── 7. Cross-strategy bonus ───────────────────────────
    strategy_count = candidate.get("_strategy_count", 1)
    cross_bonus = min(0.10, max(0, (strategy_count - 1)) * 0.05)

    # ── 7b. Discovery-weight bonus ────────────────────────
    # Cross-strategy bonus already counts duplicate sources; discovery_weight
    # captures positional + seed affinity from the similar_artist crawl. Cap
    # the bonus so it stays additive (max +0.04 at perfect alignment).
    discovery_weight = float(candidate.get("_discovery_weight", 0.0))
    discovery_bonus = max(0.0, min(0.04, discovery_weight * 0.04))

    # ── 8. Mood boost (set by filter_candidates_by_mood) ──
    mood_boost = candidate.get("_mood_boost", 0.0)

    # ── 9. Calibration boost (V 6.397: rank-sensitive) ────
    # Calibration rows carry decreasing weights by wizard rank:
    #   top_artist pinned → 5.0
    #   artist_rank #1    → 3.0
    #   artist_rank #10   → 1.2
    #   artist_rank tail  → 1.0
    # Prior scaling `min(0.20, w*0.03)` flattened this to a 0.03-0.15
    # range — too narrow to differentiate rank #1 from rank #10. Bumped
    # to `min(0.25, w*0.05)` so the boost gradient is:
    #   pinned (w=5) → 0.25, rank #1 (w=3) → 0.15, rank #5 (w=2.2) →
    #   0.11, tail (w=1) → 0.05. Rank position now meaningfully shapes
    #   the recommendation surface.
    cal_boost = 0.0
    if calibration_artists:
        cal_weight = calibration_artists.get(artist_name, 0.0)
        if cal_weight > 0:
            cal_boost = min(0.25, cal_weight * 0.05)
            if genre_score < 0.15:
                cal_boost = 0.0

    # ── 10. Recency boost ──────────────────────────────────
    recency_boost = 0.0
    release_date = candidate.get("release_date", "")
    if release_date and len(release_date) >= 10:
        try:
            from datetime import UTC, datetime
            rd = datetime.fromisoformat(release_date).replace(tzinfo=UTC)
            age_days = (datetime.now(tz=UTC) - rd).days
            if age_days < 30:
                recency_boost = 0.02
            elif age_days < 90:
                recency_boost = 0.01
        except (ValueError, TypeError):
            pass

    # ── Weighted combination with graceful degradation ────
    dim_scores: dict[str, float] = {}
    dim_weights: dict[str, float] = {}

    if has_clap:
        dim_scores["clap"] = clap_score
        dim_weights["clap"] = w.get("clap", 0.30)
    if has_mert:
        dim_scores["mert"] = mert_score
        dim_weights["mert"] = w.get("mert", 0.25)
    if has_effnet:
        dim_scores["effnet"] = effnet_score
        dim_weights["effnet"] = w.get("effnet", 0.15)

    dim_scores["genre"] = genre_score
    dim_weights["genre"] = w.get("genre", 0.15)
    dim_scores["artist"] = artist_match
    dim_weights["artist"] = w.get("artist", 0.05)

    if has_scalar:
        dim_scores["scalar"] = scalar_score
        dim_weights["scalar"] = w.get("scalar", 0.06)
    if has_tempo_band:
        dim_scores["tempo_band"] = tempo_band_score
        dim_weights["tempo_band"] = w.get("tempo_band", 0.08)
    if has_mood:
        dim_scores["mood_match"] = mood_score
        dim_weights["mood_match"] = w.get("mood_match", 0.10)

    w_total = sum(dim_weights.values())
    if w_total > 0:
        dim_weights = {k: v / w_total for k, v in dim_weights.items()}

    overall = sum(
        dim_weights[k] * dim_scores[k] for k in dim_scores
    )

    overall += cross_bonus
    overall += discovery_bonus
    overall += mood_boost * 0.1
    overall += cal_boost
    overall += recency_boost
    overall += halftime_bonus  # V 6.393
    overall -= 0.05 * diversity_penalty
    overall -= 0.03 * staleness

    overall = max(0.0, min(1.0, overall))

    # ── V 6.393: BRECVEMA-channel explanation ─────────────
    # Juslin & Västfjäll's eight-mechanism taxonomy collapses for scorer
    # purposes into three interpretable channels: acoustic (brain-stem +
    # rhythmic entrainment), scene (evaluative conditioning + identity),
    # and affective (contagion + mood). We surface the top contributing
    # channels by weighted score — the attribution mirrors the model's
    # actual decision process instead of hand-tuned thresholds.
    explanation = _render_brecvema_explanation(
        candidate=candidate,
        dim_scores=dim_scores,
        dim_weights=dim_weights,
        artist_matched_as=matched_as,
        nearest_tracks=nearest_tracks,
        user_band_dist=user_band_dist,
        cand_tempo=cand_tempo,
        halftime_bonus=halftime_bonus,
        recency_boost=recency_boost,
        cal_boost=cal_boost,
        cross_bonus=cross_bonus,
        strategy_count=strategy_count,
    )

    return {
        **candidate,
        "_score": round(overall, 3),
        "_breakdown": {
            "clap_similarity": round(clap_score, 3),
            "mert_similarity": round(mert_score, 3),
            "effnet_similarity": round(effnet_score, 3),
            "genre_match": round(genre_score, 3),
            "scalar_similarity": round(scalar_score, 3),
            "tempo_band_similarity": round(tempo_band_score, 3),
            "mood_match": round(mood_score, 3),
            "artist_match": round(artist_match, 3),
            "calibration_boost": round(cal_boost, 3),
            "recency_boost": round(recency_boost, 3),
            "halftime_bonus": round(halftime_bonus, 3),
            "diversity_penalty": round(diversity_penalty, 3),
            "staleness": round(staleness, 3),
            "cross_strategy_bonus": round(cross_bonus, 3),
            "discovery_bonus": round(discovery_bonus, 3),
            "mood_boost": round(mood_boost, 3),
        },
        "_explanation": explanation,
    }


# ── BRECVEMA Explanation Renderer (V 6.393) ──────────────────────────────
# Maps the seven-dim score breakdown onto Juslin & Västfjäll's psychological
# channels. Instead of "similar to X; sounds like your taste; genre match,"
# the output reads "Sonic fit (CLAP 0.86, tempo in your 140-BPM drill zone);
# Scene affinity (Shiva is your #2 listened artist)." Each channel's
# contribution to the final score drives its narrative priority — the
# explanation is literally a sorted view of the scorer's own reasoning.


_TEMPO_BAND_LABELS = {
    "resting": "slow (60-80 BPM)",
    "walking": "mid-tempo (80-110 BPM)",
    "brisk": "up-tempo (110-130 BPM)",
    "fast_motor": "driving (130-160 BPM)",
    "driven": "fast (160+ BPM)",
}


def _top_user_tempo_band(user_band_dist: dict[str, float] | None) -> str | None:
    if not user_band_dist:
        return None
    top = max(user_band_dist.items(), key=lambda kv: kv[1], default=None)
    if top and top[1] >= 0.30:
        return top[0]
    return None


def _render_brecvema_explanation(
    *,
    candidate: dict[str, Any],
    dim_scores: dict[str, float],
    dim_weights: dict[str, float],
    artist_matched_as: str,
    nearest_tracks: list[dict[str, Any]] | None,
    user_band_dist: dict[str, float] | None,
    cand_tempo: float | None,
    halftime_bonus: float,
    recency_boost: float,
    cal_boost: float,
    cross_bonus: float,
    strategy_count: int,
) -> str:
    """Build a 1-3 clause explanation from the top contributing channels.

    Channel = weighted group of dims:
      • acoustic  := clap + mert + effnet + tempo_band + scalar
      • scene     := artist + genre
      • affective := mood_match
      • identity  := artist_match when matched as 'primary' (self-signalling)
      • novelty   := cross_bonus + discovery (surfaces when prominent)
    """
    # Per-channel weighted contribution to the final score.
    contrib: dict[str, float] = {
        "acoustic": sum(
            dim_scores.get(k, 0.0) * dim_weights.get(k, 0.0)
            for k in ("clap", "mert", "effnet", "tempo_band", "scalar")
        ),
        "scene": sum(
            dim_scores.get(k, 0.0) * dim_weights.get(k, 0.0)
            for k in ("genre", "artist")
        ),
        "affective": dim_scores.get("mood_match", 0.0)
                     * dim_weights.get("mood_match", 0.0),
    }
    # Sort channels high → low; only speak about ones pulling their weight.
    ordered = sorted(contrib.items(), key=lambda kv: kv[1], reverse=True)

    clauses: list[str] = []

    for channel, score in ordered[:2]:
        if score < 0.05:
            continue
        clauses.append(
            _acoustic_clause(
                candidate=candidate,
                dim_scores=dim_scores,
                user_band_dist=user_band_dist,
                cand_tempo=cand_tempo,
                halftime_bonus=halftime_bonus,
                nearest_tracks=nearest_tracks,
            ) if channel == "acoustic"
            else _scene_clause(
                candidate=candidate,
                dim_scores=dim_scores,
                matched_as=artist_matched_as,
            ) if channel == "scene"
            else _affective_clause(candidate=candidate, dim_scores=dim_scores)
        )

    # Novelty / recency / calibration appended when they cross a threshold —
    # they're modulators, not primary reasons.
    if cal_boost >= 0.05:
        clauses.append("top calibrated artist")
    elif recency_boost > 0 and candidate.get("release_date"):
        clauses.append(f"released {candidate['release_date'][:7]}")
    if cross_bonus >= 0.05 and strategy_count >= 2:
        clauses.append(f"matched by {strategy_count} discovery paths")

    clauses = [c for c in clauses if c]
    if not clauses:
        return "moderate match"
    return "; ".join(clauses)


def _acoustic_clause(
    *,
    candidate: dict[str, Any],
    dim_scores: dict[str, float],
    user_band_dist: dict[str, float] | None,
    cand_tempo: float | None,
    halftime_bonus: float,
    nearest_tracks: list[dict[str, Any]] | None,
) -> str:
    """Sonic-fit narrative: lead with nearest track when available, then
    tempo-band + halftime annotations when they're doing real work."""
    bits: list[str] = []

    if nearest_tracks:
        nt = nearest_tracks[0]
        bits.append(
            f"sounds like {nt.get('name', '?')} by {nt.get('artist_name', '?')}"
        )
    elif dim_scores.get("clap", 0) >= 0.7:
        bits.append("sonic fit with your library")

    top_band = _top_user_tempo_band(user_band_dist)
    tb_score = dim_scores.get("tempo_band", 0.0)
    if top_band and tb_score >= 0.5 and cand_tempo:
        label = _TEMPO_BAND_LABELS.get(top_band, top_band)
        bits.append(f"{int(round(cand_tempo))} BPM in your {label} zone")

    if halftime_bonus >= 0.015:
        bits.append("halftime feel you tend to favor")

    if not bits:
        return ""
    return bits[0] if len(bits) == 1 else f"{bits[0]} ({', '.join(bits[1:])})"


def _scene_clause(
    *,
    candidate: dict[str, Any],
    dim_scores: dict[str, float],
    matched_as: str,
) -> str:
    """Cultural / scene narrative — artist loyalty > genre match."""
    artist_name = candidate.get("artist_name", "")
    artist_score = dim_scores.get("artist", 0.0)

    if artist_score >= 0.5:
        if matched_as == "feat":
            return f"features an artist you listen to"
        return f"{artist_name} is in your top artists"

    genre_score = dim_scores.get("genre", 0.0)
    if genre_score >= 0.4:
        top_g = ", ".join((candidate.get("genre_names") or [])[:2])
        if top_g:
            return f"{top_g} matches your genre footprint"

    return ""


def _affective_clause(
    *,
    candidate: dict[str, Any],
    dim_scores: dict[str, float],
) -> str:
    """Mood / affective narrative."""
    mood_score = dim_scores.get("mood_match", 0.0)
    if mood_score < 0.5:
        return ""
    tags = candidate.get("mood_tags") or []
    if tags:
        return f"mood fit ({tags[0]})"
    return "mood fit with your library"


def _find_nearest_tracks(
    candidate_clap: list[float] | None,
    user_library_claps: dict[str, dict[str, Any]] | None,
    top_n: int = 2,
) -> list[dict[str, Any]]:
    """Find the user's library tracks most similar to the candidate."""
    if not candidate_clap or not user_library_claps:
        return []
    import numpy as np
    c_arr = np.array(candidate_clap)
    c_norm = np.linalg.norm(c_arr)
    if c_norm == 0:
        return []
    c_arr = c_arr / c_norm

    scored = []
    for cid, info in user_library_claps.items():
        emb = info.get("clap")
        if not emb:
            continue
        e_arr = np.array(emb)
        e_norm = np.linalg.norm(e_arr)
        if e_norm == 0:
            continue
        sim = float(np.dot(c_arr, e_arr / e_norm))
        scored.append((sim, info))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s[1] for s in scored[:top_n]]


def rank_candidates(
    candidates: list[dict[str, Any]],
    profile: dict[str, Any],
    count: int = 20,
    *,
    weights: dict[str, float] | None = None,
    audio_features_map: dict[str, dict[str, Any]] | None = None,
    user_audio_centroid: dict[str, float] | None = None,
    embedding_map: dict[str, list[float]] | None = None,
    user_embedding_centroid: list[float] | None = None,
    clap_map: dict[str, list[float]] | None = None,
    user_clap_centroid: list[float] | None = None,
    mert_map: dict[str, list[float]] | None = None,
    user_mert_centroid: list[float] | None = None,
    recent_recommendations: list[dict[str, Any]] | None = None,
    calibration_artists: dict[str, float] | None = None,
    user_library_claps: dict[str, dict[str, Any]] | None = None,
    mood_tags_map: dict[str, list[str]] | None = None,
    mood_scores_map: dict[str, dict[str, float]] | None = None,
    # Legacy params — accepted but ignored for backward compat
    user_tag_profile: dict[str, float] | None = None,
    collaborative_matches: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates using MMR-style scoring with diversity.

    Optimized: precomputes base scores (without diversity penalty), then
    applies diversity penalty incrementally during greedy selection.
    """
    if not candidates:
        return []

    af_map = audio_features_map or {}
    emb_map = embedding_map or {}
    staleness_idx = _build_staleness_index(recent_recommendations or [])

    base_scored: list[dict[str, Any]] = []
    for c in candidates:
        cid = c.get("catalog_id", "")
        c_clap = (clap_map or {}).get(cid)

        nearest = _find_nearest_tracks(
            c_clap, user_library_claps,
        ) if user_library_claps else None

        c_moods = (mood_tags_map or {}).get(cid) or c.get("mood_tags")
        c_scores = (mood_scores_map or {}).get(cid) or c.get("mood_scores")
        scored = score_candidate(
            c, profile, already_selected=None,
            weights=weights,
            audio_features=af_map.get(cid),
            user_audio_centroid=user_audio_centroid,
            candidate_embedding=emb_map.get(cid),
            user_embedding_centroid=user_embedding_centroid,
            candidate_clap=c_clap,
            user_clap_centroid=user_clap_centroid,
            candidate_mert=(mert_map or {}).get(cid),
            user_mert_centroid=user_mert_centroid,
            candidate_mood_tags=c_moods,
            candidate_mood_scores=c_scores,
            staleness_index=staleness_idx,
            calibration_artists=calibration_artists,
            nearest_tracks=nearest,
        )
        base_scored.append(scored)

    # Step 2: Greedy re-rank — DPP-style diversity + Steck calibration (V 6.386)
    # Falls back to legacy metadata MMR for candidates without CLAP.
    # V 6.394: discovery_disposition + taste_shape modulate diversity weight,
    # DPP kernel bandwidth, Steck calibration weight, and the Wundt stretch
    # target that actively rewards the "calibrated-novelty" distance band.
    from musicmind.engine.similarity import song_similarity

    w = weights or DEFAULT_WEIGHTS
    diversity_weight_base = w.get("diversity", 0.10)
    target_genre_dist = _build_user_genre_distribution(
        profile.get("genre_vector"),
    )

    # ── V 6.394: personality-adaptive re-rank params ──────────────────
    # Disposition ∈ [0,1]; default 0.3 means a moderate explorer profile.
    disposition = float(profile.get("discovery_disposition") or 0.30)
    disposition = max(0.0, min(1.0, disposition))
    taste_info = profile.get("taste_shape") or {}
    shape = (
        taste_info.get("shape") if isinstance(taste_info, dict) else "mixed"
    ) or "mixed"

    # Diversity weight: explorers pay more for diversity (+50% at disp=1.0),
    # exploiters pay less (−30% at disp=0.0). Range roughly [0.07, 0.15].
    diversity_weight = diversity_weight_base * (0.7 + disposition * 0.8)

    # DPP kernel bandwidth: wider for explorers (accepts more distant
    # already-selected tracks), narrower for exploiters (cluster tight).
    dpp_sigma = _DPP_SIGMA * (0.85 + disposition * 0.4)

    # Steck calibration weight: univores want depth in their favorite
    # genres — don't force spread; omnivores expect genre breadth;
    # mixed keeps the default. Applied multiplicatively.
    cal_scale = {"univore": 0.5, "mixed": 1.0, "omnivore": 1.2}.get(
        shape, 1.0,
    )
    cal_weight_eff = _CALIBRATION_WEIGHT * cal_scale

    # Wundt stretch target — CLAP distance from centroid the scorer
    # actively wants the rec slate to occupy. 0.15 = near centroid (safe);
    # 0.50 = firmly outside the comfort zone (adventurous). The stretch
    # bonus is a gaussian peaked on this target (σ=0.15) scaled to ±0.025.
    stretch_target = 0.15 + disposition * 0.35
    stretch_bonus_weight = 0.025
    stretch_sigma = 0.15

    def _wundt_stretch(cand_clap: list[float] | None) -> float:
        """Bonus for occupying the user's calibrated-novelty distance band."""
        if not cand_clap or not user_clap_centroid:
            return 0.0
        cand_vec = np.asarray(cand_clap, dtype=np.float32)
        ctr_vec = np.asarray(user_clap_centroid, dtype=np.float32)
        cn = float(np.linalg.norm(cand_vec))
        xn = float(np.linalg.norm(ctr_vec))
        if cn == 0 or xn == 0:
            return 0.0
        sim = float(np.dot(cand_vec, ctr_vec) / (cn * xn))
        sim = max(-1.0, min(1.0, sim))
        distance = 1.0 - sim  # cosine distance
        peak = math.exp(
            -((distance - stretch_target) ** 2) / (2.0 * stretch_sigma ** 2)
        )
        return stretch_bonus_weight * peak

    selected: list[dict[str, Any]] = []
    remaining = list(base_scored)
    running_genre_counts: dict[str, float] = defaultdict(float)

    cmap = clap_map or {}

    for _ in range(min(count, len(base_scored))):
        best_idx = -1
        best_score = -1.0
        best_div_penalty = 0.0
        best_cal_penalty = 0.0
        best_stretch = 0.0

        for i, c in enumerate(remaining):
            base_score = c["_score"]
            cid = c.get("catalog_id", "")
            c_clap = cmap.get(cid)

            # DPP-style quality-weighted gaussian diversity penalty on CLAP.
            # Returns None when CLAP data is unavailable for either the
            # candidate or all of the already-selected tracks.
            dpp_penalty = _dpp_diversity_penalty(
                c_clap, base_score, selected, cmap,
                sigma=dpp_sigma,
            ) if selected else None

            if dpp_penalty is not None:
                diversity_penalty = dpp_penalty
            elif selected:
                # Fallback: legacy metadata-based MMR.
                max_sim = max(song_similarity(c, s) for s in selected)
                diversity_penalty = max_sim * 0.3
            else:
                diversity_penalty = 0.0

            # Steck calibration — KL(running∪cand || user target dist).
            cal_penalty = _calibration_penalty(
                c.get("genre_names", []),
                running_genre_counts,
                target_genre_dist,
            )

            # V 6.394: Wundt-curve stretch bonus — explicit tolerance-calibrated
            # novelty injection. Peaks at (disposition-scaled) CLAP distance.
            stretch = _wundt_stretch(c_clap)

            adjusted = (
                base_score
                - diversity_weight * diversity_penalty
                - cal_weight_eff * cal_penalty
                + stretch
            )
            adjusted = max(0.0, min(1.0, adjusted))

            if adjusted > best_score:
                best_score = adjusted
                best_idx = i
                best_div_penalty = diversity_penalty
                best_cal_penalty = cal_penalty
                best_stretch = stretch

        best = remaining.pop(best_idx)
        best["_score"] = round(best_score, 3)
        best["_breakdown"]["diversity_penalty"] = round(best_div_penalty, 3)
        best["_breakdown"]["calibration_kl"] = round(best_cal_penalty, 4)
        best["_breakdown"]["wundt_stretch"] = round(best_stretch, 4)
        selected.append(best)

        # Update running genre distribution with the chosen track.
        for g, wt in _genres_with_parents(best.get("genre_names", [])).items():
            running_genre_counts[g] += wt

    return selected
