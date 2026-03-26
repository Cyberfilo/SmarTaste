"""Tests for context-aware mood filtering."""

from __future__ import annotations

from musicmind.engine.mood import (
    MOOD_PROFILES,
    filter_candidates_by_mood,
    get_mood_profile,
)


class TestMoodProfiles:
    def test_known_moods_exist(self) -> None:
        for mood in ["workout", "chill", "focus", "party", "sad", "driving"]:
            assert get_mood_profile(mood) is not None

    def test_unknown_returns_none(self) -> None:
        assert get_mood_profile("nonexistent") is None

    def test_case_insensitive(self) -> None:
        assert get_mood_profile("WORKOUT") is not None
        assert get_mood_profile("Chill") is not None

    def test_all_profiles_have_names(self) -> None:
        for name, profile in MOOD_PROFILES.items():
            assert profile.name == name


class TestFilterCandidatesByMood:
    def test_prefers_matching_genres(self) -> None:
        candidates = [
            {"catalog_id": "1", "genre_names": ["Hip-Hop/Rap", "Drill"]},
            {"catalog_id": "2", "genre_names": ["Classical", "Chamber Music"]},
            {"catalog_id": "3", "genre_names": ["Electronic", "Dance"]},
        ]
        result = filter_candidates_by_mood(candidates, "workout")
        # Hip-Hop and Electronic should rank higher than Classical
        ids = [c["catalog_id"] for c in result]
        assert "2" not in ids or ids.index("2") > ids.index("1")

    def test_never_filters_to_empty(self) -> None:
        candidates = [
            {"catalog_id": "1", "genre_names": ["Classical"]},
            {"catalog_id": "2", "genre_names": ["Classical"]},
        ]
        result = filter_candidates_by_mood(candidates, "workout")
        assert len(result) >= 1

    def test_unknown_mood_returns_all(self) -> None:
        candidates = [
            {"catalog_id": "1", "genre_names": ["Pop"]},
            {"catalog_id": "2", "genre_names": ["Rock"]},
        ]
        result = filter_candidates_by_mood(candidates, "nonexistent")
        assert len(result) == 2
        assert all(c["_mood_boost"] == 0.0 for c in result)

    def test_audio_features_used_when_available(self) -> None:
        candidates = [
            {"catalog_id": "1", "genre_names": ["Electronic"]},
            {"catalog_id": "2", "genre_names": ["Electronic"]},
        ]
        audio = {
            "1": {"energy": 0.9, "tempo": 140.0, "beat_strength": 0.8},
            "2": {"energy": 0.1, "tempo": 60.0, "beat_strength": 0.1},
        }
        result = filter_candidates_by_mood(candidates, "workout", audio)
        # Song 1 should have higher mood boost (high energy = workout match)
        boosts = {c["catalog_id"]: c["_mood_boost"] for c in result}
        if "1" in boosts and "2" in boosts:
            assert boosts["1"] > boosts["2"]

    def test_min_keep_ratio(self) -> None:
        candidates = [
            {"catalog_id": str(i), "genre_names": ["Classical"]}
            for i in range(10)
        ]
        result = filter_candidates_by_mood(
            candidates, "workout", min_keep_ratio=0.5
        )
        assert len(result) >= 5
