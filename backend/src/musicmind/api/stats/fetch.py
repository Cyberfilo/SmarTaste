"""Data fetching for listening stats.

Spotify top items by time period, Apple Music computed stats from library data.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"

SPOTIFY_TIME_RANGE_MAP: dict[str, str] = {
    "month": "short_term",
    "6months": "medium_term",
    "alltime": "long_term",
}

PERIOD_DAYS_MAP: dict[str, int | None] = {
    "month": 30,
    "6months": 180,
    "alltime": None,
}


# -- Spotify Fetch Functions --------------------------------------------------


async def fetch_spotify_top_tracks_for_period(
    access_token: str,
    *,
    period: str = "month",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch user's top tracks from Spotify for a given time period.

    Uses Spotify's /me/top/tracks endpoint with the time_range parameter
    mapped from the application period names (month, 6months, alltime) to
    Spotify's native terms (short_term, medium_term, long_term).

    Args:
        access_token: Valid Spotify access token.
        period: Time period -- one of "month", "6months", "alltime".
        limit: Maximum number of tracks to return (max 50 per page).

    Returns:
        List of track dicts with rank, name, artist_name, album_name,
        duration_ms, catalog_id, preview_url.

    Raises:
        ValueError: If period is not one of the valid values.
    """
    time_range = SPOTIFY_TIME_RANGE_MAP.get(period)
    if time_range is None:
        msg = f"Invalid period '{period}'. Must be one of: {', '.join(SPOTIFY_TIME_RANGE_MAP)}"
        raise ValueError(msg)

    results: list[dict[str, Any]] = []
    offset = 0
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient() as client:
            while offset < limit:
                page_size = min(50, limit - offset)
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/me/top/tracks",
                    headers=headers,
                    params={
                        "time_range": time_range,
                        "limit": page_size,
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
                for item in items:
                    artists = item.get("artists", [])
                    artist_name = artists[0].get("name", "") if artists else ""
                    album = item.get("album", {})
                    results.append({
                        "rank": len(results) + 1,
                        "name": item.get("name", ""),
                        "artist_name": artist_name,
                        "album_name": album.get("name", ""),
                        "duration_ms": item.get("duration_ms"),
                        "catalog_id": item.get("id", ""),
                        "preview_url": item.get("preview_url") or "",
                    })
                offset += page_size
                if data.get("next") is None:
                    break
    except httpx.HTTPStatusError:
        logger.exception(
            "Error fetching Spotify top tracks for period %s at offset %d",
            period,
            offset,
        )
    except httpx.HTTPError:
        logger.exception("Connection error fetching Spotify top tracks")

    logger.info("Fetched %d Spotify top tracks for period %s", len(results), period)
    return results


async def fetch_spotify_top_artists_for_period(
    access_token: str,
    *,
    period: str = "month",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch user's top artists from Spotify for a given time period.

    Uses Spotify's /me/top/artists endpoint with the time_range parameter.

    Args:
        access_token: Valid Spotify access token.
        period: Time period -- one of "month", "6months", "alltime".
        limit: Maximum number of artists to return (max 50 per page).

    Returns:
        List of artist dicts with rank, name, id, genres.

    Raises:
        ValueError: If period is not one of the valid values.
    """
    time_range = SPOTIFY_TIME_RANGE_MAP.get(period)
    if time_range is None:
        msg = f"Invalid period '{period}'. Must be one of: {', '.join(SPOTIFY_TIME_RANGE_MAP)}"
        raise ValueError(msg)

    results: list[dict[str, Any]] = []
    offset = 0
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient() as client:
            while offset < limit:
                page_size = min(50, limit - offset)
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/me/top/artists",
                    headers=headers,
                    params={
                        "time_range": time_range,
                        "limit": page_size,
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
                        "rank": len(results) + 1,
                        "name": item.get("name", ""),
                        "id": item.get("id", ""),
                        "genres": item.get("genres", []),
                    })
                offset += page_size
                if data.get("next") is None:
                    break
    except httpx.HTTPStatusError:
        logger.exception(
            "Error fetching Spotify top artists for period %s at offset %d",
            period,
            offset,
        )
    except httpx.HTTPError:
        logger.exception("Connection error fetching Spotify top artists")

    logger.info("Fetched %d Spotify top artists for period %s", len(results), period)
    return results


# -- Apple Music Compute Functions --------------------------------------------


def _parse_date(value: Any) -> datetime | None:
    """Parse a date string or datetime into a timezone-aware datetime.

    Handles ISO 8601 strings (with or without timezone), bare date strings
    (YYYY-MM-DD), and datetime objects.  Returns None for unparseable input.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
        # Bare date like "2024-06-15"
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        except (ValueError, TypeError):
            pass
    return None


def _song_date(song: dict[str, Any]) -> datetime | None:
    """Best-effort date for a song: dateAdded → releaseDate fallback.

    Apple Music's library API often returns dateAdded as null.  When that
    happens, fall back to releaseDate so period filtering still works.
    """
    parsed = _parse_date(song.get("date_added_to_library"))
    if parsed is not None:
        return parsed
    return _parse_date(song.get("release_date"))


def _filter_songs_by_period(
    songs: list[dict[str, Any]],
    *,
    period: str,
) -> list[dict[str, Any]]:
    """Filter songs by date based on the requested period.

    Uses date_added_to_library when available, falling back to release_date.
    Songs with no usable date are included in "alltime" and excluded from
    shorter periods (since we can't determine when they were added).

    Args:
        songs: List of song_metadata_cache-compatible dicts.
        period: Time period -- "month", "6months", or "alltime".

    Returns:
        Filtered list of songs within the requested period.
    """
    days = PERIOD_DAYS_MAP.get(period)
    if days is None:
        # alltime -- return everything
        return list(songs)

    cutoff = datetime.now(UTC) - timedelta(days=days)

    filtered: list[dict[str, Any]] = []
    for song in songs:
        parsed = _song_date(song)
        if parsed is None:
            # No date at all — skip for period filtering
            continue
        if parsed >= cutoff:
            filtered.append(song)
    return filtered


def compute_apple_music_top_tracks(
    songs: list[dict[str, Any]],
    *,
    period: str = "month",
) -> list[dict[str, Any]]:
    """Compute top tracks from Apple Music cached library data.

    Apple Music has no native time-range API. This function filters by
    date_added_to_library and sorts by most recently added as a proxy
    for listening frequency.

    Args:
        songs: List of song_metadata_cache-compatible dicts with date_added_to_library.
        period: Time period -- "month", "6months", or "alltime".

    Returns:
        List of ranked track dicts with rank, name, artist_name, album_name, catalog_id.
    """
    filtered = _filter_songs_by_period(songs, period=period)

    # Sort by best available date descending (most recent first)
    def _sort_key(s: dict[str, Any]) -> str:
        parsed = _song_date(s)
        if parsed is not None:
            return parsed.isoformat()
        return ""

    filtered.sort(key=_sort_key, reverse=True)

    results: list[dict[str, Any]] = []
    for i, song in enumerate(filtered, start=1):
        results.append({
            "rank": i,
            "name": song.get("name", ""),
            "artist_name": song.get("artist_name", ""),
            "album_name": song.get("album_name", ""),
            "catalog_id": song.get("catalog_id", ""),
        })

    logger.info(
        "Computed %d Apple Music top tracks for period %s", len(results), period
    )
    return results


def compute_apple_music_top_artists(
    songs: list[dict[str, Any]],
    *,
    period: str = "month",
) -> list[dict[str, Any]]:
    """Compute top artists from Apple Music cached library data.

    Aggregates from filtered tracks: groups by artist_name, counts tracks
    per artist, and collects the union of genres from each artist's tracks.

    Args:
        songs: List of song_metadata_cache-compatible dicts.
        period: Time period -- "month", "6months", or "alltime".

    Returns:
        List of ranked artist dicts with rank, name, genres, track_count.
    """
    filtered = _filter_songs_by_period(songs, period=period)

    artist_tracks: dict[str, int] = {}
    artist_genres: dict[str, set[str]] = {}

    from musicmind.engine.profile import parse_artists

    for song in filtered:
        raw_artist = song.get("artist_name", "")
        if not raw_artist:
            continue
        # Credit both primary and featuring artists
        for name, _weight in parse_artists(raw_artist):
            artist_tracks[name] = artist_tracks.get(name, 0) + 1
        genres = song.get("genre_names", [])
        if isinstance(genres, str):
            import json

            try:
                genres = json.loads(genres)
            except (json.JSONDecodeError, TypeError):
                genres = []
        for name, _weight in parse_artists(raw_artist):
            if name not in artist_genres:
                artist_genres[name] = set()
            artist_genres[name].update(genres)

    # Sort by track count descending
    sorted_artists = sorted(
        artist_tracks.items(), key=lambda x: x[1], reverse=True
    )

    results: list[dict[str, Any]] = []
    for i, (name, track_count) in enumerate(sorted_artists, start=1):
        results.append({
            "rank": i,
            "name": name,
            "genres": sorted(artist_genres.get(name, set())),
            "track_count": track_count,
        })

    logger.info(
        "Computed %d Apple Music top artists for period %s", len(results), period
    )
    return results
