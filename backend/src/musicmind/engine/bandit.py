"""Contextual bandit — Thompson Sampling for adaptive exploration.

Replaces the fixed diversity weight (0.08) with a context-dependent
exploration parameter. Each (user, context) pair maintains a Beta(α, β)
distribution that learns from recommendation outcomes.

Context features: time_of_day (4 buckets), day_of_week (2 buckets),
session_length (3 buckets).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime
from typing import Any

import numpy as np
import sqlalchemy as sa

from musicmind.db.schema import bandit_arms

# ── Context Feature Extraction ─────────────────────────────────────────────


def _time_bucket(hour: int) -> str:
    """Map hour to time-of-day bucket."""
    if 6 <= hour < 12:
        return "morning"
    elif 12 <= hour < 18:
        return "afternoon"
    elif 18 <= hour < 23:
        return "evening"
    return "night"


def _day_bucket(weekday: int) -> str:
    """Map weekday (0=Mon) to bucket."""
    return "weekend" if weekday >= 5 else "weekday"


def _session_bucket(session_length: int) -> str:
    """Map session length to bucket."""
    if session_length <= 3:
        return "short"
    elif session_length <= 10:
        return "medium"
    return "long"


def build_context_key(
    *,
    now: datetime | None = None,
    session_length: int = 0,
) -> str:
    """Build a context key from current conditions.

    The key uniquely identifies the context for bandit arm selection.
    Format: "time_bucket:day_bucket:session_bucket"

    Args:
        now: Current datetime (defaults to UTC now).
        session_length: Number of songs in current session.

    Returns:
        Context key string.
    """
    if now is None:
        now = datetime.now(UTC)
    time_b = _time_bucket(now.hour)
    day_b = _day_bucket(now.weekday())
    session_b = _session_bucket(session_length)
    return f"{time_b}:{day_b}:{session_b}"


# ── Thompson Sampling ──────────────────────────────────────────────────────


def sample_exploration_weight(alpha: float, beta_param: float) -> float:
    """Sample an exploration weight from Beta(α, β).

    Maps the sample to the diversity weight range [0.02, 0.20].

    Args:
        alpha: Success count + prior (≥ 1.0).
        beta_param: Failure count + prior (≥ 1.0).

    Returns:
        Exploration weight in [0.02, 0.20].
    """
    sample = random.betavariate(max(1.0, alpha), max(1.0, beta_param))
    # Map [0, 1] to [0.02, 0.20]
    return 0.02 + sample * 0.18


def update_arm(
    alpha: float,
    beta_param: float,
    reward: float,
) -> tuple[float, float]:
    """Update Beta distribution with observed reward.

    Uses a soft update: α += reward, β += (1 - reward).
    Applies decay to prevent old observations from dominating.

    Args:
        alpha: Current α parameter.
        beta_param: Current β parameter.
        reward: Observed reward in [0, 1].

    Returns:
        Updated (α, β) tuple.
    """
    # Decay existing counts slightly to allow adaptation
    decay = 0.995
    new_alpha = alpha * decay + reward
    new_beta = beta_param * decay + (1.0 - reward)
    return round(new_alpha, 4), round(new_beta, 4)


def feedback_to_reward(feedback_type: str) -> float:
    """Convert user feedback to a bandit reward signal.

    Args:
        feedback_type: One of thumbs_up, thumbs_down, skip,
            added_to_library.

    Returns:
        Reward in [0, 1].
    """
    return {
        "thumbs_up": 0.8,
        "added_to_library": 1.0,
        "thumbs_down": 0.0,
        "skipped": 0.2,
    }.get(feedback_type, 0.5)


# ── Database Operations ────────────────────────────────────────────────────


async def get_arm_state(
    engine,
    *,
    user_id: str,
    context_key: str,
) -> tuple[float, float]:
    """Load arm state from DB, returning (α, β). Defaults to (1.0, 1.0)."""
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(bandit_arms.c.alpha, bandit_arms.c.beta).where(
                sa.and_(
                    bandit_arms.c.user_id == user_id,
                    bandit_arms.c.context_key == context_key,
                )
            )
        )
        row = result.first()

    if row is None:
        return 1.0, 1.0
    return float(row.alpha), float(row.beta)


async def save_arm_state(
    engine,
    *,
    user_id: str,
    context_key: str,
    alpha: float,
    beta_param: float,
) -> None:
    """Save arm state to DB (upsert)."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        existing = await conn.execute(
            sa.select(bandit_arms.c.id).where(
                sa.and_(
                    bandit_arms.c.user_id == user_id,
                    bandit_arms.c.context_key == context_key,
                )
            )
        )
        if existing.first():
            await conn.execute(
                bandit_arms.update()
                .where(
                    sa.and_(
                        bandit_arms.c.user_id == user_id,
                        bandit_arms.c.context_key == context_key,
                    )
                )
                .values(alpha=alpha, beta=beta_param, updated_at=now)
            )
        else:
            await conn.execute(
                bandit_arms.insert().values(
                    user_id=user_id,
                    context_key=context_key,
                    alpha=alpha,
                    beta=beta_param,
                    updated_at=now,
                )
            )


async def sample_diversity_weight(
    engine,
    *,
    user_id: str,
    session_length: int = 0,
) -> tuple[float, str]:
    """Sample diversity weight for current context via Thompson Sampling.

    Returns:
        Tuple of (diversity_weight, context_key).
    """
    context_key = build_context_key(session_length=session_length)
    alpha, beta_param = await get_arm_state(
        engine, user_id=user_id, context_key=context_key,
    )
    weight = sample_exploration_weight(alpha, beta_param)
    return weight, context_key


async def record_bandit_outcome(
    engine,
    *,
    user_id: str,
    context_key: str,
    feedback_type: str,
) -> None:
    """Update bandit arm after observing user feedback.

    Args:
        engine: SQLAlchemy async engine.
        user_id: User ID.
        context_key: Context key used during recommendation.
        feedback_type: User feedback type.
    """
    reward = feedback_to_reward(feedback_type)
    alpha, beta_param = await get_arm_state(
        engine, user_id=user_id, context_key=context_key,
    )
    new_alpha, new_beta = update_arm(alpha, beta_param, reward)
    await save_arm_state(
        engine,
        user_id=user_id,
        context_key=context_key,
        alpha=new_alpha,
        beta_param=new_beta,
    )
