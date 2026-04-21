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
    max_retries: int = 2,
) -> list[int]:
    """Find all Deezer artist_ids that match `artist_name` exactly.

    V 6.402: retry-on-empty with exponential backoff (1s, 2s). Deezer
    under rate-limit pressure returns `{"data": []}` silently (not a
    429), indistinguishable from a genuine no-match at first glance.
    Two retries absorb cross-phase burst contention; per-artist cost
    stays bounded (~3 requests worst case) and only pays the penalty
    when responses are actually empty.

    Strategy: `/search?q=<name>&limit=50` (TRACK search), aggregate
    artist_ids where `track.artist.name` matches exactly. Surfaces
    dup-id artists that `/search/artist` buries under nb_fan sort.
    """
    from collections import Counter

    q = artist_name.strip()
    if not q:
        return []
    target = _norm_name(q)

    tracks: list[dict] = []
    for attempt in range(max_retries + 1):
        try:
            resp = await client.get(
                f"{DEEZER_API}/search",
                params={"q": q, "limit": 50},
            )
            if not resp.is_success:
                if resp.status_code in (429, 500, 502, 503) and attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return []
            tracks = resp.json().get("data") or []
        except (httpx.HTTPError, ValueError, TypeError):
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            return []

        if not tracks and attempt < max_retries:
            await asyncio.sleep(2 ** attempt)
            continue
        break

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
    max_retries: int = 2,
) -> list[dict]:
    """Fetch up to `limit` top tracks for a Deezer artist_id.

    V 6.402: same retry-on-empty-or-5xx pattern as the discovery helper
    so transient throttling doesn't become permanent `tracks_found=0`
    state rows. ISRC isn't on `/top` responses; `_backfill_isrcs`
    worker phase resolves them on later cycles.
    """
    for attempt in range(max_retries + 1):
        try:
            resp = await client.get(
                f"{DEEZER_API}/artist/{artist_id}/top",
                params={"limit": limit},
            )
            if not resp.is_success:
                if resp.status_code in (429, 500, 502, 503) and attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return []
            data = resp.json().get("data") or []
        except (httpx.HTTPError, ValueError, TypeError):
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            return []

        if not data and attempt < max_retries:
            await asyncio.sleep(2 ** attempt)
            continue
        return data

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

