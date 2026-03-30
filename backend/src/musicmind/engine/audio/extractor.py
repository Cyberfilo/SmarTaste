"""Audio feature extractor — downloads previews and analyzes with essentia.

Extracts scalar features (BPM, danceability, energy, key, loudness) and
128-dim Discogs-EffNet embeddings from 30-second preview clips.

Gracefully returns None when essentia or dependencies are unavailable.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from musicmind.engine.audio.models import ExtractedFeatures

logger = logging.getLogger(__name__)

# Lazy-check for essentia availability
_ESSENTIA_AVAILABLE: bool | None = None


def _check_essentia() -> bool:
    """Check if essentia is importable."""
    global _ESSENTIA_AVAILABLE  # noqa: PLW0603
    if _ESSENTIA_AVAILABLE is None:
        try:
            import essentia  # noqa: F401
            import essentia.standard  # noqa: F401
            _ESSENTIA_AVAILABLE = True
        except ImportError:
            _ESSENTIA_AVAILABLE = False
            logger.info("essentia not available — audio extraction disabled")
    return _ESSENTIA_AVAILABLE


def _check_effnet() -> bool:
    """Check if essentia TensorFlow models are available."""
    try:
        from essentia.standard import TensorflowPredictEffnetDiscogs  # noqa: F401
        return True
    except (ImportError, AttributeError):
        return False


async def download_preview(preview_url: str) -> bytes | None:
    """Download a preview clip from a URL.

    Args:
        preview_url: URL to a 30-second audio preview (M4A/MP3).

    Returns:
        Raw audio bytes or None on failure.
    """
    if not preview_url:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(preview_url)
            resp.raise_for_status()
            return resp.content
    except Exception:
        logger.warning("Failed to download preview from %s", preview_url)
        return None


def extract_features(audio_bytes: bytes) -> ExtractedFeatures | None:
    """Extract audio features from raw audio bytes.

    Uses essentia's MonoLoader, RhythmExtractor, and spectral algorithms.
    Falls back to subset of features if specific algorithms fail.

    Args:
        audio_bytes: Raw audio file content (M4A, MP3, or WAV).

    Returns:
        ExtractedFeatures or None if essentia is unavailable.
    """
    if not _check_essentia():
        return None

    import essentia.standard as es

    # Write to temp file for MonoLoader
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        try:
            audio = es.MonoLoader(filename=tmp.name, sampleRate=16000)()
        except Exception:
            logger.warning("MonoLoader failed — file may be corrupt")
            return None

    if len(audio) < 16000:
        logger.warning("Audio too short for analysis (%d samples)", len(audio))
        return None

    features = ExtractedFeatures()

    # Tempo / BPM
    try:
        rhythm = es.RhythmExtractor2013(method="multifeature")
        bpm, beats, beats_confidence, _, beats_intervals = rhythm(audio)
        features.tempo = round(float(bpm), 1)
        # Beat strength from confidence
        features.beat_strength = round(float(np.mean(beats_confidence)), 3) if len(
            beats_confidence
        ) > 0 else None
    except Exception:
        logger.debug("Rhythm extraction failed")

    # Energy (RMS-based)
    try:
        rms = es.RMS()
        frame_gen = es.FrameGenerator(audio, frameSize=2048, hopSize=1024)
        rms_values = [rms(frame) for frame in frame_gen]
        if rms_values:
            mean_rms = float(np.mean(rms_values))
            # Normalize to 0-1 (typical RMS range for music: 0.01-0.3)
            features.energy = round(min(1.0, mean_rms / 0.2), 3)
    except Exception:
        logger.debug("Energy extraction failed")

    # Spectral brightness (centroid-based)
    try:
        centroid = es.SpectralCentroidTime()
        brightness_val = centroid(audio)
        # Normalize: typical centroid for music 1000-8000 Hz at 16kHz SR
        features.brightness = round(min(1.0, max(0.0, float(brightness_val) / 8000.0)), 3)
    except Exception:
        logger.debug("Brightness extraction failed")

    # Danceability
    try:
        dance = es.Danceability()
        danceability_val, _ = dance(audio)
        features.danceability = round(float(danceability_val), 3)
    except Exception:
        logger.debug("Danceability extraction failed")

    # Key detection
    try:
        key_algo = es.KeyExtractor()
        key, scale, strength = key_algo(audio)
        features.key = str(key)
        features.scale = str(scale)
    except Exception:
        logger.debug("Key extraction failed")

    # Loudness (LUFS approximation via ReplayGain)
    try:
        rg = es.ReplayGain()
        replay_gain = rg(audio)
        features.loudness_lufs = round(float(replay_gain), 1)
    except Exception:
        logger.debug("Loudness extraction failed")

    # Acousticness heuristic (spectral flatness inverse)
    try:
        flatness = es.Flatness()
        frame_gen = es.FrameGenerator(audio, frameSize=2048, hopSize=1024)
        flat_vals = []
        for frame in frame_gen:
            spec = es.Spectrum()(frame)
            if len(spec) > 0:
                flat_vals.append(flatness(spec))
        if flat_vals:
            mean_flat = float(np.mean(flat_vals))
            # High flatness = noise-like (low acousticness), low = tonal (high acousticness)
            features.acousticness = round(1.0 - min(1.0, mean_flat * 5.0), 3)
    except Exception:
        logger.debug("Acousticness extraction failed")

    # Valence proxy (spectral contrast based)
    try:
        sc = es.SpectralContrast(frameSize=2048, sampleRate=16000)
        frame_gen = es.FrameGenerator(audio, frameSize=2048, hopSize=1024)
        contrasts = []
        for frame in frame_gen:
            spec = es.Spectrum()(frame)
            if len(spec) > 0:
                contrast, valley = sc(spec)
                contrasts.append(float(np.mean(contrast)))
        if contrasts:
            mean_contrast = float(np.mean(contrasts))
            # Higher contrast often correlates with brighter, more positive music
            features.valence = round(min(1.0, max(0.0, (mean_contrast + 20.0) / 40.0)), 3)
    except Exception:
        logger.debug("Valence extraction failed")

    # Arousal (energy + tempo composite)
    if features.energy is not None and features.tempo is not None:
        tempo_norm = min(1.0, max(0.0, (features.tempo - 60.0) / 120.0))
        features.arousal = round((features.energy * 0.6 + tempo_norm * 0.4), 3)

    return features


def extract_embedding(audio_bytes: bytes) -> list[float] | None:
    """Extract 128-dim Discogs-EffNet embedding from audio.

    Requires essentia-tensorflow. Returns None if unavailable.

    Args:
        audio_bytes: Raw audio file content.

    Returns:
        List of 128 floats or None.
    """
    if not _check_essentia() or not _check_effnet():
        return None

    import essentia.standard as es
    from essentia.standard import TensorflowPredictEffnetDiscogs

    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        try:
            audio = es.MonoLoader(filename=tmp.name, sampleRate=16000)()
        except Exception:
            return None

    try:
        model = TensorflowPredictEffnetDiscogs(graphFilename="discogs-effnet-bs64.pb")
        embeddings = model(audio)
        if len(embeddings) > 0:
            # Average across time frames to get single 128-dim vector
            avg = np.mean(embeddings, axis=0)
            return [round(float(x), 6) for x in avg]
    except Exception:
        logger.debug("EffNet embedding extraction failed")

    return None


async def analyze_track(
    preview_url: str,
    *,
    extract_embeddings: bool = True,
) -> ExtractedFeatures | None:
    """Full analysis pipeline: download preview → extract features + embedding.

    Args:
        preview_url: URL to audio preview.
        extract_embeddings: Whether to compute EffNet embeddings.

    Returns:
        ExtractedFeatures with all available data, or None on failure.
    """
    audio_bytes = await download_preview(preview_url)
    if audio_bytes is None:
        return None

    features = extract_features(audio_bytes)
    if features is None:
        return None

    if extract_embeddings:
        embedding = extract_embedding(audio_bytes)
        features.embedding = embedding

    return features
