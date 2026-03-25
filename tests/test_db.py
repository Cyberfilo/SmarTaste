"""Tests for the SQLite persistence layer — schema, manager, and queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from musicmind.db.manager import DatabaseManager
from musicmind.db.queries import QueryExecutor


@pytest.fixture
async def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    manager = DatabaseManager(db_path)
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
async def queries(db: DatabaseManager):
    return QueryExecutor(db.engine)


class TestDatabaseManager:
    async def test_initialize_creates_db(self, tmp_path) -> None:
        db_path = tmp_path / "new.db"
        manager = DatabaseManager(db_path)
        await manager.initialize()

        assert db_path.exists()
        await manager.close()

    async def test_initialize_creates_tables(self, db: DatabaseManager) -> None:
        from sqlalchemy import inspect

        async with db.engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert "listening_history" in tables
        assert "song_metadata_cache" in tables
        assert "artist_cache" in tables
        assert "taste_profile_snapshots" in tables
        assert "generated_playlists" in tables

    async def test_engine_not_initialized_raises(self, tmp_path) -> None:
        manager = DatabaseManager(tmp_path / "x.db")
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = manager.engine


class TestListeningHistory:
    async def test_insert_and_query(self, queries: QueryExecutor) -> None:
        records = [
            {
                "song_id": "1234",
                "song_name": "Test Song",
                "artist_name": "Test Artist",
                "album_name": "Test Album",
                "genre_names": ["Pop", "Dance"],
                "duration_ms": 200000,
                "observed_at": datetime.now(tz=UTC),
                "position_in_recent": 1,
                "source": "recently_played",
            }
        ]
        count = await queries.insert_listening_history(records)
        assert count == 1

        history = await queries.get_listening_history()
        assert len(history) == 1
        assert history[0]["song_id"] == "1234"
        assert history[0]["genre_names"] == ["Pop", "Dance"]

    async def test_query_with_since_filter(self, queries: QueryExecutor) -> None:
        now = datetime.now(tz=UTC)
        old = now - timedelta(days=7)

        await queries.insert_listening_history([
            {
                "song_id": "old",
                "song_name": "Old",
                "artist_name": "A",
                "observed_at": old,
                "source": "recently_played",
            },
            {
                "song_id": "new",
                "song_name": "New",
                "artist_name": "A",
                "observed_at": now,
                "source": "recently_played",
            },
        ])

        recent = await queries.get_listening_history(since=now - timedelta(hours=1))
        assert len(recent) == 1
        assert recent[0]["song_id"] == "new"

    async def test_insert_empty_list(self, queries: QueryExecutor) -> None:
        count = await queries.insert_listening_history([])
        assert count == 0


class TestSongMetadataCache:
    async def test_upsert_new_song(self, queries: QueryExecutor) -> None:
        songs = [{
            "catalog_id": "1234",
            "name": "Test Song",
            "artist_name": "Test Artist",
            "album_name": "Test Album",
            "genre_names": ["Pop"],
            "duration_ms": 200000,
            "release_date": "2024-01-15",
        }]
        count = await queries.upsert_song_metadata(songs)
        assert count == 1

        cached = await queries.get_cached_song("1234")
        assert cached is not None
        assert cached["name"] == "Test Song"

    async def test_upsert_updates_existing(self, queries: QueryExecutor) -> None:
        song = {
            "catalog_id": "1234",
            "name": "V1",
            "artist_name": "A",
        }
        await queries.upsert_song_metadata([song])

        song["name"] = "V2"
        await queries.upsert_song_metadata([song])

        cached = await queries.get_cached_song("1234")
        assert cached["name"] == "V2"

    async def test_get_all_cached_songs(self, queries: QueryExecutor) -> None:
        await queries.upsert_song_metadata([
            {"catalog_id": "a", "name": "Alpha", "artist_name": "X"},
            {"catalog_id": "b", "name": "Beta", "artist_name": "Y"},
        ])
        songs = await queries.get_all_cached_songs()
        assert len(songs) == 2

    async def test_get_nonexistent_song(self, queries: QueryExecutor) -> None:
        result = await queries.get_cached_song("nonexistent")
        assert result is None


class TestArtistCache:
    async def test_upsert_and_query(self, queries: QueryExecutor) -> None:
        await queries.upsert_artist([{
            "artist_id": "9012",
            "name": "Test Artist",
            "genre_names": ["Pop"],
            "top_song_ids": ["1", "2"],
            "similar_artist_ids": ["3"],
        }])

        artists = await queries.get_all_cached_artists()
        assert len(artists) == 1
        assert artists[0]["name"] == "Test Artist"
        assert artists[0]["top_song_ids"] == ["1", "2"]


class TestTasteSnapshots:
    async def test_save_and_get_latest(self, queries: QueryExecutor) -> None:
        snap_id = await queries.save_taste_snapshot({
            "genre_vector": {"Pop": 0.5, "Hip-Hop": 0.3},
            "top_artists": [{"name": "Artist1", "score": 0.9}],
            "audio_trait_preferences": {"lossless": 0.8},
            "release_year_distribution": {"2024": 0.6},
            "familiarity_score": 0.3,
            "total_songs_analyzed": 100,
            "listening_hours_estimated": 50.5,
        })
        assert snap_id >= 1

        latest = await queries.get_latest_taste_snapshot()
        assert latest is not None
        assert latest["genre_vector"] == {"Pop": 0.5, "Hip-Hop": 0.3}
        assert latest["familiarity_score"] == 0.3

    async def test_no_snapshot_returns_none(self, queries: QueryExecutor) -> None:
        assert await queries.get_latest_taste_snapshot() is None


class TestGeneratedPlaylists:
    async def test_save_and_list(self, queries: QueryExecutor) -> None:
        pl_id = await queries.save_generated_playlist({
            "apple_playlist_id": "pl.abc",
            "name": "Test Playlist",
            "description": "A test",
            "vibe_prompt": "chill vibes",
            "track_ids": ["1", "2", "3"],
        })
        assert pl_id >= 1

        playlists = await queries.get_generated_playlists()
        assert len(playlists) == 1
        assert playlists[0]["name"] == "Test Playlist"
        assert playlists[0]["track_ids"] == ["1", "2", "3"]


class TestCacheStats:
    async def test_empty_stats(self, queries: QueryExecutor) -> None:
        stats = await queries.get_cache_stats()
        assert stats["songs_cached"] == 0
        assert stats["artists_cached"] == 0
        assert stats["listening_history_entries"] == 0

    async def test_stats_after_inserts(self, queries: QueryExecutor) -> None:
        await queries.upsert_song_metadata([
            {"catalog_id": "1", "name": "S1", "artist_name": "A"},
            {"catalog_id": "2", "name": "S2", "artist_name": "B"},
        ])
        await queries.insert_listening_history([{
            "song_id": "1",
            "song_name": "S1",
            "artist_name": "A",
            "observed_at": datetime.now(tz=UTC),
            "source": "recently_played",
        }])

        stats = await queries.get_cache_stats()
        assert stats["songs_cached"] == 2
        assert stats["listening_history_entries"] == 1
