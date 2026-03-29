"""Session API endpoints — track played songs for contextual recommendations.

POST /api/session/played — record a played song
GET /api/session/context — get current session context vector
DELETE /api/session — clear session
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from musicmind.auth.dependencies import get_current_user
from musicmind.engine.session import session_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session", tags=["session"])


class PlayedRequest(BaseModel):
    """Record a played song in the current session."""

    catalog_id: str = Field(..., description="Track catalog ID")
    embedding: list[float] = Field(
        default_factory=list,
        description="128-dim audio embedding (optional, looked up if missing)",
    )


class SessionContextResponse(BaseModel):
    """Current session context information."""

    has_session: bool = False
    track_count: int = 0
    context_vector: list[float] | None = None
    played_ids: list[str] = Field(default_factory=list)


@router.post("/played", status_code=status.HTTP_200_OK)
async def record_played(
    request: Request,
    body: PlayedRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Record a played song in the user's listening session.

    If no embedding is provided, attempts to look up from audio_embeddings cache.
    """
    user_id = current_user["user_id"]
    embedding = body.embedding

    # Look up embedding from cache if not provided
    if not embedding:
        try:
            from musicmind.engine.audio.cache import get_cached_embedding
            cached = await get_cached_embedding(
                request.app.state.engine,
                catalog_id=body.catalog_id,
                user_id=user_id,
            )
            if cached and cached.vector:
                embedding = cached.vector
        except Exception:
            pass  # Embedding lookup is best-effort

    session_manager.add_played(user_id, body.catalog_id, embedding)
    session = session_manager.get(user_id)
    track_count = len(session.entries) if session else 0

    logger.info(
        "Recorded played track %s for user %s (session: %d tracks)",
        body.catalog_id, user_id, track_count,
    )
    return {"recorded": True, "session_tracks": track_count}


@router.get("/context")
async def get_session_context(
    current_user: dict = Depends(get_current_user),
) -> SessionContextResponse:
    """Get the current session context for the authenticated user."""
    user_id = current_user["user_id"]
    session = session_manager.get(user_id)

    if session is None:
        return SessionContextResponse()

    return SessionContextResponse(
        has_session=True,
        track_count=len(session.entries),
        context_vector=session.get_context_vector(),
        played_ids=session.get_played_ids(),
    )


@router.delete("", status_code=status.HTTP_200_OK)
async def clear_session(
    current_user: dict = Depends(get_current_user),
) -> dict[str, str]:
    """Clear the user's current listening session."""
    user_id = current_user["user_id"]
    if user_id in session_manager._sessions:
        del session_manager._sessions[user_id]
    return {"message": "Session cleared"}
