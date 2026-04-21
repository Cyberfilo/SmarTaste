"""On-demand Essentia deep analysis — user-triggered acoustic feature extraction.

Downloads 30s preview clips and runs Essentia to extract:
- 128-dim Discogs-EffNet embedding (for cosine similarity)
- BPM, key, scale, danceability (override API-sourced values)

Constraints:
- Max 10 seed tracks per request
- Never bulk-analyzes the library
- Sequential downloads (not parallel) to be polite to CDNs
- Temp files cleaned up immediately after extraction
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx

from musicmind.engine.audio.cache import (
    get_cached_embedding,
    store_embedding,
    store_features,
)
from musicmind.engine.audio.models import AudioEmbedding, ExtractedFeatures

logger = logging.getLogger(__name__)

MAX_SEED_TRACKS = 10


def _check_essentia() -> bool:
    """Check if essentia is available."""
    try:
        import essentia  # noqa: F401
        import essentia.standard  # noqa: F401

        return True
    except ImportError:
        return False


def _check_effnet() -> bool:
    """Check if the Discogs-EffNet TensorFlow model is available."""
    try:
        from essentia.standard import TensorflowPredictEffnetDiscogs  # noqa: F401

        return True
    except ImportError:
        return False


ESSENTIA_AVAILABLE = _check_essentia()
EFFNET_AVAILABLE = _check_effnet()


async def analyze_seeds(
    engine: Any,
    tracks: list[dict[str, Any]],
    *,
    user_id: str,
    progress_callback: Any | None = None,
) -> list[dict[str, Any]]:
    """Analyze seed tracks with Essentia for deep acoustic similarity.

    Downloads 30s preview clips, extracts features + 128-dim embeddings,
    and stores results in the audio cache. Skips tracks that already have
    embeddings cached.

    Args:
        engine: SQLAlchemy async engine.
        tracks: Song dicts with catalog_id, isrc, preview_url, artwork_url_template.
        user_id: User ID for cache scoping.
        progress_callback: Optional async callable(current, total, track_name) for progress.

    Returns:
        List of result dicts: {catalog_id, status, embedding_dim, features_extracted}.

    Raises:
        ValueError: If more than MAX_SEED_TRACKS provided.
    """
    if len(tracks) > MAX_SEED_TRACKS:
        raise ValueError(
            f"Maximum {MAX_SEED_TRACKS} seed tracks allowed, "
            f"got {len(tracks)}. Please reduce your selection."
        )

    if not ESSENTIA_AVAILABLE:
        logger.warning("Essentia not installed — falling back to API-only features")

    results: list[dict[str, Any]] = []

    for i, track in enumerate(tracks):
        catalog_id = track.get("catalog_id", "")
        name = track.get("name", "Unknown")
        isrc = track.get("isrc") or ""

        if progress_callback:
            try:
                await progress_callback(i + 1, len(tracks), name)
            except Exception:
                pass

        # Skip if embedding already cached
        existing = await get_cached_embedding(
            engine, catalog_id=catalog_id, user_id=user_id
        )
        if existing and existing.vector and len(existing.vector) == 128:
            results.append({
                "catalog_id": catalog_id,
                "status": "cached",
                "embedding_dim": 128,
                "features_extracted": False,
            })
            continue

        # Try to get a preview URL
        preview_url = track.get("preview_url") or ""

        if not preview_url:
            results.append({
                "catalog_id": catalog_id,
                "status": "no_preview",
                "embedding_dim": 0,
                "features_extracted": False,
            })
            continue

        # Download preview
        audio_bytes = await _download_preview(preview_url)
        if not audio_bytes:
            results.append({
                "catalog_id": catalog_id,
                "status": "download_failed",
                "embedding_dim": 0,
                "features_extracted": False,
            })
            continue

        # Extract features with Essentia (if available)
        if ESSENTIA_AVAILABLE:
            features, embedding = _extract_with_essentia(audio_bytes)
        else:
            features = None
            embedding = None

        # Store results
        embedding_dim = 0
        features_extracted = False

        if features:
            await store_features(
                engine,
                catalog_id=catalog_id,
                user_id=user_id,
                features=features,
            )
            features_extracted = True

        if embedding and len(embedding) == 128:
            await store_embedding(
                engine,
                catalog_id=catalog_id,
                user_id=user_id,
                embedding=AudioEmbedding(
                    catalog_id=catalog_id,
                    isrc=isrc or None,
                    vector=embedding,
                ),
            )
            embedding_dim = 128

        results.append({
            "catalog_id": catalog_id,
            "status": "analyzed" if features_extracted else "partial",
            "embedding_dim": embedding_dim,
            "features_extracted": features_extracted,
        })

    analyzed = sum(1 for r in results if r["status"] == "analyzed")
    cached = sum(1 for r in results if r["status"] == "cached")
    logger.info(
        "Essentia analysis complete: %d analyzed, %d cached, %d total",
        analyzed, cached, len(results),
    )
    return results


async def _download_preview(url: str) -> bytes | None:
    """Download a 30s audio preview clip.

    Sequential, not parallel — polite to CDNs.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.warning("Failed to download preview from %s", url[:80])
        return None


