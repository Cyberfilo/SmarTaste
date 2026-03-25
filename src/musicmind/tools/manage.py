"""Library management MCP tools — create playlists, add tracks, rate songs."""

from __future__ import annotations

from musicmind.server import mcp


def _ctx():
    ctx = mcp.get_context()
    lc = ctx.request_context.lifespan_context
    return lc["client"], lc["queries"]


@mcp.tool()
async def musicmind_create_playlist(
    name: str,
    description: str = "",
    track_ids: list[str] | None = None,
) -> str:
    """Create a new playlist in your Apple Music library.

    Args:
        name: Playlist name
        description: Optional playlist description
        track_ids: Optional list of catalog song IDs to add initially
    """
    client, queries = _ctx()
    result = await client.create_playlist(name, description, track_ids)

    track_count = len(track_ids) if track_ids else 0
    playlist_id = result.id or "(pending)"

    lines = [
        f"## Playlist Created: {name}",
        f"**ID:** `{playlist_id}`",
    ]
    if description:
        lines.append(f"**Description:** {description}")
    if track_count:
        lines.append(f"**Tracks added:** {track_count}")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_add_to_playlist(
    playlist_id: str,
    track_ids: list[str],
) -> str:
    """Add tracks to an existing library playlist.

    Args:
        playlist_id: The library playlist ID
        track_ids: List of catalog song IDs to add
    """
    client, _ = _ctx()
    await client.add_tracks_to_playlist(playlist_id, track_ids)
    return f"Added {len(track_ids)} track(s) to playlist `{playlist_id}`."


@mcp.tool()
async def musicmind_add_to_library(song_ids: list[str]) -> str:
    """Add catalog songs to your Apple Music library.

    Args:
        song_ids: List of catalog song IDs to add to library
    """
    client, _ = _ctx()
    await client.add_to_library(song_ids)
    return f"Added {len(song_ids)} song(s) to your library."


@mcp.tool()
async def musicmind_rate_song(
    song_id: str,
    rating: str,
) -> str:
    """Rate a song in your Apple Music library.

    Args:
        song_id: Catalog song ID
        rating: "love" (thumbs up), "dislike" (thumbs down), or "neutral" (remove rating)
    """
    client, queries = _ctx()

    if rating == "love":
        await client.rate_song(song_id, 1)
        # Update cache
        cached = await queries.get_cached_song(song_id)
        if cached:
            await queries.upsert_song_metadata([{**cached, "user_rating": 1}])
        return f"Loved song `{song_id}`"
    elif rating == "dislike":
        await client.rate_song(song_id, -1)
        cached = await queries.get_cached_song(song_id)
        if cached:
            await queries.upsert_song_metadata([{**cached, "user_rating": -1}])
        return f"Disliked song `{song_id}`"
    elif rating == "neutral":
        await client.delete_rating(song_id)
        cached = await queries.get_cached_song(song_id)
        if cached:
            await queries.upsert_song_metadata([{**cached, "user_rating": None}])
        return f"Removed rating for song `{song_id}`"
    else:
        return f"Invalid rating '{rating}'. Use 'love', 'dislike', or 'neutral'."
