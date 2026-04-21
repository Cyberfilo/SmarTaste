"""Shared cobweb candidate ranking.

Used by both worker._build_user_cobweb and indexer._suggest_and_enrich_artists
so suggested artists follow the same selection logic everywhere.

Core idea: accumulate co-occurrence evidence (sum, not max), dampen with log1p
to prevent super-collaborators from dominating, weight each contribution by the
primary artist's affinity so feats on top-tier tracks count more than feats on
tail tracks.
"""
from __future__ import annotations

import math
from typing import Any

from musicmind.engine.profile import parse_artists

# EffNet embedding dimensionality (Essentia model output).
# Used by the worker to validate embeddings before populating tracks for
# the centroid-similarity prefilter. The pure helper below intentionally
# accepts smaller vectors so it remains test-friendly.
EFFNET_EMBEDDING_DIM = 1280


def rank_cobweb_candidates(
    *,
    library_rows: list[dict[str, Any]],
    library_artist_names: set[str],  # lowercased
    existing_cobweb_names: set[str],  # lowercased
    max_total: int | None = None,
) -> list[tuple[str, float]]:
    """Rank featured-artist candidates for the cobweb.

    Args:
        library_rows: One dict per library track with keys:
            - "artist_name": raw artist string (may contain feat/ft/featuring)
            - "primary_affinity": float in [0, 1] — the primary artist's affinity
              score (from build_artist_affinity). Use 0.1 as fallback for unknown.
        library_artist_names: Lowercased names already in the user's library (excluded).
        existing_cobweb_names: Lowercased names already in the cobweb (excluded).
        max_total: Optional hard cap on returned candidates. If None, cap is
            the number of unique candidates (feat density).

    Returns:
        (name, priority) tuples sorted by priority descending. Priority =
        log1p(sum_i (weight_i * 2.0 * primary_affinity_i)).
    """
    raw: dict[str, float] = {}
    canonical_name: dict[str, str] = {}

    for row in library_rows:
        raw_name = row.get("artist_name") or ""
        if not raw_name:
            continue
        primary_affinity = float(row.get("primary_affinity", 0.1))
        parsed = parse_artists(raw_name)
        if not parsed:
            continue
        primary_lowers = {n.lower() for n, w in parsed if w == 1.0}
        for name, weight in parsed:
            key = name.strip().lower()
            if not key or len(key) <= 1:
                continue
            if key in primary_lowers:
                continue
            if key in library_artist_names or key in existing_cobweb_names:
                continue
            contribution = weight * 2.0 * max(0.05, primary_affinity)
            raw[key] = raw.get(key, 0.0) + contribution
            canonical_name.setdefault(key, name.strip())

    if not raw:
        return []

    priorities: list[tuple[str, float]] = [
        (canonical_name[k], math.log1p(v))
        for k, v in raw.items()
    ]
    priorities.sort(key=lambda x: x[1], reverse=True)

    density_cap = len(priorities)
    effective_cap = (
        min(max_total, density_cap) if max_total is not None else density_cap
    )
    return priorities[:effective_cap]


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0.0 for empty or mismatched inputs."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def prefilter_by_centroid_similarity(
    *,
    tracks: list[dict[str, Any]],
    centroid: list[float] | None,
    keep_fraction: float = 0.7,
) -> list[dict[str, Any]]:
    """Keep the top keep_fraction of tracks by embedding cosine to centroid.

    Tracks without an embedding are kept (we can't rank them yet; enrichment
    is the only way to learn their embedding). If the centroid is None
    (cold-start user), the input is returned unchanged.

    Args:
        tracks: Dicts that may contain "effnet_embedding": list[float].
        centroid: User's L2-normalized embedding centroid, or None.
        keep_fraction: Fraction in (0, 1] to keep among tracks that HAVE embeddings.

    Returns:
        Filtered tracks. Order of non-embedding tracks preserved; ranked tracks
        are returned in descending-similarity order.
    """
    if not tracks:
        return tracks
    if centroid is None:
        return tracks

    keep_fraction = max(0.01, min(1.0, keep_fraction))

    with_emb: list[tuple[float, dict[str, Any]]] = []
    without_emb: list[dict[str, Any]] = []

    for t in tracks:
        emb = t.get("effnet_embedding") or t.get("embedding")
        if isinstance(emb, list) and len(emb) > 0:
            with_emb.append((_cosine(emb, centroid), t))
        else:
            without_emb.append(t)

    if not with_emb:
        return tracks

    with_emb.sort(key=lambda x: x[0], reverse=True)
    keep_n = max(1, int(round(len(with_emb) * keep_fraction)))
    kept_ranked = [t for _, t in with_emb[:keep_n]]
    return kept_ranked + without_emb
