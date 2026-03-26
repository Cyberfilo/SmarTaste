"""Adaptive weight optimizer — learns scoring weights from user feedback.

Uses coordinate descent grid search to minimize MSE between predicted
scores and feedback-derived targets. Falls back to defaults when
insufficient feedback exists.
"""

from __future__ import annotations

from typing import Any

import numpy as np

DEFAULT_WEIGHTS: dict[str, float] = {
    "genre": 0.25,
    "artist": 0.15,
    "novelty": 0.13,
    "freshness": 0.12,
    "diversity": 0.10,
    "audio": 0.15,
    "staleness": 0.10,
}

MIN_FEEDBACK_FOR_OPTIMIZATION = 10

FEEDBACK_TARGETS: dict[str, float] = {
    "thumbs_up": 1.0,
    "thumbs_down": 0.0,
    "added_to_library": 1.0,
    "skipped": 0.2,
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


def optimize_weights(
    feedback: list[dict[str, Any]],
    current_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Optimize scoring weights from feedback data.

    Uses coordinate descent: for each weight dimension, try a range
    of values and keep the one that minimizes MSE against feedback targets.

    Returns default weights if insufficient feedback.
    """
    if len(feedback) < MIN_FEEDBACK_FOR_OPTIMIZATION:
        return dict(DEFAULT_WEIGHTS)

    weights = dict(current_weights or DEFAULT_WEIGHTS)
    dimensions = list(weights.keys())

    # Build targets and predicted scores from feedback
    targets = []
    predicted = []
    for fb in feedback:
        target = feedback_to_target(fb.get("feedback_type", ""))
        pred = fb.get("predicted_score")
        if pred is not None:
            targets.append(target)
            predicted.append(pred)

    if len(targets) < MIN_FEEDBACK_FOR_OPTIMIZATION:
        return dict(DEFAULT_WEIGHTS)

    targets_arr = np.array(targets)

    # Coordinate descent: 3 passes
    for _ in range(3):
        for dim in dimensions:
            best_mse = float("inf")
            best_val = weights[dim]

            # Try values from 0.05 to 0.30
            for trial in np.linspace(0.05, 0.30, 11):
                test_weights = dict(weights)
                test_weights[dim] = float(trial)
                test_weights = _normalize_weights(test_weights)

                # Recompute predicted scores using weight ratios
                # Simple model: adjust predicted by weight ratio change
                ratio = test_weights[dim] / max(weights[dim], 0.01)
                adjusted = np.array(predicted) * (0.7 + 0.3 * ratio)
                adjusted = np.clip(adjusted, 0.0, 1.0)

                mse = float(np.mean((adjusted - targets_arr) ** 2))
                if mse < best_mse:
                    best_mse = mse
                    best_val = float(trial)

            weights[dim] = best_val

    return _normalize_weights(weights)
