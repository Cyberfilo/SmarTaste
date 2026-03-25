"""Catalog search and lookup MCP tools."""

from __future__ import annotations

from musicmind.server import mcp
from musicmind.tools.helpers import (
    extract_artist_cache_data,
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
async def musicmind_search(
    query: str,
    types: str = "songs,albums,artists",
    limit: int = 10,
) -> str:
    """Search the Apple Music catalog.

    Args:
        query: Search term (e.g., "Drake", "chill lofi beats", "Radiohead OK Computer")
        types: Comma-separated types: songs, albums, artists, playlists
        limit: Number of results per type (max 25)
    """
    client, queries = _ctx()
    result = await client.search_catalog(query, types=types, limit=min(limit, 25))

    lines = [f'## Catalog Search: "{query}"']

    if result.songs.data:
        lines.append(f"\n### Songs ({len(result.songs.data)})")
        cache_data = []
        for i, r in enumerate(result.songs.data, start=1):
            lines.append(format_song_md(r, index=i))
            song = extract_song_cache_data(r)
            if song:
                cache_data.append(song)
        if cache_data:
            await queries.upsert_song_metadata(cache_data)

    if result.albums.data:
        lines.append(f"\n### Albums ({len(result.albums.data)})")
        for i, r in enumerate(result.albums.data, start=1):
            lines.append(format_album_md(r, index=i))

    if result.artists.data:
        lines.append(f"\n### Artists ({len(result.artists.data)})")
        for i, r in enumerate(result.artists.data, start=1):
            lines.append(format_artist_md(r, index=i))

    if result.playlists.data:
        lines.append(f"\n### Playlists ({len(result.playlists.data)})")
        for i, r in enumerate(result.playlists.data, start=1):
            lines.append(format_playlist_md(r, index=i))

    if len(lines) == 1:
        lines.append("No results found.")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_lookup_song(song_id: str) -> str:
    """Get full details for a catalog song including metadata and relationships.

    Args:
        song_id: Apple Music catalog song ID
    """
    client, queries = _ctx()
    resource = await client.get_song(song_id)
    attrs = resource.attributes

    # Cache
    song = extract_song_cache_data(resource)
    if song:
        await queries.upsert_song_metadata([song])

    genres = ", ".join(attrs.get("genreNames", []))
    duration_ms = attrs.get("durationInMillis", 0) or 0
    duration = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}" if duration_ms else ""
    traits = ", ".join(attrs.get("audioTraits", []))
    editorial = attrs.get("editorialNotes", {})
    notes = ""
    if isinstance(editorial, dict):
        notes = editorial.get("standard", editorial.get("short", ""))

    lines = [
        f"## {attrs.get('name', 'Unknown')}",
        f"**Artist:** {attrs.get('artistName', 'Unknown')}",
        f"**Album:** {attrs.get('albumName', '')}",
        f"**Genres:** {genres}",
        f"**Duration:** {duration}",
        f"**Release Date:** {attrs.get('releaseDate', 'N/A')}",
        f"**ISRC:** {attrs.get('isrc', 'N/A')}",
        f"**Content Rating:** {attrs.get('contentRating', 'N/A')}",
        f"**Has Lyrics:** {attrs.get('hasLyrics', False)}",
    ]
    if traits:
        lines.append(f"**Audio Traits:** {traits}")
    if notes:
        lines.append(f"\n> {notes}")
    lines.append(f"\n*Catalog ID: `{resource.id}`*")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_lookup_artist(artist_id: str) -> str:
    """Get full details for an artist including top songs and similar artists.

    Args:
        artist_id: Apple Music catalog artist ID
    """
    client, queries = _ctx()
    resource = await client.get_artist(
        artist_id, views="top-songs,similar-artists"
    )
    attrs = resource.attributes

    # Cache artist
    artist_data = extract_artist_cache_data(resource)
    await queries.upsert_artist([artist_data])

    genres = ", ".join(attrs.get("genreNames", []))

    lines = [
        f"## {attrs.get('name', 'Unknown')}",
        f"**Genres:** {genres}",
        f"**URL:** {attrs.get('url', 'N/A')}",
    ]

    # Top songs from views
    top_songs = resource.views.get("top-songs", {})
    if isinstance(top_songs, dict) and top_songs.get("data"):
        lines.append("\n### Top Songs")
        cache_data = []
        for i, s in enumerate(top_songs["data"][:10], start=1):
            from musicmind.models import Resource as Res

            r = Res(**s)
            lines.append(format_song_md(r, index=i))
            song_cache = extract_song_cache_data(r)
            if song_cache:
                cache_data.append(song_cache)
        if cache_data:
            await queries.upsert_song_metadata(cache_data)

    # Similar artists from views
    similar = resource.views.get("similar-artists", {})
    if isinstance(similar, dict) and similar.get("data"):
        lines.append("\n### Similar Artists")
        for i, a in enumerate(similar["data"][:10], start=1):
            from musicmind.models import Resource as Res

            r = Res(**a)
            lines.append(format_artist_md(r, index=i))

    lines.append(f"\n*Artist ID: `{resource.id}`*")
    return "\n".join(lines)


