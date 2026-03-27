"""Cross-service track deduplication.

Identifies and merges duplicate tracks across Spotify and Apple Music using:
1. ISRC (International Standard Recording Code) as primary match key
2. Fuzzy title + artist name matching as fallback for tracks without ISRC

When duplicates are found, metadata is merged: the record with richer data
(more genres, longer editorial notes, etc.) takes priority for each field.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


# ── Text Normalization ───────────────────────────────────────────────────────

# Patterns to strip from titles for fuzzy comparison
_PAREN_SUFFIX = re.compile(r"\s*[\(\[].+?[\)\]]")
_FEAT_SUFFIX = re.compile(r"\s*(feat\.?|ft\.?|featuring)\s+.+", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_MULTI_SPACE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    """Normalize text for fuzzy comparison.

    Strips accents, parenthetical suffixes, featuring credits, and
    non-alphanumeric characters. Lowercases everything.
    """
    if not text:
        return ""

    # Decompose unicode and strip accents
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))

    # Lowercase
    lower = stripped.lower()

    # Strip parenthetical suffixes: "(feat. X)", "[Deluxe Edition]", etc.
    lower = _PAREN_SUFFIX.sub("", lower)

    # Strip featuring credits outside parentheses
    lower = _FEAT_SUFFIX.sub("", lower)

    # Remove non-alphanumeric (keep spaces)
    lower = _NON_ALNUM.sub("", lower)

    # Collapse whitespace
    return _MULTI_SPACE.sub(" ", lower).strip()


# ── Fuzzy Matching ───────────────────────────────────────────────────────────


def fuzzy_title_artist_match(
    title_a: str,
    artist_a: str,
    title_b: str,
    artist_b: str,
) -> bool:
    """Check if two tracks are likely the same song via fuzzy text comparison.

    Normalizes titles and artist names, then checks for exact match after
    normalization. This catches differences like:
    - "Song (feat. X)" vs "Song"
    - "Song [Deluxe]" vs "Song"
    - Accent differences: "cafe" vs "cafe"
    - Punctuation differences

    Args:
        title_a: Track title from service A.
        artist_a: Artist name from service A.
        title_b: Track title from service B.
        artist_b: Artist name from service B.

    Returns:
        True if tracks are likely the same song.
    """
    norm_title_a = _normalize_text(title_a)
    norm_title_b = _normalize_text(title_b)
    norm_artist_a = _normalize_text(artist_a)
    norm_artist_b = _normalize_text(artist_b)

    if not norm_title_a or not norm_title_b:
        return False

    if not norm_artist_a or not norm_artist_b:
        return False

    return norm_title_a == norm_title_b and norm_artist_a == norm_artist_b


# ── Metadata Merging ─────────────────────────────────────────────────────────


def _merge_track_metadata(
    track_a: dict[str, Any],
    track_b: dict[str, Any],
) -> dict[str, Any]:
    """Merge metadata from two duplicate track records.

    Prefers the record with richer data for each field:
    - genre_names: use the longer list
    - editorial_notes: use the non-empty one
    - isrc: use whichever has it
    - Preserves both catalog_ids via _service_ids dict

    Both records are kept accessible for cross-service linking.
    """
    merged = dict(track_a)  # Start with track_a as base

    # Track service origins
    service_a = track_a.get("service_source", "unknown")
    service_b = track_b.get("service_source", "unknown")

    # Store both catalog IDs for cross-service linking
    merged["_service_ids"] = {
        service_a: track_a.get("catalog_id", ""),
        service_b: track_b.get("catalog_id", ""),
    }

    # Use whichever has the ISRC
    if not merged.get("isrc") and track_b.get("isrc"):
        merged["isrc"] = track_b["isrc"]

    # Use the longer genre list (more specific)
    genres_a = track_a.get("genre_names") or []
    genres_b = track_b.get("genre_names") or []
    if isinstance(genres_a, str):
        genres_a = [genres_a]
    if isinstance(genres_b, str):
        genres_b = [genres_b]
    if len(genres_b) > len(genres_a):
        merged["genre_names"] = genres_b

    # Prefer non-empty editorial notes
    if not merged.get("editorial_notes") and track_b.get("editorial_notes"):
        merged["editorial_notes"] = track_b["editorial_notes"]

    # Prefer non-empty audio traits
    traits_a = track_a.get("audio_traits") or []
    traits_b = track_b.get("audio_traits") or []
    if not traits_a and traits_b:
        merged["audio_traits"] = traits_b

    # Prefer non-empty artwork
    if not merged.get("artwork_url_template") and track_b.get("artwork_url_template"):
        merged["artwork_url_template"] = track_b["artwork_url_template"]

    # Prefer non-empty preview URL
    if not merged.get("preview_url") and track_b.get("preview_url"):
        merged["preview_url"] = track_b["preview_url"]

    # Duration: use the one that exists
    if not merged.get("duration_ms") and track_b.get("duration_ms"):
        merged["duration_ms"] = track_b["duration_ms"]

    # Mark as unified
    merged["service_source"] = "unified"

    return merged


# ── ISRC-based Deduplication ─────────────────────────────────────────────────


def _isrc_match(
    tracks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group tracks by ISRC and merge duplicates.

    Args:
        tracks: List of track dicts with optional 'isrc' field.

    Returns:
        Tuple of (deduplicated tracks with ISRC, tracks without ISRC).
    """
    isrc_groups: dict[str, list[dict[str, Any]]] = {}
    no_isrc: list[dict[str, Any]] = []

    for track in tracks:
        isrc = track.get("isrc")
        if isrc:
            isrc_key = isrc.strip().upper()
            if isrc_key not in isrc_groups:
                isrc_groups[isrc_key] = []
            isrc_groups[isrc_key].append(track)
        else:
            no_isrc.append(track)

    deduplicated: list[dict[str, Any]] = []
    for _isrc, group in isrc_groups.items():
        if len(group) == 1:
            deduplicated.append(group[0])
        else:
            # Merge all tracks in the group
            merged = group[0]
            for other in group[1:]:
                merged = _merge_track_metadata(merged, other)
            deduplicated.append(merged)

    return deduplicated, no_isrc


