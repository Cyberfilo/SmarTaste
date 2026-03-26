"""Tests for temporal decay in profile building."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from musicmind.engine.profile import (
    build_artist_affinity,
    build_genre_vector,
    temporal_decay_weight,
)


class TestTemporalDecayWeight:
    def test_now_returns_one(self) -> None:
        now = datetime.now(tz=UTC)
        assert temporal_decay_weight(now, now) == 1.0

    def test_half_life(self) -> None:
        now = datetime.now(tz=UTC)
        half_life = 90.0
        past = now - timedelta(days=half_life)
        weight = temporal_decay_weight(past, now, half_life_days=half_life)
        assert abs(weight - 0.5) < 0.01

    def test_double_half_life(self) -> None:
        now = datetime.now(tz=UTC)
        half_life = 90.0
        past = now - timedelta(days=half_life * 2)
        weight = temporal_decay_weight(past, now, half_life_days=half_life)
        assert abs(weight - 0.25) < 0.01

    def test_none_returns_default(self) -> None:
        now = datetime.now(tz=UTC)
        assert temporal_decay_weight(None, now) == 0.5

    def test_string_timestamp(self) -> None:
        now = datetime.now(tz=UTC)
        past_str = (now - timedelta(days=1)).isoformat()
        weight = temporal_decay_weight(past_str, now)
        assert 0.9 < weight < 1.0


class TestTemporalGenreVector:
    def test_decay_weights_recent_higher(self) -> None:
        now = datetime.now(tz=UTC)
        songs = [
            {
                "genre_names": ["Pop"],
                "fetched_at": now,
            },
            {
                "genre_names": ["Rock"],
                "fetched_at": now - timedelta(days=365),
            },
        ]
        vector = build_genre_vector(songs, [], use_temporal_decay=True)
        # Pop (recent) should have higher weight than Rock (old)
        assert vector.get("Pop", 0) > vector.get("Rock", 0)


class TestTemporalArtistAffinity:
    def test_decay_ranks_recent_higher(self) -> None:
        now = datetime.now(tz=UTC)
        songs = [
            {"artist_name": "Recent", "fetched_at": now},
            {"artist_name": "Old", "fetched_at": now - timedelta(days=365)},
        ]
        artists = build_artist_affinity(songs, [], use_temporal_decay=True)
        names = [a["name"] for a in artists]
        assert names[0] == "Recent"