@mcp.tool()
async def musicmind_lookup_album(album_id: str) -> str:
    """Get full details for an album including track listing.

    Args:
        album_id: Apple Music catalog album ID
    """
    client, queries = _ctx()
    resource = await client.get_album(album_id, include="tracks")
    attrs = resource.attributes

    genres = ", ".join(attrs.get("genreNames", []))
    editorial = attrs.get("editorialNotes", {})
    notes = ""
    if isinstance(editorial, dict):
        notes = editorial.get("standard", editorial.get("short", ""))

    lines = [
        f"## {attrs.get('name', 'Unknown')}",
        f"**Artist:** {attrs.get('artistName', 'Unknown')}",
        f"**Genres:** {genres}",
        f"**Tracks:** {attrs.get('trackCount', 0)}",
        f"**Release Date:** {attrs.get('releaseDate', 'N/A')}",
        f"**Is Single:** {attrs.get('isSingle', False)}",
    ]
    if notes:
        lines.append(f"\n> {notes}")

    # Track listing from relationships
    tracks_rel = resource.relationships.get("tracks", {})
    track_data = tracks_rel.get("data", []) if isinstance(tracks_rel, dict) else []
    if track_data:
        lines.append("\n### Tracks")
        cache_data = []
        for i, t in enumerate(track_data, start=1):
            from musicmind.models import Resource as Res

            r = Res(**t)
            lines.append(format_song_md(r, index=i))
            song_cache = extract_song_cache_data(r)
            if song_cache:
                cache_data.append(song_cache)
        if cache_data:
            await queries.upsert_song_metadata(cache_data)

    lines.append(f"\n*Album ID: `{resource.id}`*")
    return "\n".join(lines)


@mcp.tool()
async def musicmind_charts(
    chart_type: str = "songs",
    genre: str | None = None,
    limit: int = 25,
) -> str:
    """Get Apple Music charts (top songs, albums, or playlists).

    Args:
        chart_type: Type of chart: "songs", "albums", or "playlists"
        genre: Optional genre ID to filter charts
        limit: Number of chart entries (max 50)
    """
    client, queries = _ctx()
    result = await client.get_charts(types=chart_type, genre=genre, limit=min(limit, 50))

    chart_data = getattr(result, chart_type, [])
    if not chart_data:
        return f"No {chart_type} charts available."

    lines = []
    for chart in chart_data:
        chart_name = (
            chart.get("name", chart.get("chart", "Chart"))
            if isinstance(chart, dict)
            else "Chart"
        )
        lines.append(f"## {chart_name}")
        data = chart.get("data", []) if isinstance(chart, dict) else []
        for i, item in enumerate(data, start=1):
            from musicmind.models import Resource as Res

            r = Res(**item)
            if chart_type == "songs":
                lines.append(format_song_md(r, index=i))
                song_cache = extract_song_cache_data(r)
                if song_cache:
                    await queries.upsert_song_metadata([song_cache])
            elif chart_type == "albums":
                lines.append(format_album_md(r, index=i))
            else:
                lines.append(format_playlist_md(r, index=i))

    return "\n".join(lines)


@mcp.tool()
async def musicmind_activities() -> str:
    """Get mood and activity categories from Apple Music (e.g., Chill, Workout, Focus)."""
    client, _ = _ctx()
    result = await client.get_activities()

    if not result.data:
        return "No activities available."

    lines = ["## Activities & Moods"]
    for r in result.data:
        name = r.attributes.get("name", "Unknown")
        lines.append(f"- **{name}** (id: `{r.id}`)")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_genres() -> str:
    """Get the full genre listing for your storefront."""
    client, _ = _ctx()
    result = await client.get_genre_list()

    if not result.data:
        return "No genres available."

    lines = ["## Genres"]
    for r in result.data:
        name = r.attributes.get("name", "Unknown")
        lines.append(f"- {name} (id: `{r.id}`)")

    return "\n".join(lines)
