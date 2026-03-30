"""Tests for engine typed models — Phase 3 type safety."""

from __future__ import annotations

import pytest

from musicmind.engine.models import (
    ArtistAffinity,
    AudioFeatures,
    Candidate,
    ScoreBreakdown,
    ScoredCandidate,
    ScoringWeights,
    UserProfile,
)
from musicmind.engine.scorer import rank_candidates, score_candidate


# ── Candidate ──────────────────────────────────────────────────────────────


def test_candidate_from_dict():
    d = {
        "catalog_id": "abc123",
        "name": "Test Song",
        "artist_name": "Test Artist",
        "genre_names": ["Pop", "Rock"],
        "release_date": "2024-01-15",
        "_strategy_count": 2,
        "unknown_field": "ignored",
    }
    c = Candidate.from_dict(d)
    assert c.catalog_id == "abc123"
    assert c.genre_names == ["Pop", "Rock"]
    assert c._strategy_count == 2


def test_candidate_from_dict_string_genres():
    d = {"catalog_id": "x", "genre_names": "Pop"}
    c = Candidate.from_dict(d)
    assert c.genre_names == ["Pop"]


def test_candidate_roundtrip():
    c = Candidate(catalog_id="a", name="Song", artist_name="Artist", genre_names=["Pop"])
    d = c.to_dict()
    c2 = Candidate.from_dict(d)
    assert c2.catalog_id == c.catalog_id
    assert c2.genre_names == c.genre_names


# ── ScoreBreakdown ─────────────────────────────────────────────────────────


def test_score_breakdown_roundtrip():
    bd = ScoreBreakdown(genre_match=0.75, artist_match=0.5, novelty=0.3)
    d = bd.to_dict()
    bd2 = ScoreBreakdown.from_dict(d)
    assert bd2.genre_match == 0.75
    assert bd2.novelty == 0.3


# ── ScoredCandidate ────────────────────────────────────────────────────────


def test_scored_candidate_to_dict():
    c = Candidate(catalog_id="abc", name="Song", artist_name="Artist")
    sc = ScoredCandidate(candidate=c, score=0.85, explanation="great match")
    d = sc.to_dict()
    assert d["_score"] == 0.85
    assert d["catalog_id"] == "abc"
    assert d["_explanation"] == "great match"


def test_scored_candidate_from_dict():
    d = {
        "catalog_id": "abc",
        "name": "Song",
        "artist_name": "Artist",
        "_score": 0.75,
        "_breakdown": {"genre_match": 0.8, "artist_match": 0.6},
        "_explanation": "good match",
    }
    sc = ScoredCandidate.from_dict(d)
    assert sc.score == 0.75
    assert sc.breakdown.genre_match == 0.8


# ── UserProfile ────────────────────────────────────────────────────────────


def test_user_profile_from_dict():
    d = {
        "genre_vector": {"Pop": 0.5, "Rock": 0.3},
        "top_artists": [
            {"name": "Artist A", "score": 1.0, "song_count": 10},
        ],
        "familiarity_score": 0.7,
    }
    profile = UserProfile.from_dict(d)
    assert profile.genre_vector["Pop"] == 0.5
    assert len(profile.top_artists) == 1
    assert profile.top_artists[0].name == "Artist A"
    assert profile.familiarity_score == 0.7


def test_user_profile_roundtrip():
    p = UserProfile(
        genre_vector={"Pop": 0.6},
        top_artists=[ArtistAffinity("Test", 0.9, 5)],
        familiarity_score=0.8,
    )
    d = p.to_dict()
    p2 = UserProfile.from_dict(d)
    assert p2.genre_vector == p.genre_vector
    assert p2.top_artists[0].name == "Test"


# ── ScoringWeights ─────────────────────────────────────────────────────────


def test_scoring_weights_roundtrip():
    w = ScoringWeights(genre=0.40, audio=0.15)
    d = w.to_dict()
    w2 = ScoringWeights.from_dict(d)
    assert w2.genre == 0.40
    assert w2.audio == 0.15


# ── Integration: typed models interop with existing scorer ─────────────────


def test_candidate_dict_works_with_score_candidate():
    """Candidate.to_dict() output should work with score_candidate."""
    c = Candidate(
        catalog_id="test1",
        name="Test Song",
        artist_name="Unknown Artist",
        genre_names=["Pop", "R&B/Soul"],
        release_date="2023-06-15",
    )
    profile = UserProfile(
        genre_vector={"Pop": 0.5, "R&B/Soul": 0.3, "Rock": 0.2},
        top_artists=[ArtistAffinity("Other Artist", 1.0, 10)],
        release_year_distribution={"2023": 0.5, "2022": 0.5},
        familiarity_score=0.6,
    )

    result = score_candidate(c.to_dict(), profile.to_dict())
    assert "_score" in result
    assert 0.0 <= result["_score"] <= 1.0
    assert "_breakdown" in result

    # Verify we can parse the result back
    sc = ScoredCandidate.from_dict(result)
    assert sc.score == result["_score"]


def test_rank_candidates_with_typed_models():
    """Verify typed model dicts work with rank_candidates."""
    candidates = [
        Candidate(
            catalog_id=f"cat_{i}",
            name=f"Song {i}",
            artist_name=f"Artist {i}",
            genre_names=["Pop"],
            release_date="2023-01-01",
        ).to_dict()
        for i in range(30)
    ]
    profile = UserProfile(
        genre_vector={"Pop": 0.6, "Rock": 0.4},
        top_artists=[ArtistAffinity("Artist 0", 1.0, 5)],
        release_year_distribution={"2023": 1.0},
        familiarity_score=0.5,
    ).to_dict()

    results = rank_candidates(candidates, profile, count=5)
    assert len(results) == 5
    for r in results:
        sc = ScoredCandidate.from_dict(r)
        assert sc.score > 0


# ── AudioFeatures ──────────────────────────────────────────────────────────


def test_audio_features_roundtrip():
    af = AudioFeatures(tempo=120.0, energy=0.8, danceability=0.7)
    d = af.to_dict()
    af2 = AudioFeatures.from_dict(d)
    assert af2.tempo == 120.0
    assert af2.energy == 0.8
    assert af2.brightness is None
