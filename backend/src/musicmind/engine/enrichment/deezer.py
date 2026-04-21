"""Deezer preview URL fallback — search by name when Apple Music lacks a preview.

Free API, no auth required. Rate limit ~10 req/s with exponential backoff.
Only used as a FALLBACK for preview URLs, not for audio features.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEEZER_API = "https://api.deezer.com"
MAX_RETRIES = 3
BACKOFF_BASE = 0.5


async def search_preview_url(
    *,
    name: str,
    artist_name: str,
    isrc: str | None = None,
) -> str | None:
    """Search Deezer for a 30s preview URL.

    Strategy: try ISRC lookup first (exact match), fall back to name search.

    Returns:
        Preview MP3 URL string, or None if not found.
    """
    if not name and not artist_name and not isrc:
        return None

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Strategy 1: ISRC direct lookup (exact match)
                if isrc:
                    try:
                        resp = await client.get(
                            f"{DEEZER_API}/2.0/track/isrc:{isrc}",
                        )
                        if resp.is_success:
                            data = resp.json()
                            if "error" not in data and data.get("preview"):
                                return data["preview"]
                    except Exception:
                        pass

                # Strategy 2: Name + artist search (fallback)
                if name and artist_name:
                    query = f"{name} {artist_name}"
                    resp = await client.get(
                        f"{DEEZER_API}/search",
                        params={"q": query, "limit": 1},
                    )
                    resp.raise_for_status()
                    items = resp.json().get("data", [])
                    if items:
                        preview = items[0].get("preview")
                        if preview:
                            return preview

                return None

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                wait = BACKOFF_BASE * (2**attempt)
                logger.debug("Deezer rate limited, waiting %.1fs", wait)
                await asyncio.sleep(wait)
                continue
            return None
        except httpx.HTTPError:
            return None

    return None


async def _deezer_search_artist_id(
    client: httpx.AsyncClient, artist_name: str,
) -> int | None:
    """Resolve an artist name to a Deezer artist id. First result wins.

    Kept for backwards compatibility; prefer
    `_deezer_discover_artist_ids` when you need to handle the
    duplicate-artist case (Deezer often has multiple ids for the same
    artist, one per release-era / distributor).
    """
    if not artist_name or not artist_name.strip():
        return None
    try:
        resp = await client.get(
            f"{DEEZER_API}/search/artist",
            params={"q": artist_name, "limit": 1},
        )
        if not resp.is_success:
            return None
        data = resp.json().get("data") or []
        if not data:
            return None
        aid = data[0].get("id")
        return int(aid) if aid else None
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def _norm_name(s: str) -> str:
    """Normalize artist name for matching: lowercase + strip + collapse WS."""
    if not s:
        return ""
    return " ".join(s.strip().lower().split())


async def _deezer_discover_artist_ids(
    client: httpx.AsyncClient,
    artist_name: str,
    *,
    max_ids: int = 3,
) -> list[int]:
    """Find all Deezer artist_ids that match `artist_name` exactly.

    Strategy: query `/search?q=<name>&limit=50` (TRACK search), aggregate
    unique artist_ids where `track.artist.name` matches the query
    (case-insensitive exact). This surfaces duplicate-id artists that
    `/search/artist` buries under its nb_fan sort (e.g., Philip on
    Deezer is split between id=96659 for old catalog and id=361808772
    for new catalog; the latter has 0 fans and never appears in
    `/search/artist` top-10, but its tracks do appear in `/search`).

    Returns up to `max_ids` matching ids, ordered by track-count in the
    sample response (so the primary account comes first).
    """
    from collections import Counter

    q = artist_name.strip()
    if not q:
        return []
    target = _norm_name(q)
    try:
        resp = await client.get(
            f"{DEEZER_API}/search",
            params={"q": q, "limit": 50},
        )
        if not resp.is_success:
            return []
        tracks = resp.json().get("data") or []
    except (httpx.HTTPError, ValueError, TypeError):
        return []

    by_id: Counter[int] = Counter()
    for t in tracks:
        a = t.get("artist") or {}
        aname = _norm_name(a.get("name") or "")
        if aname == target:
            aid = a.get("id")
            if aid is not None:
                try:
                    by_id[int(aid)] += 1
                except (TypeError, ValueError):
                    continue

    return [aid for aid, _ in by_id.most_common(max_ids)]


async def _fetch_artist_top_tracks_deezer(
    client: httpx.AsyncClient,
    artist_id: int,
    *,
    limit: int = 100,
) -> list[dict]:
    """Fetch up to `limit` top tracks for a Deezer artist_id.

    Single request to `/artist/{id}/top` — fast and rate-limit-friendly.
    Trade-off: ISRC is not present on the track objects returned here
    (Deezer's `/track/{id}` would expose it, but that would be one
    extra call per track = 100+ calls, defeating the point). Tracks
    land in global_song_cache with isrc=NULL; the existing
    `_backfill_isrcs` worker phase resolves them on subsequent cycles.
    """
    try:
        resp = await client.get(
            f"{DEEZER_API}/artist/{artist_id}/top",
            params={"limit": limit},
        )
        if not resp.is_success:
            return []
        return resp.json().get("data") or []
    except (httpx.HTTPError, ValueError, TypeError):
        return []


async def fetch_artist_full_discography(
    artist_name: str,
    *,
    limit: int = 500,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Deezer-only full-discography fallback (no auth, no user context).

    V 6.399: complete rewrite. Prior version walked `/search/artist` →
    `/artist/{id}/albums` → `/album/{id}` = 100+ requests per artist,
    which tripped rate limiting AND missed duplicate artist_ids. New
    algorithm:
      1. `/search?q=<name>&limit=50` (TRACK search) → aggregate all
         artist_ids whose tracks surface with an exact-matching artist
         name. Handles the duplicate-id case (Philip at 96659 AND
         361808772 both get found).
      2. For each matched artist_id (up to 3), `/artist/{id}/top?
         limit=100` → up to 100 tracks per id in 1 request each.
      3. Merge + dedupe by (title, album) pair. ISRCs are missing —
         the worker's `_backfill_isrcs` phase fills them later.

    Total: ~4 requests per artist instead of 100+. Rate limit safe,
    and catches dup-id artists that the old /search/artist approach
    missed entirely.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15.0)

    collected: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()

    def _push(track: dict) -> bool:
        """Returns True if we still need more, False if limit hit."""
        key = (
            (track.get("name") or "").strip().lower(),
            (track.get("album_name") or "").strip().lower(),
        )
        if key in seen_keys:
            return len(collected) < limit
        seen_keys.add(key)
        collected.append(track)
        return len(collected) < limit

    try:
        artist_ids = await _deezer_discover_artist_ids(
            client, artist_name, max_ids=3,
        )
        if not artist_ids:
            return []

        for artist_id in artist_ids:
            if len(collected) >= limit:
                break
            tracks = await _fetch_artist_top_tracks_deezer(
                client, artist_id, limit=100,
            )
            await asyncio.sleep(0.25)  # throttle between ids

            for t in tracks:
                album = t.get("album") or {}
                canon = {
                    "catalog_id": f"dz:{t.get('id')}",
                    "isrc": "",  # not on /top; backfill fills later
                    "name": t.get("title") or "",
                    "artist_name": (t.get("artist") or {}).get(
                        "name"
                    ) or artist_name,
                    "album_name": album.get("title") or "",
                    "duration_ms": int(t.get("duration") or 0) * 1000,
                    "release_date": "",
                    "preview_url": t.get("preview") or "",
                    "artwork_url": (
                        album.get("cover_xl")
                        or album.get("cover_big")
                        or album.get("cover_medium")
                        or ""
                    ),
                    "genre_names": [],
                    "service_source": "deezer",
                }
                if not _push(canon):
                    return collected[:limit]
    finally:
        if own_client:
            await client.aclose()

    return collected[:limit]

