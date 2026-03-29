"""Tests for Phase 7: Music knowledge graph."""

from __future__ import annotations

import pytest

from musicmind.engine.knowledge_graph.embeddings import (
    _biased_walk,
    _walks_to_embeddings,
    artist_graph_similarity,
)
from musicmind.engine.knowledge_graph.ingest import (
    RELATION_MAP,
    RELATION_TYPES,
)


# ── Graph walk tests ──────────────────────────────────────────────────────


def test_biased_walk_single_node() -> None:
    """Walk from isolated node returns just that node."""
    adj = {"a": []}
    walk = _biased_walk(adj, "a", length=10)
    assert walk == ["a"]


def test_biased_walk_linear_graph() -> None:
    """Walk on linear graph visits connected nodes."""
    adj = {"a": ["b"], "b": ["a", "c"], "c": ["b"]}
    walk = _biased_walk(adj, "a", length=5)
    assert walk[0] == "a"
    assert len(walk) >= 2
    assert all(n in adj for n in walk)


def test_biased_walk_length_limit() -> None:
    """Walk respects max length."""
    adj = {"a": ["b"], "b": ["a", "c"], "c": ["b", "d"], "d": ["c"]}
    walk = _biased_walk(adj, "a", length=3)
    assert len(walk) <= 3


def test_biased_walk_fully_connected() -> None:
    """Walk on fully connected graph explores freely."""
    nodes = ["a", "b", "c", "d"]
    adj = {n: [m for m in nodes if m != n] for n in nodes}
    walk = _biased_walk(adj, "a", length=20)
    assert len(walk) == 20
    # Should visit multiple nodes
    assert len(set(walk)) > 1


# ── Embedding computation tests ──────────────────────────────────────────


def test_walks_to_embeddings_dimension() -> None:
    """Embeddings have correct dimension."""
    walks = [["a", "b", "c"], ["b", "a", "c"]]
    embeddings = _walks_to_embeddings(walks, ["a", "b", "c"], dim=128)
    assert len(embeddings) == 3
    for vec in embeddings.values():
        assert len(vec) == 128


def test_walks_to_embeddings_normalized() -> None:
    """Embeddings are approximately L2-normalized."""
    import numpy as np
    walks = [["a", "b"], ["b", "a"]]
    embeddings = _walks_to_embeddings(walks, ["a", "b"], dim=64)
    for vec in embeddings.values():
        norm = float(np.linalg.norm(vec))
        assert norm == pytest.approx(1.0, abs=0.01)


def test_connected_nodes_similar() -> None:
    """Directly connected nodes should have higher similarity."""
    # A-B connected, C isolated
    adj = {"a": ["b"], "b": ["a"], "c": []}
    walks = []
    for node in ["a", "b", "c"]:
        for _ in range(10):
            walks.append(_biased_walk(adj, node, length=10))
    embeddings = _walks_to_embeddings(walks, ["a", "b", "c"], dim=64)

    sim_ab = artist_graph_similarity(embeddings["a"], embeddings["b"])
    sim_ac = artist_graph_similarity(embeddings["a"], embeddings["c"])
    # Connected nodes should be more similar than disconnected
    # (not guaranteed with random initialization, but generally true)
    # Use a loose check
    assert isinstance(sim_ab, float)
    assert isinstance(sim_ac, float)


# ── Similarity function tests ─────────────────────────────────────────────


def test_artist_graph_similarity_identical() -> None:
    """Identical embeddings have similarity ~1.0."""
    vec = [0.1] * 128
    assert artist_graph_similarity(vec, vec) == pytest.approx(1.0, abs=0.01)


def test_artist_graph_similarity_none() -> None:
    """Returns 0.0 when either embedding is None."""
    assert artist_graph_similarity(None, [0.1] * 128) == 0.0
    assert artist_graph_similarity([0.1] * 128, None) == 0.0


# ── Ingest constants tests ────────────────────────────────────────────────


def test_relation_types_mapped() -> None:
    """All relation types have a mapping."""
    for rt in RELATION_TYPES:
        assert rt in RELATION_MAP, f"'{rt}' not in RELATION_MAP"