def _extract_with_essentia(audio_bytes: bytes) -> tuple[ExtractedFeatures | None, list[float] | None]:
    """Extract audio features and embedding from raw audio bytes using Essentia.

    Writes to a temp file (Essentia needs a file path), processes, and cleans up.

    Returns:
        Tuple of (ExtractedFeatures, embedding_vector) or (None, None) on failure.
    """
    if not ESSENTIA_AVAILABLE:
        return None, None

    try:
        import essentia.standard as es

        # Write to temp file (Essentia's MonoLoader needs a path)
        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)

        try:
            # Load audio at 16kHz mono
            audio = es.MonoLoader(filename=str(tmp_path), sampleRate=16000)()

            if len(audio) < 1600:  # Less than 0.1s
                logger.warning("Audio too short for analysis")
                return None, None

            # BPM
            rhythm = es.RhythmExtractor2013(method="multifeature")
            bpm, beats, beats_confidence, _, _ = rhythm(audio)

            # Key and scale
            key_extractor = es.KeyExtractor()
            key, scale, key_strength = key_extractor(audio)

            # Energy (RMS-based)
            rms = es.RMS()
            energy_raw = float(rms(audio))
            energy = min(1.0, energy_raw * 10)  # Normalize roughly to 0-1

            # Danceability
            dance = es.Danceability()
            danceability, _ = dance(audio)

            # Spectral centroid (brightness proxy)
            centroid = es.SpectralCentroidTime()
            brightness_raw = float(centroid(audio))
            brightness = min(1.0, brightness_raw / 8000)  # Normalize

            # Beat strength
            beat_strength = float(beats_confidence.mean()) if len(beats_confidence) > 0 else 0.5

            features = ExtractedFeatures(
                tempo=round(float(bpm), 1),
                energy=round(energy, 3),
                danceability=round(float(danceability), 3),
                brightness=round(brightness, 3),
                beat_strength=round(beat_strength, 3),
                key=key,
                scale=scale,
            )

            # Embedding (if TensorFlow model available)
            embedding: list[float] | None = None
            if EFFNET_AVAILABLE:
                try:
                    from essentia.standard import (
                        MonoLoader,
                        TensorflowPredictEffnetDiscogs,
                    )

                    # EffNet needs 44.1kHz
                    audio_44k = MonoLoader(
                        filename=str(tmp_path), sampleRate=44100
                    )()
                    model = TensorflowPredictEffnetDiscogs(
                        graphFilename="discogs-effnet-bs64-1.pb",
                        output="PartitionedCall:1",
                    )
                    embeddings = model(audio_44k)
                    if len(embeddings) > 0:
                        # Average across frames
                        embedding = [
                            round(float(v), 6)
                            for v in embeddings.mean(axis=0)
                        ]
                except Exception:
                    logger.debug("EffNet embedding extraction failed, continuing")

            return features, embedding

        finally:
            # Clean up temp file immediately
            tmp_path.unlink(missing_ok=True)

    except Exception:
        logger.exception("Essentia extraction failed")
        return None, None
