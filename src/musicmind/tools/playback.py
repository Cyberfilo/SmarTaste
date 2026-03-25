"""Playback history MCP tools — recently played, heavy rotation."""

from __future__ import annotations

from datetime import UTC, datetime

from musicmind.server import mcp
from musicmind.tools.helpers import extract_song_cache_data, format_song_md


def _ctx():
    ctx = mcp.get_context()
    lc = ctx.request_context.lifespan_context
    return lc["client"], lc["queries"]


@mcp.tool()
async def musicmind_recently_played(limit: int = 30) -> str:
    """Get recently played tracks. Automatically caches to listening history for taste analysis.

    Args:
        limit: Number of recent tracks (max 50, fetched in pages of 10)
    """
    client, queries = _ctx()
    limit = min(limit, 50)

    all_tracks = []
    offset = 0
    while len(all_tracks) < limit:
        batch_size = min(10, limit - len(all_tracks))
        result = await client.get_recently_played_tracks(limit=batch_size, offset=offset)
        all_tracks.extend(result.data)
        if not result.has_more:
            break
        offset += batch_size

    if not all_tracks:
        return "No recently played tracks found."

    # Cache to listening history + song metadata
    now = datetime.now(tz=UTC)
    history_records = []
    cache_data = []

    for i, r in enumerate(all_tracks):
        attrs = r.attributes
        history_records.append({
            "song_id": r.id,
            "song_name": attrs.get("name", ""),
            "artist_name": attrs.get("artistName", ""),
            "album_name": attrs.get("albumName", ""),
            "genre_names": attrs.get("genreNames", []),
            "duration_ms": attrs.get("durationInMillis"),
            "observed_at": now,
            "position_in_recent": i + 1,
            "source": "recently_played",
        })
        song = extract_song_cache_data(r)
        if song:
            cache_data.append(song)

    await queries.insert_listening_history(history_records)
    if cache_data:
        await queries.upsert_song_metadata(cache_data)

    lines = [f"## Recently Played ({len(all_tracks)} tracks)"]
    for i, r in enumerate(all_tracks, start=1):
        lines.append(format_song_md(r, index=i))

    lines.append(f"\n*{len(history_records)} entries cached to listening history*")
    return "\n".join(lines)


@mcp.tool()
async def musicmind_heavy_rotation() -> str:
    """Get your heavy rotation — content you've been listening to a lot recently."""
    client, queries = _ctx()
    result = await client.get_heavy_rotation(limit=25)

    if not result.data:
        return "No heavy rotation data available (this can be empty for light listeners)."

    lines = ["## Heavy Rotation"]
    for i, r in enumerate(result.data, start=1):
        attrs = r.attributes
        name = attrs.get("name", "Unknown")
        rtype = r.type.replace("-", " ").title()
        lines.append(f"{i}. **{name}** ({rtype}, id: `{r.id}`)")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_apple_recommendations() -> str:
    """Get Apple Music's personalized recommendations for you."""
    client, _ = _ctx()
    result = await client.get_recommendations()

    if not result.data:
        return "No recommendations available."

    lines = ["## Apple Music Recommendations"]
    for i, r in enumerate(result.data, start=1):
        attrs = r.attributes
        name = attrs.get("name", attrs.get("title", "Recommendation"))
        rtype = r.type.replace("-", " ").title()
        lines.append(f"{i}. **{name}** ({rtype})")

    return "\n".join(lines)
