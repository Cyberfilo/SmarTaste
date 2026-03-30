"""Node2Vec graph embeddings — 128-dim artist vectors from the knowledge graph.

Implements simplified Node2Vec: biased random walks on the artist relationship
graph, then averages co-occurrence context windows to produce embeddings.

For a small graph (~100-500 nodes), this is computed on-demand without
requiring Word2Vec. The embedding for each artist is the average of its
walk neighborhoods.
"""

from __future__ import annotations

import json
import logging
import random
from typing import Any

import numpy as np
import sqlalchemy as sa

from musicmind.db.schema import kg_artists, kg_relationships

logger = logging.getLogger(__name__)

# Node2Vec hyperparameters
WALK_LENGTH = 80
NUM_WALKS = 10
EMBEDDING_DIM = 128
P = 1.0  # return parameter (1.0 = unbiased)
Q = 0.5  # in-out parameter (< 1.0 = BFS-like, favors local exploration)
WINDOW_SIZE = 5


async def load_graph(engine) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Load the knowledge graph as adjacency list.

    Returns:
        Tuple of (adjacency dict, mbid-to-name dict).
    """
    adj: dict[str, list[str]] = {}
    names: dict[str, str] = {}

    async with engine.begin() as conn:
        # Load artists
        result = await conn.execute(sa.select(kg_artists.c.mbid, kg_artists.c.name))
        for row in result.fetchall():
            adj.setdefault(row.mbid, [])
            names[row.mbid] = row.name

        # Load relationships (bidirectional)
        result = await conn.execute(
            sa.select(
                kg_relationships.c.source_mbid,
                kg_relationships.c.target_mbid,
            )
        )
        for row in result.fetchall():
            adj.setdefault(row.source_mbid, []).append(row.target_mbid)
            adj.setdefault(row.target_mbid, []).append(row.source_mbid)

    return adj, names


def _biased_walk(
    adj: dict[str, list[str]],
    start: str,
    length: int = WALK_LENGTH,
    p: float = P,
    q: float = Q,
) -> list[str]:
    """Perform a single biased random walk from start node.

    Uses Node2Vec-style biased sampling based on previous node.
    """
    walk = [start]
    if start not in adj or not adj[start]:
        return walk

    # First step: uniform random
    walk.append(random.choice(adj[start]))

    for _ in range(length - 2):
        cur = walk[-1]
        prev = walk[-2]
        neighbors = adj.get(cur, [])
        if not neighbors:
            break

        # Compute unnormalized transition probabilities
        weights: list[float] = []
        for nbr in neighbors:
            if nbr == prev:
                weights.append(1.0 / p)  # return to previous
            elif nbr in adj.get(prev, []):
                weights.append(1.0)  # BFS-like (neighbor of prev)
            else:
                weights.append(1.0 / q)  # DFS-like (explore)

        total = sum(weights)
        if total == 0:
            break
        probs = [w / total for w in weights]
        next_node = random.choices(neighbors, weights=probs, k=1)[0]
        walk.append(next_node)

    return walk


def _walks_to_embeddings(
    walks: list[list[str]],
    all_nodes: list[str],
    dim: int = EMBEDDING_DIM,
    window: int = WINDOW_SIZE,
) -> dict[str, list[float]]:
    """Convert random walks to embeddings via co-occurrence averaging.

    For each node, builds a context representation by averaging random
    vectors assigned to co-occurring nodes within a window. This is a
    simplified alternative to full Word2Vec that works well for small
    graphs.
    """
    rng = np.random.default_rng(42)

    # Assign random base vector to each node
    node_vectors: dict[str, np.ndarray] = {
        node: rng.normal(0, 0.1, dim).astype(np.float32) for node in all_nodes
    }

    # Build co-occurrence context for each node
    context_sums: dict[str, np.ndarray] = {
        node: np.zeros(dim, dtype=np.float32) for node in all_nodes
    }
    context_counts: dict[str, int] = {node: 0 for node in all_nodes}

    for walk in walks:
        for i, node in enumerate(walk):
            start = max(0, i - window)
            end = min(len(walk), i + window + 1)
            for j in range(start, end):
                if j != i:
                    context_sums[node] += node_vectors[walk[j]]
                    context_counts[node] += 1

    # Compute embedding = base_vector + averaged context
    embeddings: dict[str, list[float]] = {}
    for node in all_nodes:
        if context_counts[node] > 0:
            ctx_avg = context_sums[node] / context_counts[node]
            final = node_vectors[node] + ctx_avg
        else:
            final = node_vectors[node]
        # L2 normalize
        norm = np.linalg.norm(final)
        if norm > 0:
            final = final / norm
        embeddings[node] = [round(float(x), 6) for x in final]

    return embeddings


async def compute_embeddings(engine) -> dict[str, list[float]]:
    """Compute Node2Vec embeddings for all artists in the knowledge graph.

    Returns:
        Dict mapping MBID to 128-dim embedding vector.
    """
    adj, names = await load_graph(engine)
    all_nodes = list(adj.keys())

    if not all_nodes:
        return {}

    # Generate random walks
    walks: list[list[str]] = []
    for node in all_nodes:
        for _ in range(NUM_WALKS):
            walk = _biased_walk(adj, node)
            walks.append(walk)

    # Convert to embeddings
    embeddings = _walks_to_embeddings(walks, all_nodes)

    logger.info(
        "Computed Node2Vec embeddings for %d artists (%d walks)",
        len(all_nodes), len(walks),
    )
    return embeddings


async def store_embeddings(
    engine,
    embeddings: dict[str, list[float]],
) -> None:
    """Store computed embeddings back to kg_artists table."""
    async with engine.begin() as conn:
        for mbid, embedding in embeddings.items():
            await conn.execute(
                kg_artists.update()
                .where(kg_artists.c.mbid == mbid)
                .values(embedding=json.dumps(embedding))
            )


async def get_artist_embedding(
    engine,
    *,
    artist_name: str,
) -> list[float] | None:
    """Look up a pre-computed embedding by artist name.

    Falls back to fuzzy name matching (case-insensitive).
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(kg_artists.c.embedding).where(
                sa.func.lower(kg_artists.c.name) == artist_name.lower()
            )
        )
        row = result.first()

    if row is None or row.embedding is None:
        return None

    embedding = row.embedding
    if isinstance(embedding, str):
        embedding = json.loads(embedding)
    return embedding if isinstance(embedding, list) else None


def artist_graph_similarity(
    embedding_a: list[float] | None,
    embedding_b: list[float] | None,
) -> float:
    """Cosine similarity between two artist graph embeddings.

    Returns 0.0 when either is None (no graph data).
    """
    if not embedding_a or not embedding_b:
        return 0.0
    if len(embedding_a) != len(embedding_b):
        return 0.0

    a = np.array(embedding_a)
    b = np.array(embedding_b)
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))
