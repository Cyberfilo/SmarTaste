"""Tests for Phase 8: Contextual bandit."""

from __future__ import annotations

import pytest

from musicmind.engine.bandit import (
    _day_bucket,
    _session_bucket,
    _time_bucket,
    build_context_key,
    feedback_to_reward,
    sample_exploration_weight,
    update_arm,
)


# ── Context feature tests ─────────────────────────────────────────────────


def test_time_bucket_morning() -> None:
    assert _time_bucket(8) == "morning"
    assert _time_bucket(6) == "morning"
    assert _time_bucket(11) == "morning"


def test_time_bucket_afternoon() -> None:
    assert _time_bucket(12) == "afternoon"
    assert _time_bucket(15) == "afternoon"


def test_time_bucket_evening() -> None:
    assert _time_bucket(18) == "evening"
    assert _time_bucket(22) == "evening"


def test_time_bucket_night() -> None:
    assert _time_bucket(0) == "night"
    assert _time_bucket(3) == "night"
    assert _time_bucket(23) == "night"


def test_day_bucket() -> None:
    assert _day_bucket(0) == "weekday"  # Monday
    assert _day_bucket(4) == "weekday"  # Friday
    assert _day_bucket(5) == "weekend"  # Saturday
    assert _day_bucket(6) == "weekend"  # Sunday


def test_session_bucket() -> None:
    assert _session_bucket(0) == "short"
    assert _session_bucket(3) == "short"
    assert _session_bucket(5) == "medium"
    assert _session_bucket(10) == "medium"
    assert _session_bucket(15) == "long"


def test_build_context_key_format() -> None:
    """Context key has 3 parts separated by colons."""
    from datetime import UTC, datetime
    now = datetime(2024, 6, 15, 14, 30, tzinfo=UTC)  # Saturday afternoon
    key = build_context_key(now=now, session_length=5)
    parts = key.split(":")
    assert len(parts) == 3
    assert parts[0] == "afternoon"
    assert parts[1] == "weekend"
    assert parts[2] == "medium"


# ── Thompson Sampling tests ───────────────────────────────────────────────


def test_sample_exploration_weight_range() -> None:
    """Sampled weight is in valid range [0.02, 0.20]."""
    for _ in range(100):
        w = sample_exploration_weight(1.0, 1.0)
        assert 0.02 <= w <= 0.20


def test_sample_high_alpha_biases_high() -> None:
    """High alpha (many successes) biases toward higher weights."""
    samples = [sample_exploration_weight(100.0, 1.0) for _ in range(100)]
    avg = sum(samples) / len(samples)
    assert avg > 0.15  # Should be biased high


def test_sample_high_beta_biases_low() -> None:
    """High beta (many failures) biases toward lower weights."""
    samples = [sample_exploration_weight(1.0, 100.0) for _ in range(100)]
    avg = sum(samples) / len(samples)
    assert avg < 0.06  # Should be biased low


# ── Arm update tests ──────────────────────────────────────────────────────


def test_update_arm_positive() -> None:
    """Positive reward increases alpha."""
    alpha, beta = update_arm(1.0, 1.0, reward=1.0)
    assert alpha > 1.0
    assert beta < 1.0


def test_update_arm_negative() -> None:
    """Negative reward increases beta."""
    alpha, beta = update_arm(1.0, 1.0, reward=0.0)
    assert alpha < 1.0
    assert beta > 1.0


def test_update_arm_decay() -> None:
    """Decay prevents old observations from dominating."""
    alpha, beta = 100.0, 1.0
    for _ in range(100):
        alpha, beta = update_arm(alpha, beta, reward=0.0)
    # After many negative rewards, beta should have grown
    assert beta > alpha


# ── Reward mapping tests ─────────────────────────────────────────────────


def test_feedback_rewards() -> None:
    assert feedback_to_reward("thumbs_up") == 0.8
    assert feedback_to_reward("thumbs_down") == 0.0
    assert feedback_to_reward("added_to_library") == 1.0
    assert feedback_to_reward("skipped") == 0.2
    assert feedback_to_reward("unknown") == 0.5
