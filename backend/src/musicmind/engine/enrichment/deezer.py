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
    """Resolve an artist name to a Deezer artist id. First result wins."""
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


async def fetch_artist_full_discography(
    artist_name: str,
    *,
    limit: int = 500,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Deezer-only full-discography fallback (no auth, no user context).

    Walks `/search/artist` → `/artist/{id}/albums` → `/album/{id}/tracks`,
    deduped by ISRC (or (title, album) pair when ISRC is absent). Returns
    canonical cache-dict shape matching Apple/Spotify fetchers: catalog_id,
    isrc, name, artist_name, album_name, duration_ms, release_date,
    preview_url, artwork_url, service_source='deezer'.

    Deezer has no artist-discography cap — only pagination (limit≤100 per
    album-list page, typically fewer than 100 albums per artist anyway).
    `limit` is a soft collection cap so prolific artists don't blow memory.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15.0)

    collected: list[dict] = []
    seen_isrcs: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()

    def _push(track: dict) -> bool:
        """Returns True if we still need more, False if limit hit."""
        isrc = (track.get("isrc") or "").strip()
        if isrc and isrc in seen_isrcs:
            return len(collected) < limit
        key = (
            (track.get("name") or "").strip().lower(),
            (track.get("album_name") or "").strip().lower(),
        )
        if not isrc and key in seen_keys:
            return len(collected) < limit
        if isrc:
            seen_isrcs.add(isrc)
        else:
            seen_keys.add(key)
        collected.append(track)
        return len(collected) < limit

    try:
        artist_id = await _deezer_search_artist_id(client, artist_name)
        if artist_id is None:
            return []

        offset = 0
        page_size = 100
        while len(collected) < limit:
            try:
                resp = await client.get(
                    f"{DEEZER_API}/artist/{artist_id}/albums",
                    params={"limit": page_size, "index": offset},
                )
                if not resp.is_success:
                    break
            except httpx.HTTPError:
                break
            payload = resp.json()
            albums = payload.get("data") or []
            if not albums:
                break

            for album in albums:
                album_id = album.get("id")
                if not album_id:
                    continue
                try:
                    tr_resp = await client.get(
                        f"{DEEZER_API}/album/{album_id}",
                    )
                    if not tr_resp.is_success:
                        continue
                except httpx.HTTPError:
                    continue
                adata = tr_resp.json()
                tracks = (adata.get("tracks") or {}).get("data") or []
                album_artwork = (
                    adata.get("cover_xl")
                    or adata.get("cover_big")
                    or adata.get("cover_medium")
                    or ""
                )
                release_date = adata.get("release_date") or ""

                for t in tracks:
                    canon = {
                        "catalog_id": f"dz:{t.get('id')}",
                        "isrc": (t.get("isrc") or "").strip(),
                        "name": t.get("title") or "",
                        "artist_name": (t.get("artist") or {}).get(
                            "name"
                        ) or artist_name,
                        "album_name": adata.get("title") or "",
                        "duration_ms": int(t.get("duration") or 0) * 1000,
                        "release_date": release_date,
                        "preview_url": t.get("preview") or "",
                        "artwork_url": album_artwork,
                        "genre_names": [],
                        "service_source": "deezer",
                    }
                    if not _push(canon):
                        return collected[:limit]

            if not payload.get("next"):
                break
            offset += len(albums)
    finally:
        if own_client:
            await client.aclose()

    return collected[:limit]
