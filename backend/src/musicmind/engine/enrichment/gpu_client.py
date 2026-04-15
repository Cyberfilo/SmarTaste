"""HTTP client for Modal GPU serverless worker.

Calls Modal endpoints for Tier 2 enrichment (CLAP + MERT embeddings).
Gracefully returns None if Modal not configured or unavailable.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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
