"""MusicMind MCP server — FastMCP entry point."""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from musicmind import __version__
from musicmind.auth import AuthManager
from musicmind.client import AppleMusicClient
from musicmind.config import DB_FILE, load_config
from musicmind.db.manager import DatabaseManager
from musicmind.db.queries import QueryExecutor

# All logging to stderr (MCP stdio transport requirement)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("musicmind")


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Server lifespan: initialize config, auth, DB, and API client on startup."""
    logger.info("MusicMind MCP v%s starting up", __version__)

    # Initialize database (always — even without API config)
    db_manager = DatabaseManager(DB_FILE)
    await db_manager.initialize()
    queries = QueryExecutor(db_manager.engine)

    try:
        config = load_config()
        auth = AuthManager(config)
        _ = auth.developer_token
        logger.info("Developer token OK, storefront=%s", config.storefront)
    except FileNotFoundError as e:
        logger.warning("Config not loaded: %s", e)
        logger.warning("Server starting in limited mode — run setup first")
        yield {"config": None, "auth": None, "client": None, "db": db_manager, "queries": queries}
        await db_manager.close()
        return
    except Exception as e:
        logger.error("Startup error: %s", e)
        yield {"config": None, "auth": None, "client": None, "db": db_manager, "queries": queries}
        await db_manager.close()
        return

    client = AppleMusicClient(auth, storefront=config.storefront)
    async with client:
        yield {
            "config": config,
            "auth": auth,
            "client": client,
            "db": db_manager,
            "queries": queries,
        }

    await db_manager.close()
    logger.info("MusicMind MCP shutting down")


mcp = FastMCP(
    "musicmind_mcp",
    lifespan=lifespan,
)


# Import tool modules to register them with the MCP server
import musicmind.tools.catalog  # noqa: F401, E402
import musicmind.tools.library  # noqa: F401, E402
import musicmind.tools.manage  # noqa: F401, E402
import musicmind.tools.playback  # noqa: F401, E402
import musicmind.tools.recommend  # noqa: F401, E402
import musicmind.tools.taste  # noqa: F401, E402


@mcp.tool()
async def musicmind_health() -> str:
    """Check MusicMind server status, auth, and configuration.

    Returns server version, auth status, and configured storefront.
    Use this to verify the server is running and properly configured.
    """
    ctx = mcp.get_context()
    config = ctx.request_context.lifespan_context.get("config")
    auth = ctx.request_context.lifespan_context.get("auth")

    has_dev = False
    has_user = False
    storefront = "unknown"

    if config and auth:
        try:
            _ = auth.developer_token
            has_dev = True
        except Exception:
            pass
        has_user = config.has_user_token
        storefront = config.storefront

    status = "ready" if (has_dev and has_user) else "limited"

    lines = [
        f"## MusicMind MCP v{__version__}",
        f"**Status:** {status}",
        f"**Developer Token:** {'OK' if has_dev else 'MISSING'}",
        f"**Music User Token:** {'OK' if has_user else 'MISSING — run setup'}",
        f"**Storefront:** {storefront}",
    ]

    if not has_dev:
        lines.append("\n> Configure your Apple Developer credentials in "
                      "`~/.config/musicmind/config.json`")
    if not has_user:
        lines.append(
            "\n> Run `uv run python -m musicmind.setup` to authorize your Apple Music account"
        )

    # Add cache stats if DB available
    lc_queries = ctx.request_context.lifespan_context.get("queries")
    if lc_queries:
        try:
            stats = await lc_queries.get_cache_stats()
            lines.append(f"\n**Cache:** {stats['songs_cached']} songs, "
                         f"{stats['artists_cached']} artists, "
                         f"{stats['listening_history_entries']} history entries")
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
async def musicmind_help() -> str:
    """Get help with MusicMind — lists all tools with descriptions and example prompts.

    Start here if you're new to MusicMind.
    """
    return """## MusicMind MCP — Tool Guide

### Getting Started
1. Run `musicmind_health` to check your setup
2. Run `musicmind_library_songs` to cache your library
3. Run `musicmind_recently_played` to cache listening history
4. Run `musicmind_taste_profile` to build your taste profile
5. Run `musicmind_discover` or `musicmind_smart_playlist` for recommendations!

### Library Browsing
- `musicmind_library_songs(limit, offset)` — Browse your library songs
- `musicmind_library_albums(limit, offset)` — Browse your library albums
- `musicmind_library_artists(limit, offset)` — Browse your library artists
- `musicmind_library_playlists()` — List all your playlists
- `musicmind_playlist_tracks(playlist_id)` — Tracks in a playlist
- `musicmind_search_library(query)` — Search your library

### Catalog Search & Lookup
- `musicmind_search(query, types)` — Search Apple Music catalog
- `musicmind_lookup_song(song_id)` — Full song details
- `musicmind_lookup_artist(artist_id)` — Artist + top songs + similar artists
- `musicmind_lookup_album(album_id)` — Album + track listing
- `musicmind_charts(chart_type, genre)` — Top charts
- `musicmind_activities()` — Mood/activity categories
- `musicmind_genres()` — All available genres

### Listening History
- `musicmind_recently_played(limit)` — Recent tracks (auto-cached)
- `musicmind_heavy_rotation()` — Your heavy rotation
- `musicmind_apple_recommendations()` — Apple's personalized picks

### Library Management
- `musicmind_create_playlist(name, description, track_ids)` — Create a playlist
- `musicmind_add_to_playlist(playlist_id, track_ids)` — Add tracks to playlist
- `musicmind_add_to_library(song_ids)` — Add songs to library
- `musicmind_rate_song(song_id, rating)` — Love, dislike, or neutral

### Taste Analysis
- `musicmind_taste_profile()` — Build/view your taste profile
- `musicmind_taste_compare(song_id)` — How well does a song match your taste?
- `musicmind_listening_stats()` — Aggregate listening statistics
- `musicmind_why_this_song(song_id)` — Explain why a song matches/doesn't

### Smart Recommendations
- `musicmind_discover(count, strategy)` — Discover new songs
- `musicmind_smart_playlist(name, vibe, count)` — Create a vibe-based playlist
- `musicmind_refresh_cache()` — Refresh data and rebuild profile

### Example Prompts
- "Show me my top genres and favorite artists"
- "Find me new drill tracks I haven't heard"
- "Create a playlist called 'Late Night Drive' with underground hip-hop vibes"
- "Why would I like this song?" (after looking up a song)
- "Compare my taste to this album's tracklist"
- "What have I been listening to lately?"
"""
