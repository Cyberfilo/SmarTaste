"""Tests for AppleMusicClient — mocked HTTP responses."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from musicmind.client import AppleMusicClient
from musicmind.auth import AuthManager


@pytest.fixture
def mock_auth() -> AuthManager:
    auth = MagicMock(spec=AuthManager)
    auth.auth_headers.return_value = {
        "Authorization": "Bearer test-dev-token",
        "Music-User-Token": "test-user-token",
    }
    return auth


@pytest.fixture
async def client(mock_auth: AuthManager):
    async with AppleMusicClient(mock_auth, storefront="it") as c:
        yield c


def _mock_response(
    data: list[dict] | None = None,
    results: dict | None = None,
    status: int = 200,
    next_url: str | None = None,
) -> httpx.Response:
    """Build a mock httpx.Response."""
    body: dict = {}
    if data is not None:
        body["data"] = data
        if next_url:
            body["next"] = next_url
    if results is not None:
        body["results"] = results
    return httpx.Response(status_code=status, json=body, request=httpx.Request("GET", "http://test"))


SONG_RESOURCE = {
    "id": "1234",
    "type": "songs",
    "attributes": {
        "name": "Test Song",
        "artistName": "Test Artist",
        "albumName": "Test Album",
        "genreNames": ["Pop", "Dance"],
        "durationInMillis": 200000,
        "releaseDate": "2024-01-15",
    },
}

ALBUM_RESOURCE = {
    "id": "5678",
    "type": "albums",
    "attributes": {
        "name": "Test Album",
        "artistName": "Test Artist",
        "trackCount": 12,
    },
}

ARTIST_RESOURCE = {
    "id": "9012",
    "type": "artists",
    "attributes": {
        "name": "Test Artist",
        "genreNames": ["Pop"],
    },
}

PLAYLIST_RESOURCE = {
    "id": "pl.abc123",
    "type": "library-playlists",
    "attributes": {"name": "My Playlist"},
}


class TestLibraryEndpoints:
    async def test_get_library_songs(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[SONG_RESOURCE], next_url="/next")
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_library_songs(limit=10, offset=0)

        assert len(result.data) == 1
        assert result.data[0].id == "1234"
        assert result.data[0].attributes["name"] == "Test Song"
        assert result.has_more is True

    async def test_get_library_albums(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[ALBUM_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_library_albums()
        assert len(result.data) == 1
        assert result.has_more is False

    async def test_get_library_artists(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[ARTIST_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_library_artists()
        assert result.data[0].attributes["name"] == "Test Artist"

    async def test_get_library_playlists(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[PLAYLIST_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_library_playlists()
        assert result.data[0].id == "pl.abc123"

    async def test_get_playlist_tracks(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[SONG_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_playlist_tracks("pl.abc123")
        assert len(result.data) == 1

    async def test_search_library(self, client: AppleMusicClient) -> None:
        resp = _mock_response(results={
            "library-songs": {"data": [SONG_RESOURCE]},
            "library-albums": {"data": [ALBUM_RESOURCE]},
        })
        client.http.request = AsyncMock(return_value=resp)

        result = await client.search_library("test")
        assert len(result.songs.data) == 1
        assert len(result.albums.data) == 1


class TestCatalogEndpoints:
    async def test_search_catalog(self, client: AppleMusicClient) -> None:
        resp = _mock_response(results={
            "songs": {"data": [SONG_RESOURCE]},
            "artists": {"data": [ARTIST_RESOURCE]},
        })
        client.http.request = AsyncMock(return_value=resp)

        result = await client.search_catalog("test query")
        assert len(result.songs.data) == 1
        assert len(result.artists.data) == 1

    async def test_get_song(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[SONG_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_song("1234")
        assert result.id == "1234"
        assert result.type == "songs"

    async def test_get_album(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[ALBUM_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_album("5678")
        assert result.attributes["name"] == "Test Album"

    async def test_get_artist_with_views(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[ARTIST_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_artist("9012", views="top-songs,similar-artists")
        assert result.id == "9012"

        # Verify views param was passed
        call_kwargs = client.http.request.call_args
        assert "views" in call_kwargs.kwargs.get("params", {})

    async def test_get_artist_top_songs(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[SONG_RESOURCE, SONG_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_artist_top_songs("9012")
        assert len(result.data) == 2

    async def test_get_similar_artists(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[ARTIST_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_similar_artists("9012")
        assert len(result.data) == 1

    async def test_get_genre_list(self, client: AppleMusicClient) -> None:
        genre = {"id": "14", "type": "genres", "attributes": {"name": "Pop"}}
        resp = _mock_response(data=[genre])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_genre_list()
        assert result.data[0].attributes["name"] == "Pop"

    async def test_get_charts(self, client: AppleMusicClient) -> None:
        resp = _mock_response(results={
            "songs": [{"chart": "most-played", "name": "Top Songs", "data": [SONG_RESOURCE]}],
        })
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_charts(types="songs")
        assert len(result.songs) == 1

    async def test_get_activities(self, client: AppleMusicClient) -> None:
        activity = {"id": "act1", "type": "activities", "attributes": {"name": "Chill"}}
        resp = _mock_response(data=[activity])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_activities()
        assert result.data[0].attributes["name"] == "Chill"

    async def test_get_song_by_ids(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[SONG_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_song_by_ids(["1234"])
        assert len(result) == 1
        assert result[0].id == "1234"


class TestHistoryEndpoints:
    async def test_get_recently_played(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[SONG_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_recently_played()
        assert len(result.data) == 1

    async def test_get_recently_played_tracks(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[SONG_RESOURCE], next_url="/next")
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_recently_played_tracks(limit=10, offset=0)
        assert len(result.data) == 1
        assert result.has_more is True

    async def test_get_heavy_rotation(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_heavy_rotation()
        assert len(result.data) == 0

    async def test_get_recommendations(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[PLAYLIST_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_recommendations()
        assert len(result.data) == 1


class TestWriteEndpoints:
    async def test_create_playlist(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[PLAYLIST_RESOURCE], status=201)
        client.http.request = AsyncMock(return_value=resp)

        result = await client.create_playlist("New Playlist", "desc", ["1234", "5678"])
        assert result.attributes["name"] == "My Playlist"

        # Verify body included tracks
        call_kwargs = client.http.request.call_args
        body = call_kwargs.kwargs.get("json", {})
        assert "relationships" in body
        assert len(body["relationships"]["tracks"]["data"]) == 2

    async def test_add_tracks_to_playlist(self, client: AppleMusicClient) -> None:
        resp = httpx.Response(
            status_code=204, request=httpx.Request("POST", "http://test")
        )
        client.http.request = AsyncMock(return_value=resp)

        await client.add_tracks_to_playlist("pl.abc", ["1234"])

    async def test_add_to_library(self, client: AppleMusicClient) -> None:
        resp = httpx.Response(
            status_code=204, request=httpx.Request("POST", "http://test")
        )
        client.http.request = AsyncMock(return_value=resp)

        await client.add_to_library(["1234", "5678"])

    async def test_rate_song(self, client: AppleMusicClient) -> None:
        resp = httpx.Response(
            status_code=204, request=httpx.Request("PUT", "http://test")
        )
        client.http.request = AsyncMock(return_value=resp)

        await client.rate_song("1234", 1)

    async def test_delete_rating(self, client: AppleMusicClient) -> None:
        resp = httpx.Response(
            status_code=204, request=httpx.Request("DELETE", "http://test")
        )
        client.http.request = AsyncMock(return_value=resp)

        await client.delete_rating("1234")

    async def test_get_song_rating_exists(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[{
            "id": "1234",
            "type": "ratings",
            "attributes": {"value": 1},
        }])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_song_rating("1234")
        assert result == 1

    async def test_get_song_rating_not_found(self, client: AppleMusicClient) -> None:
        resp = httpx.Response(
            status_code=404, request=httpx.Request("GET", "http://test")
        )
        client.http.request = AsyncMock(side_effect=httpx.HTTPStatusError(
            "Not Found", request=resp.request, response=resp
        ))

        result = await client.get_song_rating("1234")
        assert result is None


class TestAuthHeaders:
    async def test_auth_headers_sent(self, client: AppleMusicClient, mock_auth) -> None:
        resp = _mock_response(data=[SONG_RESOURCE])
        client.http.request = AsyncMock(return_value=resp)

        await client.get_library_songs()

        call_kwargs = client.http.request.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers["Authorization"] == "Bearer test-dev-token"
        assert headers["Music-User-Token"] == "test-user-token"


class TestUtility:
    async def test_get_storefront(self, client: AppleMusicClient) -> None:
        resp = _mock_response(data=[{
            "id": "it",
            "type": "storefronts",
            "attributes": {"name": "Italy", "defaultLanguageTag": "it-IT"},
        }])
        client.http.request = AsyncMock(return_value=resp)

        result = await client.get_storefront()
        assert result.id == "it"

    async def test_client_not_initialized_raises(self, mock_auth) -> None:
        client = AppleMusicClient(mock_auth)
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = client.http


class TestRateLimiting:
    async def test_retries_on_429(self, client: AppleMusicClient) -> None:
        rate_limited = httpx.Response(
            status_code=429, request=httpx.Request("GET", "http://test")
        )
        ok_resp = _mock_response(data=[SONG_RESOURCE])

        client.http.request = AsyncMock(side_effect=[rate_limited, ok_resp])

        with patch("musicmind.client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.get_library_songs()
            assert len(result.data) == 1
