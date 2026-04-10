"""Adaptive weight system — context-adaptive + feedback-learned weights.

Two layers:
1. Context-adaptive: weights shift based on user profile characteristics
   (embedding availability, calibration presence, mood mode).
   Works from day one, no feedback needed.
2. Feedback-learned: coordinate descent on per-dimension breakdowns.
   Requires 10+ feedback entries with stored breakdowns.
"""

from __future__ import annotations

from typing import Any

import numpy as np

DEFAULT_WEIGHTS: dict[str, float] = {
    "clap": 0.30,
    "mert": 0.25,
    "effnet": 0.15,
    "genre": 0.15,
    "scalar": 0.10,
    "artist": 0.05,
}

MIN_FEEDBACK_FOR_OPTIMIZATION = 10

FEEDBACK_TARGETS: dict[str, float] = {
    "thumbs_up": 1.0,
    "thumbs_down": 0.0,
    "added_to_library": 1.0,
    "skipped": 0.2,
}

# Maps weight keys to breakdown keys for score recomputation
_BREAKDOWN_MAP: dict[str, str] = {
    "clap": "clap_similarity",
    "mert": "mert_similarity",
    "effnet": "effnet_similarity",
    "genre": "genre_match",
    "scalar": "scalar_similarity",
    "artist": "artist_match",
    "diversity": "diversity_penalty",
    "staleness": "staleness",
}


# ── Context-Adaptive Weights ─────────────────────────────────────────────


def compute_context_weights(
    profile: dict[str, Any],
    *,
    has_audio: bool = False,
    has_calibration: bool = False,
    mood_active: bool = False,
) -> dict[str, float]:
    """Compute scoring weights adapted to this user's profile characteristics.

    Shifts weights based on:
    - Embedding availability: CLAP/MERT/EffNet centroids present → boost those
    - Audio scalar availability: if enriched features exist, scalar weight increases
    - Calibration presence: if user did onboarding, artist weight increases
    - Mood mode: if mood filter active, CLAP + scalar become dominant

    Returns normalized weights summing to 1.0.
    """
    # ── Base weights ──────────────────────────────────────
    w_clap = 0.30
    w_mert = 0.25
    w_effnet = 0.15
    w_genre = 0.15
    w_scalar = 0.10
    w_artist = 0.05

    # Check which embedding centroids are available in the profile
    has_clap = profile.get("clap_centroid") is not None
    has_mert = profile.get("mert_centroid") is not None
    has_effnet = profile.get("embedding_centroid") is not None

    # If embeddings are missing, redistribute their weight to genre + scalar
    if not has_clap:
        redistribute = w_clap
        w_clap = 0.0
        w_genre += redistribute * 0.6
        w_scalar += redistribute * 0.4

    if not has_mert:
        redistribute = w_mert
        w_mert = 0.0
        w_genre += redistribute * 0.6
        w_scalar += redistribute * 0.4

    if not has_effnet:
        redistribute = w_effnet
        w_effnet = 0.0
        w_genre += redistribute * 0.6
        w_scalar += redistribute * 0.4

    # Audio scalar features available: boost scalar weight slightly
    if has_audio:
        w_scalar += 0.05
        w_genre -= 0.03
        w_artist -= 0.02

    # Calibration exists: boost artist
    if has_calibration:
        w_artist += 0.05
        w_genre -= 0.03
        w_scalar -= 0.02

    # Mood active: CLAP (holistic vibe) + scalar become dominant
    if mood_active:
        w_clap = max(w_clap, 0.35)
        w_scalar = max(w_scalar, 0.20)
        remaining = 1.0 - w_clap - w_scalar
        other = w_mert + w_effnet + w_genre + w_artist
        ratio = remaining / other if other > 0 else 1.0
        w_mert *= ratio
        w_effnet *= ratio
        w_genre *= ratio
        w_artist *= ratio

    return _normalize_weights({
        "clap": w_clap,
        "mert": w_mert,
        "effnet": w_effnet,
        "genre": w_genre,
        "scalar": w_scalar,
        "artist": w_artist,
    })


# ── Feedback-Learned Weights ──────────────────────────────────────────────


def feedback_to_target(feedback_type: str) -> float:
    """Map feedback type to a target score (0-1)."""
    return FEEDBACK_TARGETS.get(feedback_type, 0.5)


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Normalize weights to sum to 1.0 with minimum floor of 0.03."""
    min_weight = 0.03
    clamped = {k: max(v, min_weight) for k, v in weights.items()}
    total = sum(clamped.values())
    return {k: round(v / total, 4) for k, v in clamped.items()}


def _recompute_score(
    breakdown: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Recompute overall score from per-dimension breakdown and weights."""
    score = 0.0
    for weight_key, breakdown_key in _BREAKDOWN_MAP.items():
        w = weights.get(weight_key, 0.0)
        dim_val = breakdown.get(breakdown_key, 0.0)
        if weight_key in ("diversity", "staleness"):
            score += w * (1.0 - dim_val)
        else:
            score += w * dim_val

    score += breakdown.get("cross_strategy_bonus", 0.0)
    score += breakdown.get("mood_boost", 0.0) * 0.1

    return max(0.0, min(1.0, score))


def optimize_weights(
    feedback: list[dict[str, Any]],
    current_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Optimize scoring weights from feedback data.

    Uses coordinate descent: for each weight dimension, try a range of values
    and keep the one that minimizes MSE against feedback targets.

    Returns default weights if insufficient feedback.
    """
    if len(feedback) < MIN_FEEDBACK_FOR_OPTIMIZATION:
        return dict(DEFAULT_WEIGHTS)

    weights = dict(current_weights or DEFAULT_WEIGHTS)
    dimensions = list(weights.keys())

    targets: list[float] = []
    breakdowns: list[dict[str, float]] = []
    for fb in feedback:
        target = feedback_to_target(fb.get("feedback_type", ""))
        breakdown = fb.get("breakdown")
        if breakdown is not None:
            targets.append(target)
            breakdowns.append(breakdown)
        elif fb.get("predicted_score") is not None:
            targets.append(target)
            breakdowns.append({})

    if len(targets) < MIN_FEEDBACK_FOR_OPTIMIZATION:
        return dict(DEFAULT_WEIGHTS)

    targets_arr = np.array(targets)
    has_breakdowns = any(b for b in breakdowns)

    if not has_breakdowns:
        return dict(DEFAULT_WEIGHTS)

    prev_mse = float("inf")
    for _ in range(3):
        for dim in dimensions:
            best_mse = float("inf")
            best_val = weights[dim]

            for trial in np.linspace(0.03, 0.40, 15):
                test_weights = dict(weights)
                test_weights[dim] = float(trial)
                test_weights = _normalize_weights(test_weights)

                predicted = np.array([
                    _recompute_score(bd, test_weights) if bd else 0.5
                    for bd in breakdowns
                ])

                mse = float(np.mean((predicted - targets_arr) ** 2))
                if mse < best_mse:
                    best_mse = mse
                    best_val = float(trial)

            weights[dim] = best_val

        current_mse = best_mse
        if abs(prev_mse - current_mse) < 1e-6:
            break
        prev_mse = current_mse

    return _normalize_weights(weights)
