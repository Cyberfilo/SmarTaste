"""Data fetching functions for Spotify and Apple Music taste profile pipeline.

Fetches library/top/recently-played data from each service and returns
song_metadata_cache-compatible dicts. Genre enrichment for Spotify uses
the top_artists response (Spotify tracks do NOT include genres).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
APPLE_MUSIC_API_BASE = "https://api.music.apple.com/v1"


# ── Spotify Helpers ──────────────────────────────────────────────────────────


def _spotify_track_to_cache_dict(track: dict[str, Any]) -> dict[str, Any]:
    """Map a Spotify track object to a song_metadata_cache-compatible dict.

    Genre names start empty -- populated separately via enrich_spotify_genres()
    using artist data (Spotify tracks do not carry genres).
    """
    artists = track.get("artists", [])
    artist_name = artists[0].get("name", "") if artists else ""
    album = track.get("album", {})
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
        "artwork_url_template": "",
        "preview_url": track.get("preview_url") or "",
        "user_rating": None,
        "date_added_to_library": None,
        "service_source": "spotify",
    }


# ── Spotify Fetch Functions ──────────────────────────────────────────────────


async def fetch_spotify_top_tracks(
    access_token: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch user's top tracks (long_term) as song cache dicts.

    Paginates with offset up to 200 total tracks (4 pages of 50).
    Falls back to empty list on 403 (dev mode restriction).

    Args:
        access_token: Valid Spotify access token.
        limit: Items per page (max 50).

    Returns:
        List of song_metadata_cache-compatible dicts.
    """
    results: list[dict[str, Any]] = []
    offset = 0
    max_total = 200
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient() as client:
            while offset < max_total:
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/me/top/tracks",
                    headers=headers,
                    params={
                        "time_range": "long_term",
                        "limit": limit,
                        "offset": offset,
                    },
                )
                if resp.status_code == 403:
                    logger.warning(
                        "Spotify top tracks returned 403 (dev mode restriction), "
                        "returning %d tracks collected so far",
                        len(results),
                    )
                    break
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break
                results.extend(
                    _spotify_track_to_cache_dict(item) for item in items
                )
                offset += limit
                if data.get("next") is None:
                    break
    except httpx.HTTPStatusError:
        logger.exception("Error fetching Spotify top tracks at offset %d", offset)
    except httpx.HTTPError:
        logger.exception("Connection error fetching Spotify top tracks")

    logger.info("Fetched %d Spotify top tracks", len(results))
    return results


