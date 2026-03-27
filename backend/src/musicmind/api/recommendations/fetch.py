"""Discovery fetch functions for Spotify and Apple Music recommendation pipeline.

Implements 4 discovery strategies (similar_artist_crawl, genre_adjacent,
editorial_mining, chart_filter) against both services. Each function
returns song_metadata_cache-compatible dicts for scoring.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
APPLE_MUSIC_API_BASE = "https://api.music.apple.com/v1"


# ── Shared Helpers ──────────────────────────────────────────────────────────


def _spotify_track_to_cache_dict(track: dict[str, Any]) -> dict[str, Any]:
    """Map a Spotify track object to a song_metadata_cache-compatible dict.

    Genre names start empty -- populated separately via artist data
    (Spotify tracks do not carry genres).
    """
    artists = track.get("artists", [])
    artist_name = artists[0].get("name", "") if artists else ""
    album = track.get("album", {})
    images = album.get("images", [])
    artwork_url = images[0].get("url", "") if images else ""
    ext_ids = track.get("external_ids", {})
    return {
        "catalog_id": track.get("id", ""),
        "library_id": None,
        "name": track.get("name", ""),
        "artist_name": artist_name,
        "album_name": album.get("name", ""),
        "genre_names": [],  # populated separately from artist genres
        "duration_ms": track.get("duration_ms"),
        "release_date": album.get("release_date"),
        "isrc": ext_ids.get("isrc"),
        "editorial_notes": "",
        "audio_traits": [],
        "has_lyrics": False,
        "content_rating": None,
        "artwork_bg_color": "",
        "artwork_url_template": artwork_url,
        "preview_url": track.get("preview_url") or "",
        "user_rating": None,
        "date_added_to_library": None,
        "service_source": "spotify",
    }


def _apple_track_to_cache_dict(resource: dict[str, Any]) -> dict[str, Any]:
    """Map an Apple Music song resource to a song_metadata_cache-compatible dict."""
    attrs = resource.get("attributes", {})
    play_params = attrs.get("playParams", {})
    artwork = attrs.get("artwork", {})
    artwork_url = artwork.get("url", "").replace("{w}", "300").replace("{h}", "300")
    return {
        "catalog_id": play_params.get("catalogId") or resource.get("id", ""),
        "library_id": None,
        "name": attrs.get("name", ""),
        "artist_name": attrs.get("artistName", ""),
        "album_name": attrs.get("albumName", ""),
        "genre_names": attrs.get("genreNames", []),
        "duration_ms": attrs.get("durationInMillis"),
        "release_date": attrs.get("releaseDate"),
        "isrc": attrs.get("isrc"),
        "editorial_notes": "",
        "audio_traits": attrs.get("audioTraits", []),
        "has_lyrics": attrs.get("hasLyrics", False),
        "content_rating": attrs.get("contentRating"),
        "artwork_bg_color": artwork.get("bgColor", ""),
        "artwork_url_template": artwork_url,
        "preview_url": "",
        "user_rating": None,
        "date_added_to_library": None,
        "service_source": "apple_music",
    }


async def _search_artist_id(
    service: str,
    access_token: str,
    artist_name: str,
    *,
    developer_token: str | None = None,
    storefront: str = "us",
) -> str | None:
    """Resolve an artist name to a service-specific artist ID via search.

    Returns None if no matching artist is found or on error.
    """
    try:
        async with httpx.AsyncClient() as client:
            if service == "spotify":
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/search",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"q": artist_name, "type": "artist", "limit": 1},
                )
                resp.raise_for_status()
                items = resp.json().get("artists", {}).get("items", [])
                return items[0].get("id") if items else None

            elif service == "apple_music":
                headers = {"Authorization": f"Bearer {developer_token or access_token}"}
                resp = await client.get(
                    f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/search",
                    headers=headers,
                    params={"term": artist_name, "types": "artists", "limit": 1},
                )
                resp.raise_for_status()
                results = (
                    resp.json()
                    .get("results", {})
                    .get("artists", {})
                    .get("data", [])
                )
                return results[0].get("id") if results else None

    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.warning("Failed to search artist '%s' on %s", artist_name, service)
    return None


# ── Discovery Strategies ────────────────────────────────────────────────────


async def discover_similar_artists(
    service: str,
    access_token: str,
    seed_artist_names: list[str],
    *,
    developer_token: str | None = None,
    storefront: str = "us",
    depth: int = 1,
    songs_per_artist: int = 5,
) -> list[dict[str, Any]]:
    """Crawl similar artists and collect their top songs.

    First resolves artist names to service-specific IDs via search,
    then fetches related artists and their top tracks.

    Args:
        service: "spotify" or "apple_music".
        access_token: Valid access token for the service.
        seed_artist_names: Artist names from the taste profile.
        developer_token: Required for Apple Music (ES256 JWT).
        storefront: Apple Music storefront (default "us").
        depth: Hops of similar artists (default 1).
        songs_per_artist: Top songs to collect per discovered artist.

    Returns:
        List of song_metadata_cache-compatible dicts.
    """
    candidates: list[dict[str, Any]] = []

    # Resolve seed names to IDs
    seed_ids: list[str] = []
    for name in seed_artist_names[:5]:
        aid = await _search_artist_id(
            service, access_token, name,
            developer_token=developer_token, storefront=storefront,
        )
        if aid:
            seed_ids.append(aid)

    if not seed_ids:
        return candidates

    visited: set[str] = set(seed_ids)
    current_layer = list(seed_ids)

    try:
        async with httpx.AsyncClient() as client:
            for _ in range(depth):
                next_layer: list[str] = []
                for artist_id in current_layer[:10]:
                    try:
                        related_ids = await _fetch_related_artists(
                            client, service, access_token, artist_id,
                            developer_token=developer_token,
                            storefront=storefront,
                            limit=5,
                        )
                        for rid in related_ids:
                            if rid in visited:
                                continue
                            visited.add(rid)
                            next_layer.append(rid)

                            tracks = await _fetch_artist_top_tracks(
                                client, service, access_token, rid,
                                developer_token=developer_token,
                                storefront=storefront,
                                limit=songs_per_artist,
                            )
                            candidates.extend(tracks)
                    except (httpx.HTTPStatusError, httpx.HTTPError):
                        logger.warning(
                            "Error crawling artist %s on %s", artist_id, service
                        )
                        continue
                current_layer = next_layer

    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception("Connection error during similar artist crawl on %s", service)

    logger.info(
        "Discovered %d tracks via similar_artists on %s", len(candidates), service
    )
    return candidates


async def discover_genre_adjacent(
    service: str,
    access_token: str,
    top_genres: list[str],
    *,
    developer_token: str | None = None,
    storefront: str = "us",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search for tracks in genres adjacent to the user's top genres.

    Args:
        service: "spotify" or "apple_music".
        access_token: Valid access token for the service.
        top_genres: User's top genre names from taste profile.
        developer_token: Required for Apple Music (ES256 JWT).
        storefront: Apple Music storefront (default "us").
        limit: Max tracks per genre search.

    Returns:
        List of song_metadata_cache-compatible dicts.
    """
    candidates: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient() as client:
            for genre in top_genres[:3]:
                try:
                    if service == "spotify":
                        resp = await client.get(
                            f"{SPOTIFY_API_BASE}/search",
                            headers={"Authorization": f"Bearer {access_token}"},
                            params={
                                "q": f"genre:{genre}",
                                "type": "track",
                                "limit": limit,
                            },
                        )
                        resp.raise_for_status()
                        items = resp.json().get("tracks", {}).get("items", [])
                        candidates.extend(
                            _spotify_track_to_cache_dict(t) for t in items
                        )

                    elif service == "apple_music":
                        headers = {
                            "Authorization": f"Bearer {developer_token or access_token}"
                        }
                        resp = await client.get(
                            f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/search",
                            headers=headers,
                            params={
                                "term": genre,
                                "types": "songs",
                                "limit": limit,
                            },
                        )
                        resp.raise_for_status()
                        items = (
                            resp.json()
                            .get("results", {})
                            .get("songs", {})
                            .get("data", [])
                        )
                        candidates.extend(
                            _apple_track_to_cache_dict(r) for r in items
                        )

                except (httpx.HTTPStatusError, httpx.HTTPError):
                    logger.warning(
                        "Error searching genre '%s' on %s", genre, service
                    )
                    continue

    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception(
            "Connection error during genre adjacent discovery on %s", service
        )

    logger.info(
        "Discovered %d tracks via genre_adjacent on %s", len(candidates), service
    )
    return candidates


