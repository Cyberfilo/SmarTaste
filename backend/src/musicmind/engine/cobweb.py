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
        primary_lower = parsed[0][0].lower()
        for name, weight in parsed:
            key = name.strip().lower()
            if not key or len(key) <= 1:
                continue
            if key == primary_lower:
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
