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
    depth: int = 2,
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

    Takes the user's top 3 genres and searches for songs in those genres
    that they might not have heard.
    """
    genre_vector = profile.get("genre_vector", {})
    if not genre_vector:
        return []

    # Get top genres
    sorted_genres = sorted(genre_vector.items(), key=lambda x: x[1], reverse=True)
    top_genres = [g for g, _ in sorted_genres[:5]]

    candidates = []
    for genre in top_genres:
        try:
            result = await client.search_catalog(
                genre, types="songs", limit=10
            )
            for song in result.songs.data:
                cache = extract_song_cache_data(song)
                if cache:
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
    """Search for editorial playlists in the user's top genres and extract songs."""
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

    # Fallback: search for songs with editorial keywords
    for genre in top_genres:
        try:
            result = await client.search_catalog(
                f"best new {genre}", types="songs", limit=10
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
    """Fetch chart songs and return them as candidates for scoring."""
    candidates = []
    try:
        charts = await client.get_charts(types="songs", limit=limit)
        for chart in charts.songs:
            data = chart.get("data", []) if isinstance(chart, dict) else []
            for item in data:
                from musicmind.models import Resource

                r = Resource(**item)
                cache = extract_song_cache_data(r)
                if cache:
                    candidates.append(cache)
                    await queries.upsert_song_metadata([cache])
    except Exception as e:
        logger.warning("Error fetching charts: %s", e)

    return candidates
