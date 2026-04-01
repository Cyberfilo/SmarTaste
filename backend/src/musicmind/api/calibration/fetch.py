"""Fetch library albums from Spotify and Apple Music for calibration wizard."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SPOTIFY_API = "https://api.spotify.com/v1"
APPLE_MUSIC_API = "https://api.music.apple.com/v1"


async def fetch_spotify_albums(
    access_token: str,
    *,
    limit: int = 50,
    max_pages: int = 4,
) -> list[dict[str, Any]]:
    """Fetch the user's saved Spotify albums with artwork.

    Returns list of album dicts with id, name, artist_name, artwork_url,
    track_count, release_date.
    """
    results: list[dict[str, Any]] = []
    offset = 0
    pages_fetched = 0
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while pages_fetched < max_pages:
                resp = await client.get(
                    f"{SPOTIFY_API}/me/albums",
                    headers=headers,
                    params={"limit": min(50, limit), "offset": offset},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    album = item.get("album", {})
                    artists = album.get("artists", [])
                    images = album.get("images", [])
                    results.append({
                        "album_id": album.get("id", ""),
                        "name": album.get("name", ""),
                        "artist_name": artists[0].get("name", "") if artists else "",
                        "artwork_url": images[0].get("url", "") if images else "",
                        "track_count": album.get("total_tracks", 0),
                        "release_date": album.get("release_date", ""),
                        "service": "spotify",
                    })

                pages_fetched += 1
                offset += len(items)
                if data.get("next") is None:
                    break
    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception("Error fetching Spotify albums")

    logger.info("Fetched %d Spotify albums", len(results))
    return results


async def fetch_apple_music_albums(
    developer_token: str,
    music_user_token: str,
    *,
    limit: int = 100,
    max_pages: int = 3,
) -> list[dict[str, Any]]:
    """Fetch the user's Apple Music library albums with artwork.

    Returns list of album dicts with id, name, artist_name, artwork_url,
    track_count, release_date.
    """
    results: list[dict[str, Any]] = []
    offset = 0
    pages_fetched = 0
    headers = {
        "Authorization": f"Bearer {developer_token}",
        "Music-User-Token": music_user_token,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while pages_fetched < max_pages:
                resp = await client.get(
                    f"{APPLE_MUSIC_API}/me/library/albums",
                    headers=headers,
                    params={
                        "limit": min(25, limit),
                        "offset": offset,
                        "include[library-albums]": "catalog",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("data", [])
                if not items:
                    break

                for resource in items:
                    attrs = resource.get("attributes", {})
                    artwork = attrs.get("artwork", {})
                    artwork_url = ""
                    if artwork.get("url"):
                        artwork_url = (
                            artwork["url"]
                            .replace("{w}", "300")
                            .replace("{h}", "300")
                        )
                    results.append({
                        "album_id": resource.get("id", ""),
                        "name": attrs.get("name", ""),
                        "artist_name": attrs.get("artistName", ""),
                        "artwork_url": artwork_url,
                        "track_count": attrs.get("trackCount", 0),
                        "release_date": attrs.get("releaseDate", ""),
                        "service": "apple_music",
                    })

                pages_fetched += 1
                offset += len(items)
                if data.get("next") is None:
                    break
    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.exception("Error fetching Apple Music albums")

    logger.info("Fetched %d Apple Music albums", len(results))
    return results
