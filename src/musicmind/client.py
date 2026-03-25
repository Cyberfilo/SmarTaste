"""Async Apple Music API client.

Covers library, catalog, history, and write endpoints with proper auth,
pagination, and rate limit handling.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import httpx

from musicmind.auth import AuthManager
from musicmind.models import (
    ChartResponse,
    PaginatedResponse,
    Resource,
    SearchResults,
)

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))

BASE_URL = "https://api.music.apple.com"
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # seconds


class AppleMusicClient:
    """Async client for the Apple Music API."""

    def __init__(self, auth: AuthManager, storefront: str = "it") -> None:
        self._auth = auth
        self._storefront = storefront
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> AppleMusicClient:
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(30.0),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Client not initialized. Use `async with` context manager.")
        return self._http

    # ── Internal request helpers ──────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request with retry on 429."""
        headers = self._auth.auth_headers()

        for attempt in range(MAX_RETRIES):
            resp = await self.http.request(
                method, path, headers=headers, params=params, json=json_body
            )

            if resp.status_code == 429:
                wait = BACKOFF_BASE * (2**attempt)
                logger.warning("Rate limited (429), retrying in %.1fs...", wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code == 204:
                return {}

            resp.raise_for_status()
            return resp.json()

        raise httpx.HTTPStatusError(
            "Max retries exceeded (429)", request=resp.request, response=resp
        )

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        """GET request with query params."""
        # Filter out None values
        clean_params = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", path, params=clean_params)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json_body=body)

    async def _put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", path, json_body=body)

    async def _delete(self, path: str) -> dict[str, Any]:
        return await self._request("DELETE", path)

    def _parse_paginated(self, raw: dict[str, Any]) -> PaginatedResponse:
        """Parse a standard paginated response."""
        return PaginatedResponse(
            data=[Resource(**item) for item in raw.get("data", [])],
            next=raw.get("next"),
        )

    def _parse_resource(self, raw: dict[str, Any]) -> Resource:
        """Parse a single resource from a data array response."""
        items = raw.get("data", [])
        if not items:
            raise ValueError("No resource found in response")
        return Resource(**items[0])

    # ── Library endpoints (require Music-User-Token) ──────────────────

    async def get_library_songs(
        self, limit: int = 25, offset: int = 0, include: str | None = "catalog"
    ) -> PaginatedResponse:
        """Get songs from the user's library."""
        raw = await self._get(
            "/v1/me/library/songs", limit=limit, offset=offset, include=include
        )
        return self._parse_paginated(raw)

    async def get_library_albums(
        self, limit: int = 25, offset: int = 0, include: str | None = "catalog"
    ) -> PaginatedResponse:
        raw = await self._get(
            "/v1/me/library/albums", limit=limit, offset=offset, include=include
        )
        return self._parse_paginated(raw)

    async def get_library_artists(
        self, limit: int = 25, offset: int = 0, include: str | None = "catalog"
    ) -> PaginatedResponse:
        raw = await self._get(
            "/v1/me/library/artists", limit=limit, offset=offset, include=include
        )
        return self._parse_paginated(raw)

    async def get_library_playlists(
        self, limit: int = 25, offset: int = 0
    ) -> PaginatedResponse:
        raw = await self._get("/v1/me/library/playlists", limit=limit, offset=offset)
        return self._parse_paginated(raw)

    async def get_playlist_tracks(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> PaginatedResponse:
        """Get tracks in a specific library playlist."""
        raw = await self._get(
            f"/v1/me/library/playlists/{playlist_id}/tracks",
            limit=limit,
            offset=offset,
            include="catalog",
        )
        return self._parse_paginated(raw)

    async def search_library(
        self,
        query: str,
        types: str = "library-songs,library-albums,library-artists",
        limit: int = 25,
        offset: int = 0,
    ) -> SearchResults:
        """Search within the user's library."""
        raw = await self._get(
            "/v1/me/library/search", term=query, types=types, limit=limit, offset=offset
        )
        results = raw.get("results", {})
        return SearchResults(
            songs=self._parse_paginated(results.get("library-songs", {})),
            albums=self._parse_paginated(results.get("library-albums", {})),
            artists=self._parse_paginated(results.get("library-artists", {})),
        )

    # ── Catalog endpoints ─────────────────────────────────────────────

    async def search_catalog(
        self,
        query: str,
        types: str = "songs,albums,artists,playlists",
        limit: int = 25,
        offset: int = 0,
    ) -> SearchResults:
        """Search the Apple Music catalog."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/search",
            term=query,
            types=types,
            limit=limit,
            offset=offset,
        )
        results = raw.get("results", {})
        return SearchResults(
            songs=self._parse_paginated(results.get("songs", {})),
            albums=self._parse_paginated(results.get("albums", {})),
            artists=self._parse_paginated(results.get("artists", {})),
            playlists=self._parse_paginated(results.get("playlists", {})),
        )

    async def get_song(self, song_id: str, include: str | None = None) -> Resource:
        """Get a catalog song by ID with optional relationships."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/songs/{song_id}", include=include
        )
        return self._parse_resource(raw)

    async def get_album(
        self, album_id: str, include: str | None = None
    ) -> Resource:
        """Get a catalog album by ID, optionally including tracks."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/albums/{album_id}", include=include
        )
        return self._parse_resource(raw)

    async def get_artist(
        self,
        artist_id: str,
        include: str | None = None,
        views: str | None = None,
    ) -> Resource:
        """Get a catalog artist by ID with optional views (top-songs, similar-artists, etc.)."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/artists/{artist_id}",
            include=include,
            views=views,
        )
        return self._parse_resource(raw)

    async def get_artist_top_songs(self, artist_id: str, limit: int = 20) -> PaginatedResponse:
        """Get an artist's top songs."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/artists/{artist_id}/view/top-songs",
            limit=limit,
        )
        return self._parse_paginated(raw)

    async def get_similar_artists(self, artist_id: str, limit: int = 15) -> PaginatedResponse:
        """Get artists similar to a given artist."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/artists/{artist_id}/view/similar-artists",
            limit=limit,
        )
        return self._parse_paginated(raw)

    async def get_genre_list(self) -> PaginatedResponse:
        """Get all genres for the storefront."""
        raw = await self._get(f"/v1/catalog/{self._storefront}/genres")
        return self._parse_paginated(raw)

    async def get_charts(
        self,
        types: str = "songs,albums,playlists",
        genre: str | None = None,
        limit: int = 25,
    ) -> ChartResponse:
        """Get charts, optionally filtered by genre."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/charts",
            types=types,
            genre=genre,
            limit=limit,
        )
        results = raw.get("results", {})
        return ChartResponse(
            songs=results.get("songs", []),
            albums=results.get("albums", []),
            playlists=results.get("playlists", []),
        )

    async def get_activities(self, limit: int = 25) -> PaginatedResponse:
        """Get mood/activity categories."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/activities", limit=limit
        )
        return self._parse_paginated(raw)

    async def get_song_by_ids(self, song_ids: list[str]) -> list[Resource]:
        """Get multiple catalog songs by IDs (max 300 per request)."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/songs",
            ids=",".join(song_ids),
        )
        return [Resource(**item) for item in raw.get("data", [])]

    async def get_artist_by_ids(self, artist_ids: list[str]) -> list[Resource]:
        """Get multiple catalog artists by IDs."""
        raw = await self._get(
            f"/v1/catalog/{self._storefront}/artists",
            ids=",".join(artist_ids),
        )
        return [Resource(**item) for item in raw.get("data", [])]

    # ── History endpoints (require Music-User-Token) ──────────────────

    async def get_recently_played(self, limit: int = 10) -> PaginatedResponse:
        """Get recently played resources (albums, playlists, stations)."""
        raw = await self._get("/v1/me/recent/played", limit=limit)
        return self._parse_paginated(raw)

    async def get_recently_played_tracks(
        self, limit: int = 10, offset: int = 0
    ) -> PaginatedResponse:
        """Get recently played tracks (song-level, max 50 total with pagination)."""
        raw = await self._get(
            "/v1/me/recent/played/tracks", limit=limit, offset=offset
        )
        return self._parse_paginated(raw)

    async def get_heavy_rotation(self, limit: int = 10) -> PaginatedResponse:
        """Get heavy rotation content."""
        raw = await self._get("/v1/me/history/heavy-rotation", limit=limit)
        return self._parse_paginated(raw)

    async def get_recommendations(self, limit: int = 10) -> PaginatedResponse:
        """Get Apple's personalized recommendations."""
        raw = await self._get("/v1/me/recommendations", limit=limit)
        return self._parse_paginated(raw)

    # ── Write endpoints (require Music-User-Token) ────────────────────

    async def create_playlist(
        self,
        name: str,
        description: str = "",
        track_ids: list[str] | None = None,
    ) -> Resource:
        """Create a new library playlist, optionally with initial tracks."""
        body: dict[str, Any] = {
            "attributes": {
                "name": name,
                "description": description,
            }
        }
        if track_ids:
            body["relationships"] = {
                "tracks": {
                    "data": [{"id": tid, "type": "songs"} for tid in track_ids]
                }
            }
        raw = await self._post("/v1/me/library/playlists", body)
        items = raw.get("data", [])
        if items:
            return Resource(**items[0])
        # Apple returns 201 with data in some cases, empty in others
        # Return a minimal resource with the response
        return Resource(id="", type="library-playlists", attributes={"name": name})

    async def add_tracks_to_playlist(
        self, playlist_id: str, track_ids: list[str]
    ) -> None:
        """Add tracks to an existing library playlist."""
        body = {"data": [{"id": tid, "type": "songs"} for tid in track_ids]}
        await self._post(f"/v1/me/library/playlists/{playlist_id}/tracks", body)

    async def add_to_library(self, song_ids: list[str]) -> None:
        """Add catalog songs to the user's library."""
        await self._post(
            "/v1/me/library",
            {"data": [{"id": sid, "type": "songs"} for sid in song_ids]},
        )

    async def rate_song(self, song_id: str, value: int) -> None:
        """Rate a song. value: 1 = love, -1 = dislike."""
        body = {
            "type": "rating",
            "attributes": {"value": value},
        }
        await self._put(f"/v1/me/ratings/songs/{song_id}", body)

    async def delete_rating(self, song_id: str) -> None:
        """Remove a song rating (set to neutral)."""
        await self._delete(f"/v1/me/ratings/songs/{song_id}")

    async def get_song_rating(self, song_id: str) -> int | None:
        """Get the user's rating for a song. Returns 1, -1, or None."""
        try:
            raw = await self._get(f"/v1/me/ratings/songs/{song_id}")
            items = raw.get("data", [])
            if items:
                return items[0].get("attributes", {}).get("value")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return None

    # ── Utility endpoints ─────────────────────────────────────────────

    async def get_storefront(self) -> Resource:
        """Auto-detect the user's storefront."""
        raw = await self._get("/v1/me/storefront")
        return self._parse_resource(raw)
