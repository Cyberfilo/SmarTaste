"""Discovery strategies — find new songs via similar artists, genre exploration, etc.

All strategies return candidate lists for the scorer.
They DO call the API (unlike profile/scorer which are local-only).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from musicmind.client import AppleMusicClient
from musicmind.db.queries import QueryExecutor
from musicmind.tools.helpers import extract_song_cache_data

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))


async def similar_artist_crawl(
    client: AppleMusicClient,
    queries: QueryExecutor,
    seed_artist_ids: list[str],
    depth: int = 1,
    songs_per_artist: int = 5,
) -> list[dict[str, Any]]:
    """Crawl similar artists and collect their top songs.

    Args:
        seed_artist_ids: Starting artist IDs to crawl from
        depth: How many hops of similar artists (1 or 2)
        songs_per_artist: Top songs to collect per discovered artist
    """
    candidates = []
    visited_artists: set[str] = set(seed_artist_ids)
    current_layer = list(seed_artist_ids)

    for _ in range(depth):
        next_layer = []
        for artist_id in current_layer[:10]:  # cap per layer
            try:
                similar = await client.get_similar_artists(artist_id, limit=5)
                for a in similar.data:
                    if a.id not in visited_artists:
                        visited_artists.add(a.id)
                        next_layer.append(a.id)

                        # Get top songs for this new artist
                        top = await client.get_artist_top_songs(
                            a.id, limit=songs_per_artist
                        )
                        for song in top.data:
                            cache = extract_song_cache_data(song)
                            if cache:
                                candidates.append(cache)
                                await queries.upsert_song_metadata([cache])
            except Exception as e:
                logger.warning("Error crawling artist %s: %s", artist_id, e)
                continue

        current_layer = next_layer

    return candidates


async def genre_adjacent_explore(
    client: AppleMusicClient,
    queries: QueryExecutor,
    profile: dict[str, Any],
    max_candidates: int = 30,
) -> list[dict[str, Any]]:
    """Search for songs in genres adjacent to the user's core genres.

    Uses the FULL regional genre names (e.g., "Italian Hip-Hop/Rap" not just
    "Hip-Hop/Rap") so the storefront returns regionally relevant results.
    Filters out candidates with zero genre overlap.
    """
    genre_vector = profile.get("genre_vector", {})
    if not genre_vector:
        return []

    # Get top genres — use full regional names for search queries
    sorted_genres = sorted(genre_vector.items(), key=lambda x: x[1], reverse=True)
    top_genres = [g for g, _ in sorted_genres[:5]]
    profile_genre_keys = set(genre_vector.keys())

    candidates = []
    for genre in top_genres:
        try:
            result = await client.search_catalog(
                genre, types="songs", limit=15
            )
            for song in result.songs.data:
                cache = extract_song_cache_data(song)
                if cache:
                    # Filter: only keep if at least one genre overlaps with profile
                    song_genres = set(cache.get("genre_names") or [])
                    from musicmind.engine.profile import expand_genres
                    song_expanded = set(expand_genres(list(song_genres)))
                    if song_expanded & profile_genre_keys:
                        candidates.append(cache)
                        await queries.upsert_song_metadata([cache])
        except Exception as e:
            logger.warning("Error exploring genre '%s': %s", genre, e)
            continue

        if len(candidates) >= max_candidates:
            break

    return candidates


async def editorial_mining(
    client: AppleMusicClient,
    queries: QueryExecutor,
    profile: dict[str, Any],
    max_candidates: int = 30,
) -> list[dict[str, Any]]:
    """Search for editorial playlists in the user's top genres and extract songs.

    Uses full regional genre names (e.g., "Italian Hip-Hop/Rap essentials")
    so the storefront returns regionally relevant results.
    """
    genre_vector = profile.get("genre_vector", {})
    if not genre_vector:
        return []

    sorted_genres = sorted(genre_vector.items(), key=lambda x: x[1], reverse=True)
    top_genres = [g for g, _ in sorted_genres[:3]]

    candidates = []
    for genre in top_genres:
        try:
            result = await client.search_catalog(
                f"{genre} essentials", types="playlists", limit=3
            )
            for playlist in result.playlists.data:
                # We can't directly get playlist tracks from catalog search
                # but we log the playlist for Claude to investigate
                pass
        except Exception as e:
            logger.warning("Error mining editorial for '%s': %s", genre, e)

    # Search for songs with editorial keywords using full genre names
    for genre in top_genres:
        try:
            result = await client.search_catalog(
                f"best new {genre}", types="songs", limit=15
            )
            for song in result.songs.data:
                cache = extract_song_cache_data(song)
                if cache:
                    candidates.append(cache)
                    await queries.upsert_song_metadata([cache])
        except Exception as e:
            logger.warning("Error in editorial search for '%s': %s", genre, e)

        if len(candidates) >= max_candidates:
            break

    return candidates


async def chart_filter(
    client: AppleMusicClient,
    queries: QueryExecutor,
    profile: dict[str, Any],
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch chart songs filtered by the user's top genre.

    Passes the user's #1 genre to get_charts() so the API returns
    genre-relevant charts instead of global pop. Then pre-filters
    candidates to only keep songs with at least one genre overlap
    with the user's top 5 genres.
    """
    genre_vector = profile.get("genre_vector", {})

    # Use the user's top genre as chart genre filter
    top_genre_id = None
    if genre_vector:
        sorted_genres = sorted(genre_vector.items(), key=lambda x: x[1], reverse=True)
        top_genre_id = sorted_genres[0][0] if sorted_genres else None

    # Get top 5 genres for overlap filtering
    top5 = set()
    if genre_vector:
        sorted_g = sorted(genre_vector.items(), key=lambda x: x[1], reverse=True)
        top5 = {g for g, _ in sorted_g[:5]}
        # Also add expanded parents for matching
        from musicmind.engine.profile import expand_genres
        expanded_top5 = set()
        for g in top5:
            expanded_top5.update(expand_genres([g]))
        top5 = expanded_top5

    candidates = []
    try:
        charts = await client.get_charts(
            types="songs", genre=top_genre_id, limit=limit
        )
        for chart in charts.songs:
            data = chart.get("data", []) if isinstance(chart, dict) else []
            for item in data:
                from musicmind.models import Resource

                r = Resource(**item)
                cache = extract_song_cache_data(r)
                if cache:
                    # Pre-filter: only keep if genres overlap with user's top genres
                    song_genres = set(cache.get("genre_names") or [])
                    song_expanded = set(expand_genres(list(song_genres)))
                    if song_expanded & top5:
                        candidates.append(cache)
                        await queries.upsert_song_metadata([cache])
    except Exception as e:
        logger.warning("Error fetching charts: %s", e)

    return candidates
