"""ReccoBeats API client for Spotify-equivalent audio features.

Free API, no auth. Returns energy, danceability, acousticness, valence.
Requires Spotify track ID (resolve via MusicBrainz if only ISRC available).
https://api.reccobeats.com/v1/track/features?spotify_id={id}
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RECCOBEATS_API = "https://api.reccobeats.com/v1"
MAX_RETRIES = 2
BACKOFF_BASE = 1.0


async def fetch_reccobeats_features(spotify_id: str) -> dict[str, Any] | None:
    """Fetch audio features from ReccoBeats by Spotify track ID.

    Returns a partial feature dict with energy, danceability, acousticness, valence.
    Returns None if the track is not found or on error.

    Args:
        spotify_id: Spotify track ID (e.g. "4iV5W9uYEdYUVa79Axb7Rh").

    Returns:
        Dict with available features. None on miss.
    """
    if not spotify_id:
        return None

    url = f"{RECCOBEATS_API}/track/features"

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params={"spotify_id": spotify_id})

                if resp.status_code == 404:
                    return None

                resp.raise_for_status()
                data = resp.json()

                result: dict[str, Any] = {}

                # Map ReccoBeats fields to our schema
                field_map = {
                    "energy": "energy",
                    "danceability": "danceability",
                    "acousticness": "acousticness",
                    "valence": "valence_proxy",
                    "tempo": "tempo",
                    "instrumentalness": "instrumentalness",
                    "loudness": "loudness",
                }

                for api_field, our_field in field_map.items():
                    val = data.get(api_field)
                    if val is not None:
                        result[our_field] = float(val)

                return result if result else None

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                import asyncio

                wait = BACKOFF_BASE * (2**attempt)
                logger.warning("ReccoBeats rate limited, waiting %.1fs", wait)
                await asyncio.sleep(wait)
                continue
            logger.warning("ReccoBeats HTTP error for %s: %s", spotify_id, exc)
            return None
        except httpx.HTTPError:
            logger.warning("ReccoBeats connection error for %s", spotify_id)
            return None

    logger.warning("ReccoBeats max retries exceeded for %s", spotify_id)
    return None
