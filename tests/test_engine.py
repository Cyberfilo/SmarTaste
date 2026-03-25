"""Tests for the taste engine — profile building, similarity, and scoring."""

from __future__ import annotations

import pytest

from musicmind.engine.profile import (
    build_artist_affinity,
    build_audio_trait_preferences,
    build_genre_vector,
    build_release_year_distribution,
    build_taste_profile,
    compute_familiarity_score,
    expand_genres,
)
from musicmind.engine.scorer import rank_candidates, score_candidate
from musicmind.engine.similarity import genre_jaccard, song_similarity, year_proximity


# ── Test Data ─────────────────────────────────────────────────────────

SONGS = [
    {
        "catalog_id": "1",
        "name": "Drill Anthem",
        "artist_name": "Rondo",
        "genre_names": ["Italian Hip-Hop/Rap", "Drill"],
        "duration_ms": 180000,
        "release_date": "2024-06-15",
        "audio_traits": ["lossless", "atmos"],
        "user_rating": 1,
    },
    {
        "catalog_id": "2",
        "name": "Summer Pop",
        "artist_name": "Pop Star",
        "genre_names": ["Pop", "Dance"],
        "duration_ms": 210000,
        "release_date": "2024-03-01",
        "audio_traits": ["lossless"],
        "user_rating": None,
    },
    {
        "catalog_id": "3",
        "name": "Trap Banger",
        "artist_name": "Rondo",
        "genre_names": ["Hip-Hop/Rap", "Trap"],
        "duration_ms": 200000,
        "release_date": "2023-11-20",
        "audio_traits": ["lossless", "spatial"],
        "user_rating": 1,
    },
    {
        "catalog_id": "4",
        "name": "Chill Vibes",
        "artist_name": "Lo-Fi Guy",
        "genre_names": ["Electronic", "Ambient"],
        "duration_ms": 240000,
        "release_date": "2022-01-10",
        "audio_traits": [],
        "user_rating": None,
    },
]

HISTORY = [
    {"song_id": "1", "song_name": "Drill Anthem", "artist_name": "Rondo",
     "genre_names": ["Italian Hip-Hop/Rap", "Drill"]},
    {"song_id": "1", "song_name": "Drill Anthem", "artist_name": "Rondo",
     "genre_names": ["Italian Hip-Hop/Rap", "Drill"]},
    {"song_id": "3", "song_name": "Trap Banger", "artist_name": "Rondo",
     "genre_names": ["Hip-Hop/Rap", "Trap"]},
    {"song_id": "2", "song_name": "Summer Pop", "artist_name": "Pop Star",
     "genre_names": ["Pop", "Dance"]},
]


# ── Genre Expansion ───────────────────────────────────────────────────

class TestExpandGenres:
    def test_simple_genre(self) -> None:
        assert "Pop" in expand_genres(["Pop"])

    def test_hierarchical_split(self) -> None:
        result = expand_genres(["Italian Hip-Hop/Rap"])
        assert "Italian Hip-Hop/Rap" in result
        assert "Hip-Hop/Rap" in result

    def test_multiple_genres(self) -> None:
        result = expand_genres(["Pop", "Italian Hip-Hop/Rap"])
        assert "Pop" in result
        assert "Italian Hip-Hop/Rap" in result
        assert "Hip-Hop/Rap" in result  # parent extracted by stripping prefix

    def test_empty(self) -> None:
        assert expand_genres([]) == []


# ── Genre Vector ──────────────────────────────────────────────────────

class TestGenreVector:
    def test_basic_vector(self) -> None:
        vector = build_genre_vector(SONGS, [])
        assert len(vector) > 0
        assert abs(sum(vector.values()) - 1.0) < 0.001

    def test_history_boosts_genres(self) -> None:
        vector_no_hist = build_genre_vector(SONGS, [])
        vector_with_hist = build_genre_vector(SONGS, HISTORY)

        # Drill appears in history (song_id "1" played 2x + repeated)
        # so its absolute count should increase, making it a larger share
        drill_no = vector_no_hist.get("Drill", 0)
        drill_with = vector_with_hist.get("Drill", 0)
        assert drill_with > drill_no

    def test_empty_input(self) -> None:
        assert build_genre_vector([], []) == {}

    def test_sums_to_one(self) -> None:
        vector = build_genre_vector(SONGS, HISTORY)
        assert abs(sum(vector.values()) - 1.0) < 0.001


# ── Artist Affinity ───────────────────────────────────────────────────

class TestArtistAffinity:
    def test_top_artist(self) -> None:
        artists = build_artist_affinity(SONGS, HISTORY)
        assert len(artists) > 0
        # Rondo should be top (2 library songs + loved + 3 history entries)
        assert artists[0]["name"] == "Rondo"
        assert artists[0]["score"] == 1.0

    def test_empty(self) -> None:
        assert build_artist_affinity([], []) == []


# ── Release Year Distribution ─────────────────────────────────────────

class TestReleaseYearDistribution:
    def test_distribution(self) -> None:
        dist = build_release_year_distribution(SONGS)
        assert "2024" in dist
        assert dist["2024"] > dist.get("2022", 0)  # two 2024 songs vs one 2022

    def test_sums_to_one(self) -> None:
        dist = build_release_year_distribution(SONGS)
        assert abs(sum(dist.values()) - 1.0) < 0.01


# ── Audio Trait Preferences ───────────────────────────────────────────