async def discover_editorial(
    service: str,
    access_token: str,
    top_genres: list[str],
    *,
    developer_token: str | None = None,
    storefront: str = "us",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search for editorial/best-of tracks in user's top genres.

    Args:
        service: "spotify" or "apple_music".
        access_token: Valid access token for the service.
        top_genres: User's top genre names from taste profile.
        developer_token: Required for Apple Music (ES256 JWT).
        storefront: Apple Music storefront (default "us").
        limit: Max tracks per genre search.

    Returns:
        List of song_metadata_cache-compatible dicts.
    """
    candidates: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient() as client:
            for genre in top_genres[:3]:
                try:
                    query = f"best new {genre}"

                    if service == "spotify":
                        resp = await client.get(
                            f"{SPOTIFY_API_BASE}/search",
                            headers={"Authorization": f"Bearer {access_token}"},
                            params={
                                "q": query,
                                "type": "track",
                                "limit": limit,
                            },
                        )
                        resp.raise_for_status()
                        items = resp.json().get("tracks", {}).get("items", [])
                        candidates.extend(
                            _spotify_track_to_cache_dict(t) for t in items
                        )

                    elif service == "apple_music":
                        headers = {
                            "Authorization": f"Bearer {developer_token or access_token}"
                        }
                        resp = await client.get(
                            f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/search",
                            headers=headers,
                            params={
                                "term": query,
                                "types": "songs",
                                "limit": limit,
                            },
                        )
                        resp.raise_for_status()
                        items = (
                            resp.json()
                            .get("results", {})
                            .get("songs", {})
                            .get("data", [])
                        )
                        candidates.extend(
                            _apple_track_to_cache_dict(r) for r in items
                        )

                except (httpx.HTTPStatusError, httpx.HTTPError):
                    logger.warning(
                        "Error in editorial search for '%s' on %s", genre, service
                    )
                    continue

    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception(
            "Connection error during editorial discovery on %s", service
        )

    logger.info(
        "Discovered %d tracks via editorial on %s", len(candidates), service
    )
    return candidates


async def discover_chart_filter(
    service: str,
    access_token: str,
    profile_genres: list[str],
    *,
    developer_token: str | None = None,
    storefront: str = "us",
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Fetch chart/new-release songs and filter by genre overlap with user profile.

    Args:
        service: "spotify" or "apple_music".
        access_token: Valid access token for the service.
        profile_genres: User's top 5 genre names for overlap filtering.
        developer_token: Required for Apple Music (ES256 JWT).
        storefront: Apple Music storefront (default "us").
        limit: Max items to fetch from charts.

    Returns:
        List of song_metadata_cache-compatible dicts filtered by genre overlap.
    """
    candidates: list[dict[str, Any]] = []
    top5 = set(g.lower() for g in profile_genres[:5])

    try:
        async with httpx.AsyncClient() as client:
            if service == "spotify":
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/browse/new-releases",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"limit": min(limit, 50)},
                )
                resp.raise_for_status()
                albums = resp.json().get("albums", {}).get("items", [])

                # Fetch tracks from each new release album
                for album in albums:
                    album_id = album.get("id", "")
                    if not album_id:
                        continue
                    try:
                        resp2 = await client.get(
                            f"{SPOTIFY_API_BASE}/albums/{album_id}/tracks",
                            headers={"Authorization": f"Bearer {access_token}"},
                            params={"limit": 5},
                        )
                        resp2.raise_for_status()
                        tracks = resp2.json().get("items", [])
                        for track in tracks:
                            # Augment track with album info for cache dict
                            track["album"] = album
                            candidates.append(
                                _spotify_track_to_cache_dict(track)
                            )
                    except (httpx.HTTPStatusError, httpx.HTTPError):
                        continue

            elif service == "apple_music":
                headers = {
                    "Authorization": f"Bearer {developer_token or access_token}"
                }
                resp = await client.get(
                    f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/charts",
                    headers=headers,
                    params={"types": "songs", "limit": limit},
                )
                resp.raise_for_status()
                chart_data = resp.json().get("results", {}).get("songs", [])
                for chart in chart_data:
                    for item in chart.get("data", []):
                        track = _apple_track_to_cache_dict(item)
                        # Filter: keep only tracks with genre overlap
                        track_genres = set(
                            g.lower() for g in track.get("genre_names", [])
                        )
                        if track_genres & top5:
                            candidates.append(track)

    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception(
            "Connection error during chart filter on %s", service
        )

    logger.info(
        "Discovered %d tracks via chart_filter on %s", len(candidates), service
    )
    return candidates


