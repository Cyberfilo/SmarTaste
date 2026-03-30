"""SoundStat API client for complete audio features.

Paid API (0.01 EUR/track). Uses Spotify track IDs.
Auth: x-api-key header. Budget-gated — only used for specific recommendations.
Docs: https://soundstat.info/api/v1/docs
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SOUNDSTAT_API = "https://soundstat.info/api/v1"


async def fetch_soundstat_features(
    spotify_id: str,
    *,
    api_key: str,
) -> dict[str, Any] | None:
    """Fetch complete audio features from SoundStat by Spotify track ID.

    Returns a comprehensive feature dict including BPM, key, mode, energy,
    danceability, valence, instrumentalness, acousticness, loudness.

    Args:
        spotify_id: Spotify track ID (e.g. "4iV5W9uYEdYUVa79Axb7Rh").
        api_key: SoundStat API key (x-api-key header).

    Returns:
        Dict with available features. None on miss or error.
    """
    if not spotify_id or not api_key:
        return None

    url = f"{SOUNDSTAT_API}/track/{spotify_id}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={"x-api-key": api_key},
            )

            if resp.status_code == 404:
                return None

            resp.raise_for_status()
            data = resp.json()

            features = data.get("features")
            if not features:
                return None

            result: dict[str, Any] = {}

            # Direct mappings
            if features.get("tempo") is not None:
                result["tempo"] = float(features["tempo"])

            if features.get("energy") is not None:
                result["energy"] = float(features["energy"])

            if features.get("danceability") is not None:
                result["danceability"] = float(features["danceability"])

            if features.get("valence") is not None:
                result["valence_proxy"] = float(features["valence"])

            if features.get("acousticness") is not None:
                result["acousticness"] = float(features["acousticness"])

            if features.get("instrumentalness") is not None:
                result["instrumentalness"] = float(features["instrumentalness"])

            if features.get("loudness") is not None:
                result["loudness"] = float(features["loudness"])

            # Key and scale from SoundStat (key is 0-11 int, mode is 0/1)
            key_int = features.get("key")
            if key_int is not None:
                key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
                if 0 <= key_int <= 11:
                    result["key"] = key_names[key_int]

            mode = features.get("mode")
            if mode is not None:
                result["scale"] = "major" if mode == 1 else "minor"

            return result if result else None

    except httpx.HTTPStatusError as exc:
        logger.warning("SoundStat HTTP error for %s: %s", spotify_id, exc)
        return None
    except httpx.HTTPError:
        logger.warning("SoundStat connection error for %s", spotify_id)
        return None