class TestAudioTraitPreferences:
    def test_preferences(self) -> None:
        prefs = build_audio_trait_preferences(SONGS)
        # 3 out of 4 songs have lossless
        assert prefs["lossless"] == 0.75

    def test_empty(self) -> None:
        assert build_audio_trait_preferences([]) == {}


# ── Familiarity Score ─────────────────────────────────────────────────

class TestFamiliarityScore:
    def test_concentrated_taste(self) -> None:
        # All in one genre → low familiarity
        vector = {"Hip-Hop/Rap": 0.95, "Pop": 0.05}
        score = compute_familiarity_score(vector)
        assert score < 0.5

    def test_diverse_taste(self) -> None:
        # Evenly spread → high familiarity
        vector = {"Pop": 0.25, "Hip-Hop": 0.25, "Rock": 0.25, "Electronic": 0.25}
        score = compute_familiarity_score(vector)
        assert score > 0.9

    def test_empty(self) -> None:
        assert compute_familiarity_score({}) == 0.0

    def test_single_genre(self) -> None:
        assert compute_familiarity_score({"Pop": 1.0}) == 0.0


# ── Full Taste Profile ───────────────────────────────────────────────

class TestBuildTasteProfile:
    def test_builds_complete_profile(self) -> None:
        profile = build_taste_profile(SONGS, HISTORY)
        assert "genre_vector" in profile
        assert "top_artists" in profile
        assert "release_year_distribution" in profile
        assert "audio_trait_preferences" in profile
        assert "familiarity_score" in profile
        assert profile["total_songs_analyzed"] == 4
        assert profile["listening_hours_estimated"] > 0


# ── Song Similarity ───────────────────────────────────────────────────

class TestSongSimilarity:
    def test_identical_songs(self) -> None:
        score = song_similarity(SONGS[0], SONGS[0])
        assert score > 0.8  # 0.85 typical: all dimensions match

    def test_same_artist_different_song(self) -> None:
        score = song_similarity(SONGS[0], SONGS[2])  # Both Rondo, Hip-Hop adjacent
        assert score > 0.5

    def test_different_genres(self) -> None:
        score = song_similarity(SONGS[0], SONGS[3])  # Drill vs Ambient
        assert score < 0.5

    def test_genre_jaccard_overlap(self) -> None:
        score = genre_jaccard(["Pop", "Dance"], ["Pop", "Rock"])
        assert 0 < score < 1

    def test_genre_jaccard_no_overlap(self) -> None:
        assert genre_jaccard(["Pop"], ["Metal"]) == 0.0

    def test_year_proximity_same_year(self) -> None:
        assert year_proximity("2024-01-01", "2024-06-15") == 1.0

    def test_year_proximity_far_apart(self) -> None:
        score = year_proximity("2000-01-01", "2024-01-01")
        assert score < 0.3


# ── Candidate Scoring ─────────────────────────────────────────────────

class TestCandidateScoring:
    @pytest.fixture
    def profile(self) -> dict:
        return build_taste_profile(SONGS, HISTORY)

    def test_score_familiar_song(self, profile: dict) -> None:
        candidate = {
            "catalog_id": "new1",
            "name": "New Drill",
            "artist_name": "Rondo",
            "genre_names": ["Hip-Hop/Rap", "Drill"],
            "release_date": "2024-08-01",
        }
        result = score_candidate(candidate, profile)
        assert result["_score"] > 0.3
        assert "genre_match" in result["_breakdown"]

    def test_score_unfamiliar_song(self, profile: dict) -> None:
        candidate = {
            "catalog_id": "new2",
            "name": "Classical Symphony",
            "artist_name": "Mozart",
            "genre_names": ["Classical"],
            "release_date": "1790-01-01",
        }
        result = score_candidate(candidate, profile)
        assert result["_score"] < 0.3

    def test_rank_candidates_returns_sorted(self, profile: dict) -> None:
        candidates = [
            {"catalog_id": "c1", "name": "Match", "artist_name": "Rondo",
             "genre_names": ["Hip-Hop/Rap", "Drill"], "release_date": "2024-01-01"},
            {"catalog_id": "c2", "name": "Mismatch", "artist_name": "Mozart",
             "genre_names": ["Classical"], "release_date": "1790-01-01"},
            {"catalog_id": "c3", "name": "Partial", "artist_name": "New Guy",
             "genre_names": ["Pop", "Dance"], "release_date": "2024-05-01"},
        ]
        ranked = rank_candidates(candidates, profile, count=3)
        assert len(ranked) == 3
        # First should score highest
        assert ranked[0]["_score"] >= ranked[1]["_score"]
        assert ranked[1]["_score"] >= ranked[2]["_score"]

    def test_rank_empty(self, profile: dict) -> None:
        assert rank_candidates([], profile) == []

    def test_diversity_penalty(self, profile: dict) -> None:
        # Two very similar candidates should result in diversity penalty for the second
        candidates = [
            {"catalog_id": "c1", "name": "Drill 1", "artist_name": "Rondo",
             "genre_names": ["Drill", "Hip-Hop/Rap"], "release_date": "2024-01-01"},
            {"catalog_id": "c2", "name": "Drill 2", "artist_name": "Rondo",
             "genre_names": ["Drill", "Hip-Hop/Rap"], "release_date": "2024-02-01"},
        ]
        ranked = rank_candidates(candidates, profile, count=2)
        # Second should have diversity penalty
        assert ranked[1]["_breakdown"]["diversity_penalty"] > 0
