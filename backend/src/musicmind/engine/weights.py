"""Adaptive weight optimizer — learns scoring weights from user feedback.

Uses coordinate descent to minimize MSE between predicted scores and
feedback-derived targets. Each trial weight set recomputes the predicted
score from stored per-dimension breakdowns, giving accurate gradients.

Falls back to defaults when insufficient feedback exists.
"""

from __future__ import annotations

from typing import Any

import numpy as np

DEFAULT_WEIGHTS: dict[str, float] = {
    "genre": 0.35,
    "audio": 0.20,
    "novelty": 0.12,
    "freshness": 0.10,
    "diversity": 0.08,
    "artist": 0.08,
    "staleness": 0.07,
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
    "genre": "genre_match",
    "artist": "artist_match",
    "audio": "audio_similarity",
    "novelty": "novelty",
    "freshness": "freshness",
    "diversity": "diversity_penalty",
    "staleness": "staleness",
}


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
    """Recompute overall score from per-dimension breakdown and weights.

    This mirrors the weighted combination in score_candidate() but uses
    stored breakdown values, giving accurate predictions for any weight set.
    """
    score = 0.0
    for weight_key, breakdown_key in _BREAKDOWN_MAP.items():
        w = weights.get(weight_key, 0.0)
        dim_val = breakdown.get(breakdown_key, 0.0)
        # diversity and staleness are penalties: score uses (1 - penalty)
        if weight_key in ("diversity", "staleness"):
            score += w * (1.0 - dim_val)
        else:
            score += w * dim_val

    # Cross-strategy bonus and mood boost are additive, not weight-dependent
    score += breakdown.get("cross_strategy_bonus", 0.0)
    score += breakdown.get("mood_boost", 0.0) * 0.1

    return max(0.0, min(1.0, score))


def optimize_weights(
    feedback: list[dict[str, Any]],
    current_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Optimize scoring weights from feedback data.

    Uses coordinate descent: for each weight dimension, try a range of values
    and keep the one that minimizes MSE against feedback targets. Recomputes
    predicted scores from stored breakdowns for accurate gradients.

    Returns default weights if insufficient feedback.
    """
    if len(feedback) < MIN_FEEDBACK_FOR_OPTIMIZATION:
        return dict(DEFAULT_WEIGHTS)

    weights = dict(current_weights or DEFAULT_WEIGHTS)
    dimensions = list(weights.keys())

    # Build targets and breakdowns from feedback
    targets: list[float] = []
    breakdowns: list[dict[str, float]] = []
    for fb in feedback:
        target = feedback_to_target(fb.get("feedback_type", ""))
        breakdown = fb.get("breakdown")
        if breakdown is not None:
            targets.append(target)
            breakdowns.append(breakdown)
        elif fb.get("predicted_score") is not None:
            # Fallback for legacy feedback without breakdown: use predicted_score
            targets.append(target)
            breakdowns.append({})

    if len(targets) < MIN_FEEDBACK_FOR_OPTIMIZATION:
        return dict(DEFAULT_WEIGHTS)

    targets_arr = np.array(targets)
    has_breakdowns = any(b for b in breakdowns)

    # If no breakdowns stored yet, return defaults (can't optimize accurately)
    if not has_breakdowns:
        return dict(DEFAULT_WEIGHTS)

    # Coordinate descent: 3 passes with early stopping
    prev_mse = float("inf")
    for _ in range(3):
        for dim in dimensions:
            best_mse = float("inf")
            best_val = weights[dim]

            # Try values from 0.03 to 0.40
            for trial in np.linspace(0.03, 0.40, 15):
                test_weights = dict(weights)
                test_weights[dim] = float(trial)
                test_weights = _normalize_weights(test_weights)

                # Recompute predicted scores from breakdowns
                predicted = np.array([
                    _recompute_score(bd, test_weights) if bd else 0.5
                    for bd in breakdowns
                ])

                mse = float(np.mean((predicted - targets_arr) ** 2))
                if mse < best_mse:
                    best_mse = mse
                    best_val = float(trial)

            weights[dim] = best_val

        # Early stopping: if MSE didn't improve meaningfully
        current_mse = best_mse
        if abs(prev_mse - current_mse) < 1e-6:
            break
        prev_mse = current_mse

    return _normalize_weights(weights)