# ── Internal Helpers ────────────────────────────────────────────────────────


async def _fetch_related_artists(
    client: httpx.AsyncClient,
    service: str,
    access_token: str,
    artist_id: str,
    *,
    developer_token: str | None = None,
    storefront: str = "us",
    limit: int = 5,
) -> list[str]:
    """Fetch IDs of artists related to the given artist."""
    if service == "spotify":
        resp = await client.get(
            f"{SPOTIFY_API_BASE}/artists/{artist_id}/related-artists",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        artists = resp.json().get("artists", [])
        return [a.get("id", "") for a in artists[:limit] if a.get("id")]

    elif service == "apple_music":
        headers = {"Authorization": f"Bearer {developer_token or access_token}"}
        resp = await client.get(
            f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/artists/{artist_id}",
            headers=headers,
            params={"views": "similar-artists"},
        )
        resp.raise_for_status()
        views = resp.json().get("data", [{}])[0].get("views", {})
        similar = views.get("similar-artists", {}).get("data", [])
        return [a.get("id", "") for a in similar[:limit] if a.get("id")]

    return []


async def _fetch_artist_top_tracks(
    client: httpx.AsyncClient,
    service: str,
    access_token: str,
    artist_id: str,
    *,
    developer_token: str | None = None,
    storefront: str = "us",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Fetch top tracks for a given artist."""
    if service == "spotify":
        resp = await client.get(
            f"{SPOTIFY_API_BASE}/artists/{artist_id}/top-tracks",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        tracks = resp.json().get("tracks", [])
        return [_spotify_track_to_cache_dict(t) for t in tracks[:limit]]

    elif service == "apple_music":
        headers = {"Authorization": f"Bearer {developer_token or access_token}"}
        resp = await client.get(
            f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/artists/{artist_id}",
            headers=headers,
            params={"views": "top-songs"},
        )
        resp.raise_for_status()
        views = resp.json().get("data", [{}])[0].get("views", {})
        songs = views.get("top-songs", {}).get("data", [])
        return [_apple_track_to_cache_dict(s) for s in songs[:limit]]

    return []
