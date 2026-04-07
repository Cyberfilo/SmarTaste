"""Adaptive weight system — context-adaptive + feedback-learned weights.

Two layers:
1. Context-adaptive: weights shift based on user profile characteristics
   (regional concentration, audio availability, calibration presence).
   Works from day one, no feedback needed.
2. Feedback-learned: coordinate descent on per-dimension breakdowns.
   Requires 10+ feedback entries with stored breakdowns.
"""

from __future__ import annotations

from typing import Any

import numpy as np

DEFAULT_WEIGHTS: dict[str, float] = {
    "genre": 0.35,
    "audio": 0.25,
    "artist": 0.20,
    "language": 0.20,
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
    "audio": "audio_similarity",
    "artist": "artist_match",
    "language": "language_match",
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
    - Regional concentration: users with >50% one language get higher language weight
    - Audio availability: if enriched features exist, audio weight increases
    - Calibration presence: if user did onboarding, artist weight increases
    - Mood mode: if mood filter active, audio becomes dominant

    Returns normalized weights summing to 1.0.
    """
    genre_vector = profile.get("genre_vector", {})

    # ── Measure regional concentration ─────────────────────
    regional_strength = _measure_regional_strength(genre_vector)

    # ── Base weights, then shift ───────────────────────────
    w_genre = 0.35
    w_audio = 0.25
    w_artist = 0.20
    w_language = 0.20

    # Regional listeners: boost language, reduce genre (they overlap)
    if regional_strength > 0.6:
        # Strong regional: e.g. 90% Italian → language matters a lot
        boost = min(0.15, (regional_strength - 0.6) * 0.375)
        w_language += boost
        w_genre -= boost * 0.5  # Take some from genre (language subsumes genre)
        w_audio -= boost * 0.5  # Take some from audio
    elif regional_strength < 0.2:
        # Global listener: language almost irrelevant
        w_language = 0.05
        w_genre += 0.10
        w_audio += 0.05

    # Audio features available: boost audio, take from language
    if has_audio:
        w_audio += 0.05
        w_language -= 0.03
        w_genre -= 0.02

    # Calibration exists: boost artist (we have explicit user signal)
    if has_calibration:
        w_artist += 0.05
        w_genre -= 0.03
        w_language -= 0.02

    # Mood active: audio becomes dominant for matching the vibe
    if mood_active:
        w_audio = max(w_audio, 0.40)
        remaining = 1.0 - w_audio
        other = w_genre + w_artist + w_language
        ratio = remaining / other if other > 0 else 1.0
        w_genre *= ratio
        w_artist *= ratio
        w_language *= ratio

    return _normalize_weights({
        "genre": w_genre,
        "audio": w_audio,
        "artist": w_artist,
        "language": w_language,
    })


def _measure_regional_strength(genre_vector: dict[str, float]) -> float:
    """Measure how regional/language-concentrated the user's taste is.

    Returns 0.0 (completely global) to 1.0 (entirely one region).
    Looks at genre names with regional prefixes (Italian, French, Korean, etc.)
    and sums their weight in the genre vector.
    """
    if not genre_vector:
        return 0.0

    regional_weight = 0.0
    for genre, weight in genre_vector.items():
        parts = genre.split()
        if len(parts) >= 2 and parts[0][0].isupper() and len(parts[0]) > 1:
            regional_weight += weight
        elif "-" in genre and len(genre.split("-")[0]) <= 2:
            # K-Pop, J-Rock style
            regional_weight += weight

    return min(1.0, regional_weight)


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
