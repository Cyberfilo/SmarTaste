"""Sequential session model — rolling context vector from recent plays.

Maintains an exponentially-weighted average of the last N song embeddings
to capture "what the user is in the mood for right now." Feeds a
session_similarity scoring dimension.

Session state is in-memory with a configurable TTL (default 2 hours).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class SessionEntry:
    """A single played song in the session."""

    catalog_id: str
    embedding: list[float]
    timestamp: float = field(default_factory=time.time)


@dataclass
class ListeningSession:
    """Rolling session with exponentially-weighted context vector.

    Keeps the last `max_entries` songs and computes a context vector
    using exponential weighting (α=0.85, most recent = highest weight).
    """

    max_entries: int = 20
    alpha: float = 0.85
    ttl_seconds: float = 7200.0  # 2 hours

    entries: deque[SessionEntry] = field(default_factory=deque)
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        """Check if the session has exceeded its TTL."""
        return (time.time() - self.created_at) > self.ttl_seconds

    def add_played(self, catalog_id: str, embedding: list[float]) -> None:
        """Record a played song with its embedding.

        Args:
            catalog_id: Track catalog ID.
            embedding: Audio embedding vector (128-dim).
        """
        if not embedding:
            return

        self.entries.append(SessionEntry(
            catalog_id=catalog_id,
            embedding=embedding,
        ))

        # Trim to max_entries
        while len(self.entries) > self.max_entries:
            self.entries.popleft()

    def get_context_vector(self) -> list[float] | None:
        """Compute the exponentially-weighted context vector.

        Most recent songs get the highest weight (α^0), oldest get α^(n-1).
        Returns None if the session is empty.

        Returns:
            128-dim context vector or None.
        """
        if not self.entries:
            return None

        n = len(self.entries)
        # Most recent entry is last in deque
        vectors = []
        weights = []
        for i, entry in enumerate(self.entries):
            if entry.embedding:
                age = n - 1 - i  # 0 for most recent
                vectors.append(entry.embedding)
                weights.append(self.alpha ** age)

        if not vectors:
            return None

        arr = np.array(vectors)
        w = np.array(weights)
        w = w / w.sum()  # normalize

        # Weighted average
        context = np.average(arr, axis=0, weights=w)
        return [round(float(x), 6) for x in context]

    def get_played_ids(self) -> list[str]:
        """Get catalog IDs of songs in the session (for anti-repeat)."""
        return [e.catalog_id for e in self.entries]


class SessionManager:
    """In-memory session store with TTL-based expiration.

    Thread-safe for single-process async use (no concurrent writes from
    multiple threads). For multi-process deployments, sessions are
    per-process and don't share state — acceptable for a small user base.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ListeningSession] = {}

    def get_or_create(self, user_id: str) -> ListeningSession:
        """Get existing session or create a new one.

        Expired sessions are replaced with fresh ones.
        """
        session = self._sessions.get(user_id)
        if session is None or session.is_expired():
            session = ListeningSession()
            self._sessions[user_id] = session
        return session

    def get(self, user_id: str) -> ListeningSession | None:
        """Get session if it exists and isn't expired."""
        session = self._sessions.get(user_id)
        if session is None:
            return None
        if session.is_expired():
            del self._sessions[user_id]
            return None
        return session

    def add_played(
        self,
        user_id: str,
        catalog_id: str,
        embedding: list[float],
    ) -> None:
        """Record a played song in the user's session."""
        session = self.get_or_create(user_id)
        session.add_played(catalog_id, embedding)

    def get_context_vector(self, user_id: str) -> list[float] | None:
        """Get the session context vector for a user."""
        session = self.get(user_id)
        if session is None:
            return None
        return session.get_context_vector()

    def cleanup_expired(self) -> int:
        """Remove all expired sessions. Returns count removed."""
        expired = [
            uid for uid, s in self._sessions.items() if s.is_expired()
        ]
        for uid in expired:
            del self._sessions[uid]
        return len(expired)


def session_similarity(
    candidate_embedding: list[float] | None,
    context_vector: list[float] | None,
) -> float:
    """Cosine similarity between a candidate and the session context.

    Returns 0.5 (neutral) when either vector is unavailable, so the
    session dimension has no effect when there's no active session.
    """
    if not candidate_embedding or not context_vector:
        return 0.5
    if len(candidate_embedding) != len(context_vector):
        return 0.5

    a = np.array(candidate_embedding)
    b = np.array(context_vector)
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0 or norm_b == 0:
        return 0.5
    return float(np.dot(a, b) / (norm_a * norm_b))


# Global session manager instance
session_manager = SessionManager()
