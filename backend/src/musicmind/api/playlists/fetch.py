"""Fetch user playlists from Spotify and Apple Music APIs."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SPOTIFY_API = "https://api.spotify.com/v1"
APPLE_MUSIC_API = "https://api.music.apple.com/v1"


async def fetch_spotify_playlists(
    access_token: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch the user's Spotify playlists.

    Returns list of playlist dicts with id, name, description, track_count,
    artwork_url, owner.
    """
    results: list[dict[str, Any]] = []
    offset = 0

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while len(results) < limit:
                resp = await client.get(
                    f"{SPOTIFY_API}/me/playlists",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"limit": min(50, limit - len(results)), "offset": offset},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break

                for pl in items:
                    images = pl.get("images", [])
                    results.append({
                        "service_playlist_id": pl.get("id", ""),
                        "name": pl.get("name", ""),
                        "description": (pl.get("description") or "").strip(),
                        "track_count": pl.get("tracks", {}).get("total", 0),
                        "artwork_url": images[0].get("url", "") if images else "",
                        "owner": pl.get("owner", {}).get("display_name", ""),
                        "service": "spotify",
                    })

                offset += len(items)
                if data.get("next") is None:
                    break
    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception("Error fetching Spotify playlists")

    logger.info("Fetched %d Spotify playlists", len(results))
    return results


async def fetch_spotify_playlist_tracks(
    access_token: str,
    playlist_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch tracks from a specific Spotify playlist."""
    results: list[dict[str, Any]] = []
    offset = 0

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while len(results) < limit:
                resp = await client.get(
                    f"{SPOTIFY_API}/playlists/{playlist_id}/tracks",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"limit": min(100, limit - len(results)), "offset": offset},
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
                    artists = track.get("artists", [])
                    album = track.get("album", {})
                    images = album.get("images", [])
                    results.append({
                        "catalog_id": track.get("id", ""),
                        "name": track.get("name", ""),
                        "artist_name": artists[0].get("name", "") if artists else "",
                        "album_name": album.get("name", ""),
                        "artwork_url": images[0].get("url", "") if images else "",
                        "genre_names": [],
                        "service": "spotify",
                    })

                offset += len(items)
                if data.get("next") is None:
                    break
    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception("Error fetching Spotify playlist tracks")

    return results


async def fetch_apple_music_playlists(
    developer_token: str,
    music_user_token: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch the user's Apple Music library playlists."""
    results: list[dict[str, Any]] = []
    offset = 0

    headers = {
        "Authorization": f"Bearer {developer_token}",
        "Music-User-Token": music_user_token,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while len(results) < limit:
                resp = await client.get(
                    f"{APPLE_MUSIC_API}/me/library/playlists",
                    headers=headers,
                    params={"limit": min(25, limit - len(results)), "offset": offset},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("data", [])
                if not items:
                    break

                for pl in items:
                    attrs = pl.get("attributes", {})
                    artwork = attrs.get("artwork", {})
                    artwork_url = ""
                    if artwork.get("url"):
                        artwork_url = (
                            artwork["url"]
                            .replace("{w}", "300")
                            .replace("{h}", "300")
                        )
                    results.append({
                        "service_playlist_id": pl.get("id", ""),
                        "name": attrs.get("name", ""),
                        "description": (
                            attrs.get("description", {}).get("standard", "")
                            if isinstance(attrs.get("description"), dict)
                            else str(attrs.get("description", ""))
                        ),
                        "track_count": 0,  # Apple doesn't include count in list
                        "artwork_url": artwork_url,
                        "owner": "",
                        "service": "apple_music",
                    })

                offset += len(items)
                if data.get("next") is None:
                    break
    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception("Error fetching Apple Music playlists")

    logger.info("Fetched %d Apple Music playlists", len(results))
    return results


async def fetch_apple_music_playlist_tracks(
    developer_token: str,
    music_user_token: str,
    playlist_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch tracks from a specific Apple Music library playlist."""
    results: list[dict[str, Any]] = []
    offset = 0

    headers = {
        "Authorization": f"Bearer {developer_token}",
        "Music-User-Token": music_user_token,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while len(results) < limit:
                resp = await client.get(
                    f"{APPLE_MUSIC_API}/me/library/playlists/{playlist_id}/tracks",
                    headers=headers,
                    params={
                        "limit": min(100, limit - len(results)),
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
                    artwork = attrs.get("artwork", {})
                    artwork_url = ""
                    if artwork.get("url"):
                        artwork_url = (
                            artwork["url"]
                            .replace("{w}", "300")
                            .replace("{h}", "300")
                        )
                    results.append({
                        "catalog_id": (
                            play_params.get("catalogId")
                            or resource.get("id", "")
                        ),
                        "name": attrs.get("name", ""),
                        "artist_name": attrs.get("artistName", ""),
                        "album_name": attrs.get("albumName", ""),
                        "artwork_url": artwork_url,
                        "genre_names": attrs.get("genreNames", []),
                        "service": "apple_music",
                    })

                offset += len(items)
                if data.get("next") is None:
                    break
    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception("Error fetching Apple Music playlist tracks")

    return results
