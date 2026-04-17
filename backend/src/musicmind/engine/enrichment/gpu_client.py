"""HTTP client for Modal GPU serverless worker.

Calls Modal endpoints for Tier 2 enrichment (CLAP + MERT embeddings).
Gracefully returns None if Modal not configured or unavailable.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Items per HTTP request. Bigger = fewer round-trips + better GPU batching,
# but risks OOM on the Mac MPS server. 25 is empirically fine with the
# CLAP-tiny + MERT-95M-float16 config.
GPU_BATCH_SIZE = 25

# Concurrent HTTP requests. The server (Modal A100 or local Mac via MPS)
# serializes GPU ops internally but pipelines audio decode with inference,
# so a small amount of concurrency reduces wall time for larger queues.
GPU_MAX_CONCURRENT = 3

# If fewer than this are pending, defer to the next worker cycle where
# more tracks will likely be available to batch with. A100 cold-start + audio
# decode dominate wall time for small batches; raising the floor from 3 → 15
# amortises those fixed costs across meaningfully larger groups, at the cost
# of higher latency for the last few straggler tracks in a run.
GPU_MIN_BATCH = 15


async def enrich_via_gpu(
    preview_url: str,
    modal_endpoint_url: str,
) -> dict[str, Any] | None:
    """Call Modal GPU worker for CLAP + MERT enrichment.

    Args:
        preview_url: URL to 30s audio preview.
        modal_endpoint_url: Base URL of Modal web endpoint.

    Returns:
        Dict with clap_512 and mert_768 lists, or None on failure.
    """
    if not modal_endpoint_url:
        return None

    url = modal_endpoint_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json={"preview_url": preview_url})
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                logger.warning("GPU enrichment error: %s", data["error"])
            return data
    except Exception:
        logger.debug("GPU enrichment failed for %s", preview_url[:80], exc_info=True)
        return None


async def encode_text_via_gpu(
    text: str,
    modal_endpoint_url: str,
) -> list[float] | None:
    """Encode text to CLAP 512-dim embedding via Modal GPU worker.

    Used for natural language search ("find me something darker").
    """
    if not modal_endpoint_url:
        return None

    url = f"{modal_endpoint_url.rstrip('/')}/encode_text"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json={"text": text})
            resp.raise_for_status()
            data = resp.json()
            return data.get("clap_512")
    except Exception:
        logger.debug("GPU text encoding failed", exc_info=True)
        return None


async def enrich_batch_via_gpu(
    preview_urls: list[str],
    modal_endpoint_url: str,
) -> list[dict[str, Any]]:
    """Batch GPU enrichment for multiple tracks."""
    if not modal_endpoint_url:
        return []

    url = modal_endpoint_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(url, json={"preview_urls": preview_urls})
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
    except Exception:
        logger.warning(
            "GPU batch enrichment failed for %d URLs",
            len(preview_urls), exc_info=True,
        )
        return []


async def enrich_batch_bytes_via_gpu(
    audio_items: list[str],
    modal_endpoint_url: str,
) -> list[dict[str, Any]]:
    """Batch GPU enrichment from base64-encoded audio bytes.

    Sends cached audio bytes directly to Modal instead of URLs.
    Eliminates expired Deezer CDN URL failures entirely.
    """
    if not modal_endpoint_url or not audio_items:
        return []

    url = modal_endpoint_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(
                url, json={"audio_items": audio_items},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
    except Exception:
        logger.warning(
            "GPU batch bytes enrichment failed for %d items",
            len(audio_items), exc_info=True,
        )
        return []


async def enrich_bytes_concurrent(
    b64_items: list[str],
    modal_endpoint_url: str,
    *,
    batch_size: int = GPU_BATCH_SIZE,
    max_concurrent: int = GPU_MAX_CONCURRENT,
) -> list[dict[str, Any]]:
    """Split base64 items into chunks and dispatch concurrently.

    Returns a flat list of results aligned with the input order. Batches
    that fail produce empty-dict results so index alignment is preserved.

    Use this over calling `enrich_batch_bytes_via_gpu` directly when you
    have more than `batch_size` items — it pipelines audio decode with
    GPU inference on the server side, cutting wall time ~2-3x on the Mac
    local worker.
    """
    if not modal_endpoint_url or not b64_items:
        return []

    chunks = [
        b64_items[i:i + batch_size]
        for i in range(0, len(b64_items), batch_size)
    ]
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _send(chunk: list[str]) -> list[dict[str, Any]]:
        async with semaphore:
            results = await enrich_batch_bytes_via_gpu(chunk, modal_endpoint_url)
            if not results:
                # Pad with empty dicts so caller can still zip by position
                return [{} for _ in chunk]
            return results

    chunk_results = await asyncio.gather(
        *[_send(c) for c in chunks],
        return_exceptions=False,
    )
    flat: list[dict[str, Any]] = []
    for cr in chunk_results:
        flat.extend(cr)
    return flat


async def enrich_urls_concurrent(
    preview_urls: list[str],
    modal_endpoint_url: str,
    *,
    batch_size: int = GPU_BATCH_SIZE,
    max_concurrent: int = GPU_MAX_CONCURRENT,
) -> list[dict[str, Any]]:
    """Same as enrich_bytes_concurrent but for preview URLs (fallback path)."""
    if not modal_endpoint_url or not preview_urls:
        return []

    chunks = [
        preview_urls[i:i + batch_size]
        for i in range(0, len(preview_urls), batch_size)
    ]
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _send(chunk: list[str]) -> list[dict[str, Any]]:
        async with semaphore:
            results = await enrich_batch_via_gpu(chunk, modal_endpoint_url)
            if not results:
                return [{} for _ in chunk]
            return results

    chunk_results = await asyncio.gather(
        *[_send(c) for c in chunks],
        return_exceptions=False,
    )
    flat: list[dict[str, Any]] = []
    for cr in chunk_results:
        flat.extend(cr)
    return flat
