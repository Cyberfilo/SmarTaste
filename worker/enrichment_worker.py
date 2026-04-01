"""Standalone enrichment worker with discography support + proxy rotation.

Connects to Railway PostgreSQL. Two phases:
  1. Enrich un-enriched library tracks (Deezer → ReccoBeats)
  2. Discover + enrich full discographies of top 80% artists

Handles ReccoBeats 429 with Retry-After header.
Rotates through proxies from proxy.txt on rate limits.

Run: DATABASE_URL="postgresql://..." python3 enrichment_worker.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("worker")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SOUNDSTAT_API_KEY = os.environ.get("SOUNDSTAT_API_KEY", "")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "30"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "500"))
SLEEP_SECONDS = int(os.environ.get("SLEEP_SECONDS", "5"))

DEEZER_API = "https://api.deezer.com"
RECCOBEATS_API = "https://api.reccobeats.com/v1/analysis/audio-features"


# ── Proxy Manager ────────────────────────────────────────────────────────────


PROXY_URLS = os.environ.get("PROXY_URLS", "").split(",")
# Default Geonode free proxy list
DEFAULT_PROXY_URL = (
    "https://proxylist.geonode.com/api/proxy-list"
    "?limit=200&page=1&sort_by=lastChecked&sort_type=desc&protocols=http"
)


class ProxyManager:
    """Load proxies from proxy.txt, URLs, or Geonode API. Rotate on failure."""

    def __init__(self) -> None:
        self.proxies: list[str] = []
        self.failed: set[str] = set()

    async def load(self) -> None:
        """Load proxies from all sources."""
        # 1. proxy.txt file
        self._load_file()
        # 2. Environment URLs
        for url in PROXY_URLS:
            url = url.strip()
            if url:
                await self._load_url(url)
        # 3. Default Geonode if nothing loaded
        if not self.proxies:
            await self._load_url(DEFAULT_PROXY_URL)

        if self.proxies:
            logger.info("Loaded %d proxies total", len(self.proxies))
        else:
            logger.info("No proxies loaded — using direct connection")

    def _load_file(self) -> None:
        path = Path(__file__).parent / "proxy.txt"
        if not path.exists():
            return
        count = 0
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                self.proxies.append(line)
                count += 1
        if count:
            logger.info("Loaded %d proxies from proxy.txt", count)

    async def _load_url(self, url: str) -> None:
        """Fetch proxies from a URL. Supports Geonode API format and plain text."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")

                if "json" in content_type or url.endswith("json") or "geonode" in url:
                    data = resp.json()
                    # Geonode format: {"data": [{"ip": "...", "port": "...", "protocols": [...]}]}
                    items = data.get("data", [])
                    if not items and isinstance(data, list):
                        items = data
                    count = 0
                    for item in items:
                        if isinstance(item, dict):
                            ip = item.get("ip", "")
                            port = item.get("port", "")
                            protocols = item.get("protocols", ["http"])
                            if ip and port:
                                proto = protocols[0] if protocols else "http"
                                proxy = f"{proto}://{ip}:{port}"
                                if proxy not in self.proxies:
                                    self.proxies.append(proxy)
                                    count += 1
                    logger.info("Loaded %d proxies from %s", count, url[:60])
                else:
                    # Plain text: one proxy per line
                    count = 0
                    for line in resp.text.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            if "://" not in line:
                                line = f"http://{line}"
                            if line not in self.proxies:
                                self.proxies.append(line)
                                count += 1
                    logger.info("Loaded %d proxies from %s", count, url[:60])
        except Exception as e:
            logger.warning("Failed to load proxies from %s: %s", url[:60], e)

    def get(self) -> str | None:
        """Get a random working proxy, or None for direct connection."""
        available = [p for p in self.proxies if p not in self.failed]
        if not available:
            return None
        return random.choice(available)

    def mark_failed(self, proxy: str) -> None:
        self.failed.add(proxy)
        remaining = len(self.proxies) - len(self.failed)
        logger.info("Proxy failed: %s (%d remaining)", proxy[:30], remaining)

    def mark_rate_limited(self, proxy: str | None) -> None:
        """On 429, mark proxy as temporarily bad so we rotate to another."""
        if proxy:
            self.failed.add(proxy)

    def reset(self) -> None:
        """Reset failed proxies (call periodically to retry them)."""
        self.failed.clear()


proxy_mgr = ProxyManager()


def _get_client(timeout: float = 15.0) -> httpx.AsyncClient:
    """Get an httpx client, optionally through a proxy."""
    proxy = proxy_mgr.get()
    if proxy:
        return httpx.AsyncClient(timeout=timeout, proxy=proxy, follow_redirects=True)
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True)


# ── Main Loop ────────────────────────────────────────────────────────────────


