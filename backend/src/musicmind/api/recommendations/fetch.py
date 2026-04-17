"""Discovery fetch functions for Spotify and Apple Music recommendation pipeline.

Implements 4 discovery strategies (similar_artist_crawl, genre_adjacent,
editorial_mining, chart_filter) against both services. Each function
returns song_metadata_cache-compatible dicts for scoring.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
APPLE_MUSIC_API_BASE = "https://api.music.apple.com/v1"

MAX_RETRIES = 3
BACKOFF_BASE = 1.0

# Shared connection pool — reused across all discovery strategies.
# Avoids creating separate TCP+SSL connections per strategy.
_shared_client: httpx.AsyncClient | None = None


def _get_shared_client() -> httpx.AsyncClient:
    """Get or create a shared httpx client with connection pooling."""
    global _shared_client  # noqa: PLW0603
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _shared_client


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """HTTP request with exponential backoff on 429/5xx."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", BACKOFF_BASE * (2 ** attempt)))
                wait = min(retry_after, 30.0)
                logger.warning("429 from %s, retry in %.1fs (attempt %d)", url[:80], wait, attempt)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < MAX_RETRIES:
                wait = BACKOFF_BASE * (2 ** attempt)
                logger.warning("%d from %s, retry in %.1fs", resp.status_code, url[:80], wait)
                await asyncio.sleep(wait)
                continue
            return resp
        except httpx.TimeoutException:
            if attempt < MAX_RETRIES:
                logger.warning("Timeout from %s, retry %d", url[:80], attempt)
                await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                continue
            raise
    return resp  # Last attempt's response


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
        client = _get_shared_client()
        if service == "spotify":
            resp = await _request_with_retry(client, "GET",
                f"{SPOTIFY_API_BASE}/search",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"q": artist_name, "type": "artist", "limit": 1},
            )
            resp.raise_for_status()
            items = resp.json().get("artists", {}).get("items", [])
            return items[0].get("id") if items else None

        elif service == "apple_music":
            headers = {"Authorization": f"Bearer {developer_token or access_token}"}
            resp = await _request_with_retry(client, "GET",
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


# ── Genre Helpers ───────────────────────────────────────────────────────────


def _genre_overlap_with_expansion(
    track_genres: list[str],
    profile_genres: list[str],
) -> bool:
    """Check genre overlap after expanding both sides to include parent genres.

    Converts "Italian Hip-Hop/Rap" → ["Italian Hip-Hop/Rap", "Hip-Hop/Rap"]
    on both sides before set intersection, so regional and parent genres
    match each other correctly.
    """
    if not track_genres or not profile_genres:
        return False
    from musicmind.engine.profile import expand_genres
    track_set = {g.lower() for g in expand_genres(track_genres)}
    profile_set = {g.lower() for g in expand_genres(profile_genres)}
    return bool(track_set & profile_set)


# ── Discovery Strategies ────────────────────────────────────────────────────


def allocate_seed_budget(
    scored_seeds: list[tuple[str, float]],
    *,
    total_budget: int,
) -> dict[str, int]:
    """Allocate track-fetch budget across seeds proportional to affinity.

    Every seed gets at least 1 slot (prevents tiny-affinity seeds from being
    silently dropped). Unallocated slots from rounding go to highest-affinity seeds.
    """
    if not scored_seeds or total_budget <= 0:
        return {}
    positive = [(n, max(0.001, s)) for n, s in scored_seeds]
    total_weight = sum(s for _, s in positive)
    allocation: dict[str, int] = {}
    for name, score in positive:
        share = int(total_budget * score / total_weight)
        allocation[name] = max(1, share)
    spent = sum(allocation.values())
    leftover = total_budget - spent
    if leftover > 0:
        for name, _ in sorted(positive, key=lambda x: x[1], reverse=True):
            if leftover <= 0:
                break
            allocation[name] += 1
            leftover -= 1
    return allocation


async def discover_similar_artists(
    service: str,
    access_token: str,
    scored_seeds: list[tuple[str, float]],
    *,
    developer_token: str | None = None,
    storefront: str = "us",
    depth: int = 1,
    total_budget: int = 40,
) -> list[dict[str, Any]]:
    """Crawl similar artists with affinity-proportional budget + positional weights.

    Args:
        service: "spotify" or "apple_music".
        access_token: Valid access token for the service.
        scored_seeds: [(artist_name, affinity_score)] from taste profile.
        developer_token: Required for Apple Music (ES256 JWT).
        storefront: Apple Music storefront (default "us").
        depth: Hops of similar artists (default 1, unused reserved param).
        total_budget: Total track-fetch budget across all seeds.

    Returns:
        List of song_metadata_cache-compatible dicts, each with _discovery_weight.
    """
    import math as _math

    candidates: list[dict[str, Any]] = []

    if not scored_seeds:
        return candidates

    allocation = allocate_seed_budget(scored_seeds, total_budget=total_budget)

    for seed_name, seed_affinity in scored_seeds:
        per_seed_budget = allocation.get(seed_name, 1)
        related_count = max(1, min(5, int(_math.ceil(_math.sqrt(per_seed_budget)))))
        songs_per_artist = max(1, per_seed_budget // related_count)

        aid = await _search_artist_id(
            service, access_token, seed_name,
            developer_token=developer_token, storefront=storefront,
        )
        if not aid:
            continue

        client = _get_shared_client()
        try:
            related = await _fetch_related_artists(
                client, service, access_token, aid,
                developer_token=developer_token,
                storefront=storefront,
                limit=related_count,
            )
        except (httpx.HTTPStatusError, httpx.HTTPError):
            logger.warning("Error crawling seed %s on %s", seed_name, service)
            continue

        for position, (rid, artist_genres) in enumerate(related):
            positional_weight = 1.0 / (1.0 + position * 0.3)
            try:
                tracks = await _fetch_artist_top_tracks(
                    client, service, access_token, rid,
                    developer_token=developer_token,
                    storefront=storefront,
                    limit=songs_per_artist,
                )
            except (httpx.HTTPStatusError, httpx.HTTPError):
                continue
            if service == "spotify" and artist_genres:
                for t in tracks:
                    if not t.get("genre_names"):
                        t["genre_names"] = artist_genres
            for t in tracks:
                t["_discovery_weight"] = positional_weight * seed_affinity
            candidates.extend(tracks)

    logger.info(
        "Discovered %d tracks via similar_artists on %s (budget=%d)",
        len(candidates), service, total_budget,
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
        client = _get_shared_client()
        for genre in top_genres:
            try:
                if service == "spotify":
                    genre_slug = genre.lower().replace("/", " ")
                    resp = await _request_with_retry(client, "GET",
                        f"{SPOTIFY_API_BASE}/search",
                        headers={"Authorization": f"Bearer {access_token}"},
                        params={
                            "q": f"genre:{genre_slug}",
                            "type": "track",
                            "limit": limit,
                        },
                    )
                    resp.raise_for_status()
                    items = resp.json().get("tracks", {}).get("items", [])

                    # Batch-fetch artist genres for Spotify tracks
                    # (Spotify tracks don't carry genres)
                    artist_ids = list({
                        a["id"]
                        for t in items
                        for a in t.get("artists", [])
                        if a.get("id")
                    })[:20]
                    artist_genre_map: dict[str, list[str]] = {}
                    if artist_ids:
                        try:
                            resp2 = await _request_with_retry(
                                client, "GET",
                                f"{SPOTIFY_API_BASE}/artists",
                                headers={
                                    "Authorization": f"Bearer {access_token}",
                                },
                                params={"ids": ",".join(artist_ids)},
                            )
                            if resp2.is_success:
                                for a in resp2.json().get("artists", []):
                                    if a and a.get("id"):
                                        artist_genre_map[a["id"]] = (
                                            a.get("genres", [])
                                        )
                        except Exception:
                            pass  # Fall back to search-query genre

                    for t in items:
                        track = _spotify_track_to_cache_dict(t)
                        # Enrich genres from artist data first, then query
                        if not track["genre_names"]:
                            first_artist_id = (
                                t.get("artists", [{}])[0].get("id", "")
                                if t.get("artists") else ""
                            )
                            artist_genres = artist_genre_map.get(
                                first_artist_id, [],
                            )
                            track["genre_names"] = (
                                artist_genres if artist_genres else [genre]
                            )
                        candidates.append(track)

                elif service == "apple_music":
                    headers = {
                        "Authorization": f"Bearer {developer_token or access_token}"
                    }
                    resp = await _request_with_retry(client, "GET",
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
        client = _get_shared_client()
        for genre in top_genres:
            try:
                if service == "spotify":
                    # Spotify search: use genre: filter + year range for fresh tracks
                    # "best new {genre}" returns literal text matches, not editorial
                    genre_slug = genre.lower().replace("/", " ").replace("&", " ")
                    query = f"genre:{genre_slug} year:2024-2026"
                    resp = await _request_with_retry(client, "GET",
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
                    for t in items:
                        track = _spotify_track_to_cache_dict(t)
                        if not track["genre_names"]:
                            track["genre_names"] = [genre]
                        candidates.append(track)

                elif service == "apple_music":
                    genre_term = genre.replace("/", " ")
                    headers = {
                        "Authorization": f"Bearer {developer_token or access_token}"
                    }
                    resp = await _request_with_retry(client, "GET",
                        f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/search",
                        headers=headers,
                        params={
                            "term": genre_term,
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
    profile_genres_top5 = list(profile_genres)[:5]

    try:
        client = _get_shared_client()
        if service == "spotify":
            resp = await _request_with_retry(client, "GET",
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
                    resp2 = await _request_with_retry(client, "GET",
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

            # Post-filter: only apply genre overlap to tracks that HAVE genres.
            # Spotify new-release tracks usually carry empty genre_names (genres
            # live on artist objects, not tracks). Pass them through when untagged;
            # reject only when tagged genres are known to not match.
            candidates = [
                c for c in candidates
                if not c.get("genre_names")
                or _genre_overlap_with_expansion(
                    c.get("genre_names", []),
                    profile_genres_top5,
                )
            ]

        elif service == "apple_music":
            headers = {
                "Authorization": f"Bearer {developer_token or access_token}"
            }
            resp = await _request_with_retry(client, "GET",
                f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/charts",
                headers=headers,
                params={"types": "songs", "limit": limit},
            )
            resp.raise_for_status()
            chart_data = resp.json().get("results", {}).get("songs", [])
            for chart in chart_data:
                for item in chart.get("data", []):
                    track = _apple_track_to_cache_dict(item)
                    # Filter: keep only tracks with expanded genre overlap
                    if _genre_overlap_with_expansion(
                        track.get("genre_names", []),
                        profile_genres_top5,
                    ):
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
) -> list[tuple[str, list[str]]]:
    """Fetch (id, genres) tuples for artists related to the given artist.

    Returns genres alongside IDs so Spotify tracks can be genre-backfilled
    (Spotify tracks don't carry genres — only artist objects do).
    """
    if service == "spotify":
        resp = await _request_with_retry(client, "GET",
            f"{SPOTIFY_API_BASE}/artists/{artist_id}/related-artists",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        artists = resp.json().get("artists", [])
        return [
            (a.get("id", ""), a.get("genres", []))
            for a in artists[:limit] if a.get("id")
        ]

    elif service == "apple_music":
        headers = {"Authorization": f"Bearer {developer_token or access_token}"}
        resp = await _request_with_retry(client, "GET",
            f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/artists/{artist_id}",
            headers=headers,
            params={"views": "similar-artists"},
        )
        resp.raise_for_status()
        views = resp.json().get("data", [{}])[0].get("views", {})
        similar = views.get("similar-artists", {}).get("data", [])
        return [
            (a.get("id", ""), a.get("attributes", {}).get("genreNames", []))
            for a in similar[:limit] if a.get("id")
        ]

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
        # market is required per Spotify API spec; use storefront as proxy
        market = storefront.upper() if len(storefront) == 2 else "US"
        resp = await _request_with_retry(client, "GET",
            f"{SPOTIFY_API_BASE}/artists/{artist_id}/top-tracks",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"market": market},
        )
        resp.raise_for_status()
        tracks = resp.json().get("tracks", [])
        return [_spotify_track_to_cache_dict(t) for t in tracks[:limit]]

    elif service == "apple_music":
        headers = {"Authorization": f"Bearer {developer_token or access_token}"}
        resp = await _request_with_retry(client, "GET",
            f"{APPLE_MUSIC_API_BASE}/catalog/{storefront}/artists/{artist_id}",
            headers=headers,
            params={"views": "top-songs"},
        )
        resp.raise_for_status()
        views = resp.json().get("data", [{}])[0].get("views", {})
        songs = views.get("top-songs", {}).get("data", [])
        return [_apple_track_to_cache_dict(s) for s in songs[:limit]]

    return []
