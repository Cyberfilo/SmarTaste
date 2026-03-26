"""Tests for feedback, audio features, classification, and play-count DB operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from musicmind.db.manager import DatabaseManager
from musicmind.db.queries import QueryExecutor


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    manager = DatabaseManager(db_path)
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
async def queries(db: DatabaseManager):
    return QueryExecutor(db.engine)


class TestRecommendationFeedback:
    async def test_insert_and_query(self, queries: QueryExecutor) -> None:
        fid = await queries.insert_feedback({
            "catalog_id": "1234",
            "feedback_type": "thumbs_up",
            "predicted_score": 0.85,
            "weight_snapshot": {"genre": 0.35, "artist": 0.08},
        })
        assert fid >= 1

        all_fb = await queries.get_all_feedback()
        assert len(all_fb) == 1
        assert all_fb[0]["catalog_id"] == "1234"
        assert all_fb[0]["feedback_type"] == "thumbs_up"
        assert all_fb[0]["weight_snapshot"]["genre"] == 0.35

    async def test_get_feedback_since(self, queries: QueryExecutor) -> None:
        old_time = datetime.now(tz=UTC) - timedelta(days=10)
        await queries.insert_feedback({
            "catalog_id": "old",
            "feedback_type": "skipped",
            "created_at": old_time,
        })
        await queries.insert_feedback({
            "catalog_id": "new",
            "feedback_type": "thumbs_up",
        })

        since = datetime.now(tz=UTC) - timedelta(hours=1)
        recent = await queries.get_feedback_since(since)
        assert len(recent) == 1
        assert recent[0]["catalog_id"] == "new"


class TestAudioFeaturesCache:
    async def test_upsert_and_get(self, queries: QueryExecutor) -> None:
        await queries.upsert_audio_features([{
            "catalog_id": "1234",
            "tempo": 128.0,
            "energy": 0.85,
            "brightness": 0.6,
            "danceability": 0.75,
            "acousticness": 0.1,
            "valence_proxy": 0.7,
            "beat_strength": 0.8,
        }])

        features = await queries.get_audio_features("1234")
        assert features is not None
        assert features["tempo"] == 128.0
        assert features["energy"] == 0.85

    async def test_get_nonexistent(self, queries: QueryExecutor) -> None:
        assert await queries.get_audio_features("nope") is None

    async def test_bulk_get(self, queries: QueryExecutor) -> None:
        await queries.upsert_audio_features([
            {"catalog_id": "a", "tempo": 100.0, "energy": 0.5},
            {"catalog_id": "b", "tempo": 140.0, "energy": 0.9},
        ])
        bulk = await queries.get_audio_features_bulk(["a", "b", "c"])
        assert "a" in bulk
        assert "b" in bulk
        assert "c" not in bulk

    async def test_upsert_updates(self, queries: QueryExecutor) -> None:
        await queries.upsert_audio_features([
            {"catalog_id": "x", "tempo": 100.0, "energy": 0.5},
        ])
        await queries.upsert_audio_features([
            {"catalog_id": "x", "tempo": 120.0, "energy": 0.7},
        ])
        f = await queries.get_audio_features("x")
        assert f["tempo"] == 120.0


class TestSoundClassificationCache:
    async def test_upsert_and_get(self, queries: QueryExecutor) -> None:
        await queries.upsert_classification_labels([{
            "catalog_id": "1234",
            "labels": {"guitar": 0.92, "singing": 0.88},
            "analyzer_version": "1.0",
        }])

        labels = await queries.get_classification_labels("1234")
        assert labels is not None
        assert labels["labels"]["guitar"] == 0.92

    async def test_bulk_get(self, queries: QueryExecutor) -> None:
        await queries.upsert_classification_labels([
            {"catalog_id": "a", "labels": {"drums": 0.5}},
            {"catalog_id": "b", "labels": {"piano": 0.8}},
        ])
        bulk = await queries.get_classification_labels_bulk(["a", "b"])
        assert len(bulk) == 2


class TestPlayCountProxy:
    async def test_first_observation(self, queries: QueryExecutor) -> None:
        await queries.upsert_play_observation("song1")
        obs = await queries.get_play_observations()
        assert len(obs) == 1
        assert obs[0]["song_id"] == "song1"
        assert obs[0]["seen_count"] == 1

    async def test_increment_count(self, queries: QueryExecutor) -> None:
        await queries.upsert_play_observation("song1")
        await queries.upsert_play_observation("song1")
        await queries.upsert_play_observation("song1")

        obs = await queries.get_play_observations()
        assert obs[0]["seen_count"] == 3

    async def test_top_played(self, queries: QueryExecutor) -> None:
        for _ in range(5):
            await queries.upsert_play_observation("frequent")
        for _ in range(2):
            await queries.upsert_play_observation("occasional")
        await queries.upsert_play_observation("rare")

        top = await queries.get_top_played(limit=2)
        assert len(top) == 2
        assert top[0]["song_id"] == "frequent"
        assert top[0]["seen_count"] == 5

    async def test_get_recent_recommendations(self, queries: QueryExecutor) -> None:
        await queries.insert_feedback({
            "catalog_id": "recent",
            "feedback_type": "thumbs_up",
        })
        old_time = datetime.now(tz=UTC) - timedelta(days=60)
        await queries.insert_feedback({
            "catalog_id": "old",
            "feedback_type": "thumbs_up",
            "created_at": old_time,
        })

        recent = await queries.get_recent_recommendations(days=30)
        assert len(recent) == 1
        assert recent[0]["catalog_id"] == "recent"
