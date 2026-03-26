"""Tests for the adaptive weight optimizer."""

from __future__ import annotations

from musicmind.engine.weights import (
    DEFAULT_WEIGHTS,
    MIN_FEEDBACK_FOR_OPTIMIZATION,
    feedback_to_target,
    optimize_weights,
)


class TestDefaultWeights:
    def test_sum_to_one(self) -> None:
        total = sum(DEFAULT_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01

    def test_has_required_keys(self) -> None:
        required = {"genre", "artist", "novelty", "freshness", "diversity", "audio", "staleness"}
        assert set(DEFAULT_WEIGHTS.keys()) == required

    def test_all_positive(self) -> None:
        assert all(v > 0 for v in DEFAULT_WEIGHTS.values())


class TestFeedbackToTarget:
    def test_thumbs_up(self) -> None:
        assert feedback_to_target("thumbs_up") == 1.0

    def test_thumbs_down(self) -> None:
        assert feedback_to_target("thumbs_down") == 0.0

    def test_added_to_library(self) -> None:
        assert feedback_to_target("added_to_library") == 1.0

    def test_skipped(self) -> None:
        assert feedback_to_target("skipped") == 0.2

    def test_unknown(self) -> None:
        assert feedback_to_target("unknown") == 0.5


class TestOptimizeWeights:
    def test_empty_feedback_returns_defaults(self) -> None:
        result = optimize_weights([])
        assert result == DEFAULT_WEIGHTS

    def test_insufficient_feedback_returns_defaults(self) -> None:
        feedback = [
            {"feedback_type": "thumbs_up", "predicted_score": 0.8}
            for _ in range(MIN_FEEDBACK_FOR_OPTIMIZATION - 1)
        ]
        result = optimize_weights(feedback)
        assert result == DEFAULT_WEIGHTS

    def test_sufficient_feedback_returns_valid_weights(self) -> None:
        feedback = [
            {"feedback_type": "thumbs_up", "predicted_score": 0.9}
            for _ in range(15)
        ] + [
            {"feedback_type": "thumbs_down", "predicted_score": 0.3}
            for _ in range(5)
        ]
        result = optimize_weights(feedback)

        # Must sum to 1.0
        assert abs(sum(result.values()) - 1.0) < 0.01
        # All non-negative
        assert all(v >= 0 for v in result.values())
        # Has same keys
        assert set(result.keys()) == set(DEFAULT_WEIGHTS.keys())

    def test_no_predicted_scores_returns_defaults(self) -> None:
        feedback = [
            {"feedback_type": "thumbs_up"}  # no predicted_score
            for _ in range(20)
        ]
        result = optimize_weights(feedback)
        assert result == DEFAULT_WEIGHTS
