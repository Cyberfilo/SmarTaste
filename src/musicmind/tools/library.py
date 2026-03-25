"""Library browsing MCP tools — songs, albums, artists, playlists."""

from __future__ import annotations

from musicmind.server import mcp
from musicmind.tools.helpers import (
    extract_song_cache_data,
    format_album_md,
    format_artist_md,
    format_playlist_md,
    format_song_md,
)


def _ctx():
    ctx = mcp.get_context()
    lc = ctx.request_context.lifespan_context
    return lc["client"], lc["queries"]


@mcp.tool()
async def musicmind_library_songs(
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Browse songs in your Apple Music library.

    Returns paginated library songs with genre and metadata.
    Songs are automatically cached for taste analysis.

    Args:
        limit: Number of songs to return (max 100)
        offset: Pagination offset
    """
    client, queries = _ctx()
    result = await client.get_library_songs(limit=min(limit, 100), offset=offset)

    # Cache songs
    cache_data = []
    for r in result.data:
        song = extract_song_cache_data(r)
        if song:
            cache_data.append(song)
    if cache_data:
        await queries.upsert_song_metadata(cache_data)

    if not result.data:
        return "No songs found in your library."

    lines = [f"## Library Songs (showing {len(result.data)}, offset {offset})"]
    for i, r in enumerate(result.data, start=offset + 1):
        lines.append(format_song_md(r, index=i))

    if result.has_more:
        lines.append(f"\n*More available — use offset={offset + limit}*")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_library_albums(
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Browse albums in your Apple Music library.

    Args:
        limit: Number of albums to return (max 100)
        offset: Pagination offset
    """
    client, queries = _ctx()
    result = await client.get_library_albums(limit=min(limit, 100), offset=offset)

    if not result.data:
        return "No albums found in your library."

    lines = [f"## Library Albums (showing {len(result.data)}, offset {offset})"]
    for i, r in enumerate(result.data, start=offset + 1):
        lines.append(format_album_md(r, index=i))

    if result.has_more:
        lines.append(f"\n*More available — use offset={offset + limit}*")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_library_artists(
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Browse artists in your Apple Music library.

    Args:
        limit: Number of artists to return (max 100)
        offset: Pagination offset
    """
    client, queries = _ctx()
    result = await client.get_library_artists(limit=min(limit, 100), offset=offset)

    if not result.data:
        return "No artists found in your library."

    lines = [f"## Library Artists (showing {len(result.data)}, offset {offset})"]
    for i, r in enumerate(result.data, start=offset + 1):
        lines.append(format_artist_md(r, index=i))

    if result.has_more:
        lines.append(f"\n*More available — use offset={offset + limit}*")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_library_playlists() -> str:
    """List all playlists in your Apple Music library."""
    client, queries = _ctx()
    result = await client.get_library_playlists(limit=100)

    if not result.data:
        return "No playlists found in your library."

    lines = [f"## Library Playlists ({len(result.data)})"]
    for i, r in enumerate(result.data, start=1):
        lines.append(format_playlist_md(r, index=i))

    return "\n".join(lines)


@mcp.tool()
async def musicmind_playlist_tracks(playlist_id: str) -> str:
    """Get all tracks in a specific library playlist.

    Args:
        playlist_id: The library playlist ID (e.g., "p.AbCdEfGh")
    """
    client, queries = _ctx()
    result = await client.get_playlist_tracks(playlist_id)

    # Cache songs
    cache_data = []
    for r in result.data:
        song = extract_song_cache_data(r)
        if song:
            cache_data.append(song)
    if cache_data:
        await queries.upsert_song_metadata(cache_data)

    if not result.data:
        return f"No tracks found in playlist `{playlist_id}`."

    lines = [f"## Playlist Tracks ({len(result.data)} songs)"]
    for i, r in enumerate(result.data, start=1):
        lines.append(format_song_md(r, index=i))

    return "\n".join(lines)


@mcp.tool()
async def musicmind_search_library(
    query: str,
    types: str = "library-songs,library-albums,library-artists",
) -> str:
    """Search within your Apple Music library.

    Args:
        query: Search term
        types: Comma-separated types to search (library-songs, library-albums, library-artists)
    """
    client, queries = _ctx()
    result = await client.search_library(query, types=types)

    lines = [f'## Library Search: "{query}"']

    if result.songs.data:
        lines.append(f"\n### Songs ({len(result.songs.data)})")
        for i, r in enumerate(result.songs.data, start=1):
            lines.append(format_song_md(r, index=i))
            song = extract_song_cache_data(r)
            if song:
                await queries.upsert_song_metadata([song])

    if result.albums.data:
        lines.append(f"\n### Albums ({len(result.albums.data)})")
        for i, r in enumerate(result.albums.data, start=1):
            lines.append(format_album_md(r, index=i))

    if result.artists.data:
        lines.append(f"\n### Artists ({len(result.artists.data)})")
        for i, r in enumerate(result.artists.data, start=1):
            lines.append(format_artist_md(r, index=i))

    if len(lines) == 1:
        lines.append("No results found.")

    return "\n".join(lines)