# ── Fuzzy Deduplication ──────────────────────────────────────────────────────


def _fuzzy_dedup(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate tracks without ISRC using fuzzy title+artist matching.

    O(n^2) comparison but typically applied only to the small subset
    of tracks lacking ISRC codes.
    """
    if not tracks:
        return []

    result: list[dict[str, Any]] = []
    used: set[int] = set()

    for i, track_a in enumerate(tracks):
        if i in used:
            continue

        current = track_a
        for j in range(i + 1, len(tracks)):
            if j in used:
                continue

            track_b = tracks[j]
            if fuzzy_title_artist_match(
                current.get("name", ""),
                current.get("artist_name", ""),
                track_b.get("name", ""),
                track_b.get("artist_name", ""),
            ):
                current = _merge_track_metadata(current, track_b)
                used.add(j)

        result.append(current)

    return result


# ── Public API ───────────────────────────────────────────────────────────────


def deduplicate_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate tracks across services.

    Two-phase approach:
    1. ISRC match: group by ISRC, merge duplicates
    2. Fuzzy match: for remaining tracks without ISRC, compare
       normalized title + artist name

    Args:
        tracks: List of track dicts from multiple services. Expected keys:
            catalog_id, name, artist_name, isrc (optional), service_source.

    Returns:
        Deduplicated list of track dicts. Cross-service duplicates are merged
        with combined metadata and service_source="unified".
    """
    if not tracks:
        return []

    # Phase 1: ISRC-based dedup
    isrc_deduped, no_isrc = _isrc_match(tracks)

    # Phase 2: Fuzzy dedup for tracks without ISRC
    fuzzy_deduped = _fuzzy_dedup(no_isrc)

    return isrc_deduped + fuzzy_deduped