async def main() -> None:
    if not DATABASE_URL:
        logger.error("Set DATABASE_URL to your Railway PostgreSQL URL")
        sys.exit(1)

    url = DATABASE_URL
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url, pool_size=20, max_overflow=20)

    # Load proxies from file + URLs + Geonode
    await proxy_mgr.load()

    logger.info("Worker started — concurrency=%d, batch=%d, proxies=%d",
                CONCURRENCY, BATCH_SIZE, len(proxy_mgr.proxies))

    try:
        # Phase 1: Library tracks
        lib_count = await enrich_library_tracks(engine)
        logger.info("Phase 1 complete: %d library tracks processed", lib_count)

        # Phase 2: Artist discographies
        await enrich_artist_discographies(engine)

        # Loop: keep checking
        cycle = 0
        while True:
            lib_count = await enrich_library_tracks(engine)
            if lib_count == 0:
                logger.info("All done. Sleeping %ds.", SLEEP_SECONDS)
            # Reset failed proxies every 10 cycles
            cycle += 1
            if cycle % 10 == 0:
                proxy_mgr.reset()
            await asyncio.sleep(SLEEP_SECONDS)
    finally:
        await engine.dispose()


# ── Phase 1: Library Tracks ──────────────────────────────────────────────────


async def enrich_library_tracks(engine) -> int:
    async with engine.begin() as conn:
        result = await conn.execute(sa.text(
            "SELECT s.catalog_id, s.user_id, s.name, s.artist_name, s.isrc, "
            "       s.service_source "
            "FROM song_metadata_cache s "
            "LEFT JOIN audio_features_cache a "
            "  ON s.catalog_id = a.catalog_id AND s.user_id = a.user_id "
            "WHERE a.catalog_id IS NULL "
            "LIMIT :limit"
        ), {"limit": BATCH_SIZE})
        rows = result.fetchall()

    if not rows:
        return 0

    tracks = [
        {"catalog_id": r[0], "user_id": r[1], "name": r[2],
         "artist_name": r[3], "isrc": r[4] or "", "service_source": r[5] or ""}
        for r in rows
    ]
    logger.info("Phase 1: Enriching %d library tracks...", len(tracks))
    stats = await _enrich_batch(engine, tracks)
    logger.info("Phase 1: %d ok, %d fail, %d skip", stats["ok"], stats["fail"], stats["skip"])
    return len(tracks)


# ── Phase 2: Artist Discographies ────────────────────────────────────────────


async def enrich_artist_discographies(engine) -> int:
    async with engine.begin() as conn:
        users_result = await conn.execute(sa.text(
            "SELECT DISTINCT user_id FROM service_connections"
        ))
        user_ids = [r[0] for r in users_result.fetchall()]

    logger.info("Phase 2: Found %d users", len(user_ids))
    total = 0
    for user_id in user_ids:
        count = await _discover_for_user(engine, user_id)
        total += count
    return total


