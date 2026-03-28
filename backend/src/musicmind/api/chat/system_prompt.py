"""Shared system prompt builder for all LLM providers.

Constructs the system prompt with user context (connected services, taste summary,
available tools) used by both ClaudeProvider and OpenAIProvider.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa

from musicmind.api.chat.tools import TOOL_DEFINITIONS
from musicmind.db.schema import service_connections, taste_profile_snapshots

logger = logging.getLogger(__name__)


async def build_system_prompt(engine: Any, user_id: str) -> str:
    """Build the system prompt with user context.

    Includes MusicMind identity, connected services, brief taste summary,
    and available tool descriptions.

    Args:
        engine: SQLAlchemy async engine.
        user_id: Current user's ID.

    Returns:
        Complete system prompt string.
    """
    parts = [
        "You are MusicMind, an AI music discovery assistant. You have access to "
        "the user's real listening data from Spotify and Apple Music. You can "
        "analyze their taste profile, find new music they'll love, explain why "
        "songs match their preferences, and adjust recommendations based on "
        "natural language. Be specific -- reference actual tracks, artists, "
        "genres, and scoring dimensions. Be conversational, not clinical.",
    ]

    # Connected services
    services = await _get_connected_services(engine, user_id)
    if services:
        svc_names = ", ".join(s.replace("_", " ").title() for s in services)
        parts.append(f"\nConnected services: {svc_names}.")
    else:
        parts.append("\nNo music services connected yet.")

    # Taste summary
    taste_summary = await _get_taste_summary(engine, user_id)
    if taste_summary:
        parts.append(f"\n{taste_summary}")

    # Available tools
    tool_names = [t["name"] for t in TOOL_DEFINITIONS]
    parts.append(
        f"\nAvailable tools: {', '.join(tool_names)}. "
        "Use these tools to access the user's music data and provide recommendations."
    )

    return "\n".join(parts)


async def _get_connected_services(engine: Any, user_id: str) -> list[str]:
    """Query connected services for the user.

    Args:
        engine: SQLAlchemy async engine.
        user_id: Current user's ID.

    Returns:
        List of service names.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(service_connections.c.service).where(
                service_connections.c.user_id == user_id,
            )
        )
        return [row.service for row in result.fetchall()]


async def _get_taste_summary(engine: Any, user_id: str) -> str | None:
    """Get a brief taste summary from the latest taste profile snapshot.

    Args:
        engine: SQLAlchemy async engine.
        user_id: Current user's ID.

    Returns:
        Summary string or None if no profile exists.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                taste_profile_snapshots.c.genre_vector,
                taste_profile_snapshots.c.top_artists,
            )
            .where(taste_profile_snapshots.c.user_id == user_id)
            .order_by(taste_profile_snapshots.c.computed_at.desc())
            .limit(1)
        )
        row = result.first()

    if not row:
        return None

    genre_vector = row.genre_vector
    if isinstance(genre_vector, str):
        genre_vector = json.loads(genre_vector)

    if not genre_vector:
        return None

    # Get top 3 genres
    sorted_genres = sorted(genre_vector.items(), key=lambda x: x[1], reverse=True)[:3]
    top_genres = [g[0] for g in sorted_genres]

    top_artists = row.top_artists
    if isinstance(top_artists, str):
        top_artists = json.loads(top_artists)

    parts = [f"User's top genres: {', '.join(top_genres)}."]
    if top_artists and isinstance(top_artists, list):
        artist_names = []
        for a in top_artists[:3]:
            if isinstance(a, dict):
                artist_names.append(a.get("name", str(a)))
            else:
                artist_names.append(str(a))
        if artist_names:
            parts.append(f"Top artists: {', '.join(artist_names)}.")

    return " ".join(parts)
