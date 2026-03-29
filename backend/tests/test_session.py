"""Tests for Phase 6: Sequential session model."""

from __future__ import annotations

import time

import pytest

from musicmind.engine.session import (
    ListeningSession,
    SessionManager,
    session_similarity,
)


# ── ListeningSession tests ────────────────────────────────────────────────


def test_empty_session_context() -> None:
    """Empty session returns None context vector."""
    session = ListeningSession()
    assert session.get_context_vector() is None
    assert session.get_played_ids() == []


def test_add_played_single() -> None:
    """Adding a single song creates context equal to that embedding."""
    session = ListeningSession()
    embedding = [0.5] * 128
    session.add_played("track1", embedding)
    ctx = session.get_context_vector()
    assert ctx is not None
    assert len(ctx) == 128
    assert ctx[0] == pytest.approx(0.5, abs=0.001)


def test_add_played_multiple_recency_bias() -> None:
    """Most recent song has highest weight in context."""
    session = ListeningSession(alpha=0.5)
    session.add_played("old", [0.0] * 128)
    session.add_played("new", [1.0] * 128)
    ctx = session.get_context_vector()
    assert ctx is not None
    # With alpha=0.5, recent gets weight 1.0, old gets weight 0.5
    # Weighted avg = (0.0*0.5 + 1.0*1.0) / 1.5 ≈ 0.667
    assert ctx[0] > 0.5


def test_max_entries_trimmed() -> None:
    """Session trims oldest entries when max_entries exceeded."""
    session = ListeningSession(max_entries=3)
    for i in range(5):
        session.add_played(f"track{i}", [float(i)] * 128)
    assert len(session.entries) == 3
    assert session.entries[0].catalog_id == "track2"


def test_session_expiration() -> None:
    """Expired session is detected."""
    session = ListeningSession(ttl_seconds=0.01)
    session.add_played("track1", [0.1] * 128)
    time.sleep(0.02)
    assert session.is_expired()


def test_played_ids() -> None:
    """get_played_ids returns all catalog IDs in order."""
    session = ListeningSession()
    session.add_played("a", [0.1] * 128)
    session.add_played("b", [0.2] * 128)
    assert session.get_played_ids() == ["a", "b"]


def test_empty_embedding_ignored() -> None:
    """Empty embedding is not added to session."""
    session = ListeningSession()
    session.add_played("track1", [])
    assert len(session.entries) == 0


# ── SessionManager tests ──────────────────────────────────────────────────


def test_manager_get_or_create() -> None:
    """get_or_create returns consistent session for same user."""
    mgr = SessionManager()
    s1 = mgr.get_or_create("user1")
    s2 = mgr.get_or_create("user1")
    assert s1 is s2


def test_manager_different_users() -> None:
    """Different users get different sessions."""
    mgr = SessionManager()
    s1 = mgr.get_or_create("user1")
    s2 = mgr.get_or_create("user2")
    assert s1 is not s2


def test_manager_expired_replaced() -> None:
    """Expired session is replaced with new one."""
    mgr = SessionManager()
    s1 = mgr.get_or_create("user1")
    s1.ttl_seconds = 0.01
    s1.created_at = time.time() - 1.0
    s2 = mgr.get_or_create("user1")
    assert s1 is not s2


def test_manager_cleanup() -> None:
    """cleanup_expired removes expired sessions."""
    mgr = SessionManager()
    mgr.get_or_create("user1")
    mgr._sessions["user1"].ttl_seconds = 0.01
    mgr._sessions["user1"].created_at = time.time() - 1.0
    removed = mgr.cleanup_expired()
    assert removed == 1
    assert mgr.get("user1") is None


# ── session_similarity tests ──────────────────────────────────────────────


def test_session_similarity_identical() -> None:
    """Identical vectors have similarity ~1.0."""
    vec = [0.1 * i for i in range(128)]
    assert session_similarity(vec, vec) == pytest.approx(1.0, abs=0.001)


def test_session_similarity_none() -> None:
    """Returns 0.5 when either vector is None."""
    assert session_similarity(None, [0.1] * 128) == 0.5
    assert session_similarity([0.1] * 128, None) == 0.5


def test_session_similarity_different() -> None:
    """Different vectors have lower similarity."""
    a = [1.0 if i < 64 else 0.0 for i in range(128)]
    b = [0.0 if i < 64 else 1.0 for i in range(128)]
    sim = session_similarity(a, b)
    assert sim < 0.1
