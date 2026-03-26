"""Tests for anti-staleness and cross-strategy bonuses in the scorer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from musicmind.engine.profile import build_taste_profile
from musicmind.engine.scorer import score_candidate

# Reuse test data from test_engine
SONGS = [
    {
        "catalog_id": "1",
        "name": "Test",
        "artist_name": "Artist",
        "genre_names": ["Pop", "Dance"],
        "duration_ms": 200000,
        "release_date": "2024-06-15",
    },
]


class TestStaleness:
    def _profile(self):
        return build_taste_profile(SONGS, [])

    def test_recently_recommended_gets_penalty(self) -> None:
        profile = self._profile()
        candidate = {
            "catalog_id": "c1",
            "name": "Song",
            "artist_name": "New",
            "genre_names": ["Pop"],
            "release_date": "2024-01-01",
        }
        # Without recent recs
        result_fresh = score_candidate(candidate, profile, recent_recommendations=[])
        # With recent rec for this song
        recent = [{"catalog_id": "c1", "created_at": datetime.now(tz=UTC)}]
        result_stale = score_candidate(
            candidate, profile, recent_recommendations=recent
        )
        assert result_stale["_score"] < result_fresh["_score"]
        assert result_stale["_breakdown"]["staleness"] > 0

    def test_old_recommendation_no_penalty(self) -> None:
        profile = self._profile()
        candidate = {
            "catalog_id": "c1",
            "name": "Song",
            "artist_name": "New",
            "genre_names": ["Pop"],
        }
        old_rec = [{
            "catalog_id": "c1",
            "created_at": datetime.now(tz=UTC) - timedelta(days=60),
        }]
        result = score_candidate(candidate, profile, recent_recommendations=old_rec)
        assert result["_breakdown"]["staleness"] == 0.0


class TestCrossStrategy:
    def _profile(self):
        return build_taste_profile(SONGS, [])

    def test_multi_strategy_gets_bonus(self) -> None:
        profile = self._profile()
        candidate = {
            "catalog_id": "c1",
            "name": "Song",
            "artist_name": "New",
            "genre_names": ["Pop"],
            "_strategy_count": 3,
        }
        result = score_candidate(candidate, profile)
        assert result["_breakdown"]["cross_strategy_bonus"] > 0

    def test_single_strategy_no_bonus(self) -> None:
        profile = self._profile()
        candidate = {
            "catalog_id": "c1",
            "name": "Song",
            "artist_name": "New",
            "genre_names": ["Pop"],
            "_strategy_count": 1,
        }
        result = score_candidate(candidate, profile)
        assert result["_breakdown"]["cross_strategy_bonus"] == 0.0
