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

    return "\n".join(lines)