async def fetch_spotify_top_artists(
    access_token: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch user's top artists (long_term) including genres.

    This is the GENRE SOURCE for Spotify -- track objects do not carry genres.
    Paginates up to 100 artists (2 pages).

    Args:
        access_token: Valid Spotify access token.
        limit: Items per page (max 50).

    Returns:
        List of artist dicts with keys: id, name, genres.
    """
    results: list[dict[str, Any]] = []
    offset = 0
    max_total = 100
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient() as client:
            while offset < max_total:
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/me/top/artists",
                    headers=headers,
                    params={
                        "time_range": "long_term",
                        "limit": limit,
                        "offset": offset,
                    },
                )
                if resp.status_code == 403:
                    logger.warning(
                        "Spotify top artists returned 403 (dev mode restriction), "
                        "returning %d artists collected so far",
                        len(results),
                    )
                    break
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break
                for item in items:
                    results.append({
                        "id": item.get("id", ""),
                        "name": item.get("name", ""),
                        "genres": item.get("genres", []),
                    })
                offset += limit
                if data.get("next") is None:
                    break
    except httpx.HTTPStatusError:
        logger.exception("Error fetching Spotify top artists at offset %d", offset)
    except httpx.HTTPError:
        logger.exception("Connection error fetching Spotify top artists")

    logger.info("Fetched %d Spotify top artists", len(results))
    return results


async def fetch_spotify_saved_tracks(
    access_token: str,
    *,
    limit: int = 50,
    max_pages: int = 4,
) -> list[dict[str, Any]]:
    """Fetch user's saved (liked) tracks as song cache dicts.

    Paginates up to max_pages (default 200 tracks).

    Args:
        access_token: Valid Spotify access token.
        limit: Items per page (max 50).
        max_pages: Maximum number of pages to fetch.

    Returns:
        List of song_metadata_cache-compatible dicts with date_added_to_library.
    """
    results: list[dict[str, Any]] = []
    offset = 0
    pages_fetched = 0
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient() as client:
            while pages_fetched < max_pages:
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/me/tracks",
                    headers=headers,
                    params={"limit": limit, "offset": offset},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break
                for item in items:
                    track = item.get("track")
                    if not track:
                        continue
                    cache_dict = _spotify_track_to_cache_dict(track)
                    cache_dict["date_added_to_library"] = item.get("added_at")
                    results.append(cache_dict)
                pages_fetched += 1
                offset += limit
                if data.get("next") is None:
                    break
    except httpx.HTTPStatusError:
        logger.exception("Error fetching Spotify saved tracks at offset %d", offset)
    except httpx.HTTPError:
        logger.exception("Connection error fetching Spotify saved tracks")

    logger.info("Fetched %d Spotify saved tracks", len(results))
    return results


async def fetch_spotify_recently_played(
    access_token: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch user's recently played tracks as history-shaped dicts.

    Spotify recently played returns up to 50 tracks with cursor pagination,
    but effectively maxes at ~50 unique items.

    Args:
        access_token: Valid Spotify access token.
        limit: Items per request (max 50).

    Returns:
        List of listening_history-compatible dicts.
    """
    results: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/me/player/recently-played",
                headers=headers,
                params={"limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("items", []):
                track = item.get("track", {})
                artists = track.get("artists", [])
                artist_name = artists[0].get("name", "") if artists else ""
                album = track.get("album", {})
                results.append({
                    "song_id": track.get("id", ""),
                    "song_name": track.get("name", ""),
                    "artist_name": artist_name,
                    "album_name": album.get("name", ""),
                    "genre_names": [],  # populated separately from artist genres
                    "duration_ms": track.get("duration_ms"),
                    "observed_at": item.get("played_at"),
                    "service_source": "spotify",
                })
    except httpx.HTTPStatusError:
        logger.exception("Error fetching Spotify recently played")
    except httpx.HTTPError:
        logger.exception("Connection error fetching Spotify recently played")

    logger.info("Fetched %d Spotify recently played entries", len(results))
    return results


def enrich_spotify_genres(
    tracks: list[dict[str, Any]],
    artists: list[dict[str, Any]],
) -> None:
    """Enrich Spotify track dicts with genres from artist data.

    Spotify track objects never include genres -- genres live on Artist objects.
    This function builds an artist_name -> genres mapping from the artists list
    and sets genre_names on each track. Mutates tracks in-place.

    Args:
        tracks: List of song_metadata_cache-compatible dicts (from fetch functions).
        artists: List of artist dicts with keys: name, genres.
    """
    artist_genres: dict[str, list[str]] = {}
    for artist in artists:
        name = artist.get("name", "")
        genres = artist.get("genres", [])
        if name and genres:
            artist_genres[name] = genres

    enriched_count = 0
    for track in tracks:
        artist_name = track.get("artist_name", "")
        if artist_name and artist_name in artist_genres:
            track["genre_names"] = artist_genres[artist_name]
            enriched_count += 1

    logger.info(
        "Enriched %d of %d tracks with artist genres",
        enriched_count,
        len(tracks),
    )


# ── Apple Music Fetch Functions ──────────────────────────────────────────────


async def fetch_apple_music_library(
    developer_token: str,
    music_user_token: str,
    *,
    limit: int = 100,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """Fetch user's Apple Music library songs with catalog metadata.

    Uses ?include[library-songs]=catalog to get full catalog metadata
    (genres, ISRC, audio traits). Both developer_token and music_user_token
    are required for library endpoints.

    Args:
        developer_token: ES256-signed Apple Developer Token.
        music_user_token: Apple Music User Token from MusicKit JS.
        limit: Items per page (max 100).
        max_pages: Maximum pages to fetch (default 500 songs cap).

    Returns:
        List of song_metadata_cache-compatible dicts.
    """
    results: list[dict[str, Any]] = []
    offset = 0
    pages_fetched = 0
    headers = {
        "Authorization": f"Bearer {developer_token}",
        "Music-User-Token": music_user_token,
    }

    try:
        async with httpx.AsyncClient() as client:
            while pages_fetched < max_pages:
                resp = await client.get(
                    f"{APPLE_MUSIC_API_BASE}/me/library/songs",
                    headers=headers,
                    params={
                        "limit": limit,
                        "offset": offset,
                        "include[library-songs]": "catalog",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("data", [])
                if not items:
                    break
                for resource in items:
                    attrs = resource.get("attributes", {})
                    play_params = attrs.get("playParams", {})
                    results.append({
                        "catalog_id": (
                            play_params.get("catalogId")
                            or resource.get("id", "")
                        ),
                        "library_id": resource.get("id"),
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
                        "artwork_bg_color": "",
                        "artwork_url_template": "",
                        "preview_url": "",
                        "user_rating": None,
                        "date_added_to_library": attrs.get("dateAdded"),
                        "service_source": "apple_music",
                    })
                pages_fetched += 1
                offset += limit
                if data.get("next") is None:
                    break
    except httpx.HTTPStatusError:
        logger.exception(
            "Error fetching Apple Music library at offset %d", offset
        )
    except httpx.HTTPError:
        logger.exception("Connection error fetching Apple Music library")

    logger.info("Fetched %d Apple Music library songs", len(results))
    return results


async def fetch_apple_music_recently_played(
    developer_token: str,
    music_user_token: str,
    *,
    limit: int = 10,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """Fetch user's recently played Apple Music tracks as history-shaped dicts.

    Paginates up to max_pages (default 50 tracks). Apple Music recently played
    is capped at ~50 items with no timestamps.

    Args:
        developer_token: ES256-signed Apple Developer Token.
        music_user_token: Apple Music User Token from MusicKit JS.
        limit: Items per page (max 10 for recently played).
        max_pages: Maximum pages to fetch.

    Returns:
        List of listening_history-compatible dicts.
    """
    results: list[dict[str, Any]] = []
    offset = 0
    pages_fetched = 0
    headers = {
        "Authorization": f"Bearer {developer_token}",
        "Music-User-Token": music_user_token,
    }

    try:
        async with httpx.AsyncClient() as client:
            while pages_fetched < max_pages:
                resp = await client.get(
                    f"{APPLE_MUSIC_API_BASE}/me/recent/played/tracks",
                    headers=headers,
                    params={"limit": limit, "offset": offset},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("data", [])
                if not items:
                    break
                for resource in items:
                    attrs = resource.get("attributes", {})
                    play_params = attrs.get("playParams", {})
                    results.append({
                        "song_id": (
                            play_params.get("catalogId")
                            or resource.get("id", "")
                        ),
                        "song_name": attrs.get("name", ""),
                        "artist_name": attrs.get("artistName", ""),
                        "album_name": attrs.get("albumName", ""),
                        "genre_names": attrs.get("genreNames", []),
                        "duration_ms": attrs.get("durationInMillis"),
                        "observed_at": None,  # Apple Music recently played has no timestamps
                        "service_source": "apple_music",
                    })
                pages_fetched += 1
                offset += limit
                if data.get("next") is None:
                    break
    except httpx.HTTPStatusError:
        logger.exception(
            "Error fetching Apple Music recently played at offset %d", offset
        )
    except httpx.HTTPError:
        logger.exception(
            "Connection error fetching Apple Music recently played"
        )

    logger.info("Fetched %d Apple Music recently played entries", len(results))
    return results