async def _discover_for_user(engine, user_id: str) -> int:
    # Get artists from library
    async with engine.begin() as conn:
        artists_result = await conn.execute(sa.text(
            "SELECT artist_name, genre_names::text, COUNT(*) AS song_count "
            "FROM song_metadata_cache "
            "WHERE user_id = :uid AND artist_name != '' "
            "GROUP BY artist_name, genre_names::text "
            "ORDER BY song_count DESC"
        ), {"uid": user_id})
        raw_artists = artists_result.fetchall()

    # Aggregate by artist name
    artist_counts: dict[str, int] = {}
    for row in raw_artists:
        name = row[0]
        artist_counts[name] = artist_counts.get(name, 0) + row[2]

    sorted_artists = sorted(artist_counts.items(), key=lambda x: x[1], reverse=True)
    cutoff = max(1, int(len(sorted_artists) * 0.8))
    relevant = [name for name, _ in sorted_artists[:cutoff]]

    logger.info("  User %s: %d artists, fetching top %d discographies",
                user_id[:8], len(sorted_artists), len(relevant))

    if not relevant:
        return 0

    # Fetch discographies concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _fetch(name: str) -> list[dict]:
        async with semaphore:
            return await _deezer_artist_top(name)

    results = await asyncio.gather(
        *[_fetch(n) for n in relevant[:100]],
        return_exceptions=True,
    )

    all_tracks: list[dict] = []
    artists_found = 0
    for result in results:
        if isinstance(result, list) and result:
            artists_found += 1
            for t in result:
                t["user_id"] = user_id
            all_tracks.extend(result)

    logger.info("  Fetched %d tracks from %d/%d artists on Deezer",
                len(all_tracks), artists_found, len(relevant))

    # Deduplicate
    seen: set[str] = set()
    unique: list[dict] = []
    for t in all_tracks:
        cid = t.get("catalog_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(t)

    # Filter already-enriched
    async with engine.begin() as conn:
        existing = await conn.execute(sa.text(
            "SELECT catalog_id FROM audio_features_cache WHERE user_id = :uid"
        ), {"uid": user_id})
        existing_ids = {r[0] for r in existing.fetchall()}

    to_enrich = [t for t in unique if t["catalog_id"] not in existing_ids]

    if not to_enrich:
        logger.info("  All discography tracks already enriched")
        return 0

    logger.info("  Enriching %d new tracks (%d cached)...",
                len(to_enrich), len(unique) - len(to_enrich))

    total_ok = 0
    for i in range(0, len(to_enrich), BATCH_SIZE):
        batch = to_enrich[i:i + BATCH_SIZE]
        stats = await _enrich_batch(engine, batch)
        total_ok += stats["ok"]
        logger.info("  Progress: %d/%d done (batch: %d ok, %d fail)",
                    min(i + len(batch), len(to_enrich)), len(to_enrich),
                    stats["ok"], stats["fail"])

    logger.info("  Done: %d tracks enriched for user %s", total_ok, user_id[:8])
    return len(to_enrich)


async def _deezer_artist_top(artist_name: str) -> list[dict]:
    """Fetch an artist's top 50 tracks from Deezer."""
    try:
        async with _get_client(10.0) as client:
            resp = await client.get(
                f"{DEEZER_API}/search/artist",
                params={"q": artist_name, "limit": 1},
            )
            artists = resp.json().get("data", [])
            if not artists:
                return []
            artist_id = artists[0].get("id")
            if not artist_id:
                return []
            resp2 = await client.get(
                f"{DEEZER_API}/artist/{artist_id}/top",
                params={"limit": 50},
            )
            return [
                {
                    "catalog_id": f"dz_{item['id']}",
                    "name": item.get("title", ""),
                    "artist_name": item.get("artist", {}).get("name", artist_name),
                    "isrc": "",
                    "service_source": "deezer",
                    "preview_url": item.get("preview", ""),
                }
                for item in resp2.json().get("data", []) if item.get("id")
            ]
    except Exception:
        return []


# ── Enrichment ───────────────────────────────────────────────────────────────


async def _enrich_batch(engine, tracks: list[dict]) -> dict[str, int]:
    stats = {"ok": 0, "fail": 0, "skip": 0}
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _bounded(track: dict) -> str:
        async with semaphore:
            return await _enrich_track(engine, track)

    results = await asyncio.gather(
        *[_bounded(t) for t in tracks],
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            stats["fail"] += 1
        elif r in ("reccobeats", "deezer"):
            stats["ok"] += 1
        elif r == "skipped":
            stats["skip"] += 1
        else:
            stats["fail"] += 1
    return stats


async def _enrich_track(engine, track: dict) -> str:
    catalog_id = track["catalog_id"]
    user_id = track.get("user_id", "")
    name = track.get("name", "")
    artist_name = track.get("artist_name", "")
    preview_url = track.get("preview_url", "")

    if not catalog_id or not user_id or not name:
        await _store_empty(engine, catalog_id, user_id)
        return "failed"

    features: dict = {}
    feature_source: dict = {}

    # Stage 1: Deezer (skip search if preview_url already known)
    if not preview_url:
        try:
            async with _get_client(10.0) as client:
                resp = await client.get(
                    f"{DEEZER_API}/search",
                    params={"q": f"{name} {artist_name}", "limit": 1},
                )
                items = resp.json().get("data", [])
                if items:
                    did = items[0]["id"]
                    resp2 = await client.get(f"{DEEZER_API}/track/{did}")
                    full = resp2.json()
                    bpm = full.get("bpm")
                    if bpm and bpm > 0:
                        features["tempo"] = float(bpm)
                        feature_source["tempo"] = "deezer"
                    preview_url = full.get("preview", "")
        except Exception:
            pass

    # Stage 2: ReccoBeats with retry + proxy rotation on 429
    if preview_url:
        recco_features = await _reccobeats_with_retry(preview_url)
        if recco_features:
            for field, key in [
                ("energy", "energy"), ("danceability", "danceability"),
                ("acousticness", "acousticness"), ("valence_proxy", "valence"),
                ("instrumentalness", "instrumentalness"),
            ]:
                if recco_features.get(key) is not None:
                    features[field] = round(float(recco_features[key]), 4)
                    feature_source[field] = "reccobeats"
            if recco_features.get("tempo") and recco_features["tempo"] > 0 and "tempo" not in features:
                features["tempo"] = round(float(recco_features["tempo"]), 2)
                feature_source["tempo"] = "reccobeats"
            if recco_features.get("loudness") is not None:
                raw = float(recco_features["loudness"])
                features["loudness"] = round(max(0.0, min(1.0, (raw + 60) / 60)), 4)
                feature_source["loudness"] = "reccobeats"

    if features:
        await _store(engine, catalog_id, user_id, features, feature_source)
        return "reccobeats" if "energy" in features else "deezer"
    else:
        await _store_empty(engine, catalog_id, user_id)
        return "failed"


async def _reccobeats_with_retry(preview_url: str, max_retries: int = 4) -> dict | None:
    """Download preview → upload to ReccoBeats. Retry on 429 with Retry-After.
    Rotates proxy on rate limit."""

    # Download preview once
    audio_bytes: bytes | None = None
    try:
        async with _get_client(20.0) as client:
            resp = await client.get(preview_url)
            audio_bytes = resp.content
    except Exception:
        return None

    if not audio_bytes or len(audio_bytes) < 1000:
        return None

    current_proxy = proxy_mgr.get()

    for attempt in range(max_retries):
        try:
            transport = None
            if current_proxy:
                transport = httpx.AsyncHTTPTransport(proxy=current_proxy)

            async with httpx.AsyncClient(timeout=60.0, transport=transport) as client:
                files = {"audioFile": ("p.mp3", audio_bytes, "audio/mpeg")}
                rr = await client.post(RECCOBEATS_API, files=files)

                if rr.status_code == 200:
                    return rr.json()

                if rr.status_code == 429:
                    retry_after = rr.headers.get("Retry-After", "")
                    try:
                        wait = float(retry_after)
                    except (ValueError, TypeError):
                        wait = 3.0 + attempt * 2

                    logger.info(
                        "ReccoBeats 429 (attempt %d/%d), Retry-After: %.0fs, rotating proxy",
                        attempt + 1, max_retries, wait,
                    )

                    # Mark current proxy as rate-limited, try another
                    proxy_mgr.mark_rate_limited(current_proxy)
                    current_proxy = proxy_mgr.get()

                    await asyncio.sleep(wait)
                    continue

                # Other error — don't retry
                return None

        except httpx.ReadTimeout:
            logger.debug("ReccoBeats timeout attempt %d/%d", attempt + 1, max_retries)
            # Try different proxy on timeout
            if current_proxy:
                proxy_mgr.mark_failed(current_proxy)
                current_proxy = proxy_mgr.get()
            continue
        except httpx.ProxyError:
            if current_proxy:
                proxy_mgr.mark_failed(current_proxy)
                current_proxy = proxy_mgr.get()
            continue
        except Exception:
            return None

    del audio_bytes
    return None


# ── DB ───────────────────────────────────────────────────────────────────────


async def _store(engine, cid: str, uid: str, features: dict, source: dict) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO audio_features_cache "
            "(catalog_id, user_id, tempo, energy, danceability, acousticness, "
            " valence_proxy, instrumentalness, loudness, feature_source, "
            " enriched_at, analyzed_at) "
            "VALUES (:cid, :uid, :tempo, :energy, :dance, :acoustic, "
            " :valence, :inst, :loud, :src, :ea, :aa) "
            "ON CONFLICT (catalog_id, user_id) DO UPDATE SET "
            "tempo=COALESCE(EXCLUDED.tempo, audio_features_cache.tempo), "
            "energy=COALESCE(EXCLUDED.energy, audio_features_cache.energy), "
            "danceability=COALESCE(EXCLUDED.danceability, audio_features_cache.danceability), "
            "acousticness=COALESCE(EXCLUDED.acousticness, audio_features_cache.acousticness), "
            "valence_proxy=COALESCE(EXCLUDED.valence_proxy, audio_features_cache.valence_proxy), "
            "instrumentalness=COALESCE(EXCLUDED.instrumentalness, audio_features_cache.instrumentalness), "
            "loudness=COALESCE(EXCLUDED.loudness, audio_features_cache.loudness), "
            "feature_source=EXCLUDED.feature_source, enriched_at=EXCLUDED.enriched_at, "
            "analyzed_at=EXCLUDED.analyzed_at"
        ), {
            "cid": cid, "uid": uid,
            "tempo": features.get("tempo"), "energy": features.get("energy"),
            "dance": features.get("danceability"), "acoustic": features.get("acousticness"),
            "valence": features.get("valence_proxy"), "inst": features.get("instrumentalness"),
            "loud": features.get("loudness"), "src": json.dumps(source),
            "ea": now, "aa": now,
        })


async def _store_empty(engine, cid: str, uid: str) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO audio_features_cache "
            "(catalog_id, user_id, feature_source, enriched_at, analyzed_at) "
            "VALUES (:cid, :uid, :src, :ea, :aa) "
            "ON CONFLICT (catalog_id, user_id) DO NOTHING"
        ), {
            "cid": cid, "uid": uid,
            "src": json.dumps({"_status": "no_data_available"}),
            "ea": now, "aa": now,
        })


if __name__ == "__main__":
    asyncio.run(main())
