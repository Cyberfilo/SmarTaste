"""Local audio analysis via Essentia + ONNX — replaces ReccoBeats/SoundStat.

Single-responsibility: audio bytes in -> features + embedding out.
No DB, no network — pure extraction.

Pipeline per track (~3-5s on CPU):
  1. Write bytes to tempfile (.m4a)
  2. MonoLoader(sampleRate=16000) -> DSP scalars (BPM, key, energy, etc.)
  3. ONNX EffNet forward pass -> 1,280-dim embedding
  4. Optional classifier heads on embedding (mood, acousticness, etc.)
  5. Cleanup tempfile

Models loaded lazily on first call and cached in module-level variables.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from musicmind.engine.audio.models import ExtractedFeatures

logger = logging.getLogger(__name__)

# ── Availability flags ──────────────────────────────────────────────────

_essentia_available: bool | None = None
_onnx_available: bool | None = None


def _check_essentia() -> bool:
    try:
        import essentia.standard  # noqa: F401
        return True
    except ImportError:
        return False


def _check_onnx() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False



def is_essentia_available() -> bool:
    global _essentia_available
    if _essentia_available is None:
        _essentia_available = _check_essentia()
    return _essentia_available


def is_onnx_available() -> bool:
    global _onnx_available
    if _onnx_available is None:
        _onnx_available = _check_onnx()
    return _onnx_available


# ── Model loading (lazy, cached) ────────────────────────────────────────

ESSENTIA_MODEL_PATH = os.environ.get(
    "ESSENTIA_MODEL_PATH",
    "/models/discogs-effnet-bsdynamic-1.onnx",
)

_onnx_session = None


def _get_onnx_session():
    """Lazy-load the ONNX EffNet model. Cached after first call."""
    global _onnx_session
    if _onnx_session is not None:
        return _onnx_session

    if not is_onnx_available():
        return None

    model_path = ESSENTIA_MODEL_PATH
    if not Path(model_path).exists():
        # Try relative paths for local dev
        candidates = [
            Path(__file__).parents[4] / "models" / "discogs-effnet-bsdynamic-1.onnx",
            Path.home() / ".cache" / "essentia" / "discogs-effnet-bsdynamic-1.onnx",
        ]
        for p in candidates:
            if p.exists():
                model_path = str(p)
                break
        else:
            logger.warning("ONNX model not found at %s", ESSENTIA_MODEL_PATH)
            return None

    import onnxruntime as ort

    # Pin to 1 thread per inference — with CONCURRENCY=25 async tasks,
    # each fighting for 8 vCPU causes contention. 1 thread × 25 concurrent
    # gives better aggregate throughput than 8 threads × 15 concurrent.
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1

    _onnx_session = ort.InferenceSession(
        model_path,
        sess_options=opts,
        providers=["CPUExecutionProvider"],
    )
    logger.info("Loaded ONNX EffNet model from %s", model_path)
    return _onnx_session


# ── Core extraction ─────────────────────────────────────────────────────


def extract_all(
    audio_bytes: bytes,
) -> tuple[ExtractedFeatures, list[float] | None]:
    """Extract audio features + 1,280-dim embedding from raw audio bytes.

    Writes to a tempfile (Essentia's MonoLoader needs a file path),
    runs DSP algorithms and ONNX inference, then cleans up.

    Args:
        audio_bytes: Raw M4A/MP3 audio data (typically a 30s preview).

    Returns:
        Tuple of (ExtractedFeatures, embedding_vector).
        Embedding is None if ONNX model unavailable.
        Features may have None fields if extraction partially fails.
    """
    if not is_essentia_available():
        logger.warning("Essentia not available — returning empty features")
        return ExtractedFeatures(), None

    import essentia.standard as es

    # Write to temp file
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)

    try:
        # Load audio at 16kHz mono (for DSP features)
        audio = es.MonoLoader(filename=str(tmp_path), sampleRate=16000)()

        if len(audio) < 1600:  # Less than 0.1s
            logger.warning("Audio too short for analysis (%d samples)", len(audio))
            return ExtractedFeatures(), None

        # ── DSP scalar features ─────────────────────────────────
        features = _extract_dsp_features(es, audio)

        # ── 1,280-dim EffNet embedding via ONNX ─────────────────
        embedding = _extract_embedding_onnx(tmp_path)

        # ── TensorFlow fallback (if ONNX unavailable but TF is) ─
        if embedding is None:
            embedding = _extract_embedding_tf(es, tmp_path)

        # ── Classifier heads on embedding ────────────────────────
        if embedding is not None:
            from musicmind.engine.audio.classifiers import (
                CLASSIFIERS_AVAILABLE,
                classify_from_embedding,
            )

            if CLASSIFIERS_AVAILABLE:
                classifier_results = classify_from_embedding(embedding)
                if classifier_results:
                    features.mood_aggressive = classifier_results.get("mood_aggressive")
                    features.mood_happy = classifier_results.get("mood_happy")
                    features.mood_party = classifier_results.get("mood_party")
                    features.mood_relaxed = classifier_results.get("mood_relaxed")
                    features.mood_sad = classifier_results.get("mood_sad")
                    features.voice_instrumental = classifier_results.get("voice_instrumental")
                    features.mood_acoustic = classifier_results.get("mood_acoustic")

        return features, embedding

    except Exception:
        logger.exception("Essentia extraction failed")
        return ExtractedFeatures(), None

    finally:
        tmp_path.unlink(missing_ok=True)


def _extract_dsp_features(es: Any, audio: Any) -> ExtractedFeatures:
    """Extract DSP scalar features from audio signal.

    Reuses the proven algorithms from analyzer.py.
    """
    try:
        # BPM
        rhythm = es.RhythmExtractor2013(method="multifeature")
        bpm, beats, beats_confidence, _, _ = rhythm(audio)

        # Key and scale
        key_extractor = es.KeyExtractor()
        key, scale, key_strength = key_extractor(audio)

        # Energy (RMS-based, normalized to ~0-1)
        rms = es.RMS()
        energy_raw = float(rms(audio))
        energy = min(1.0, energy_raw * 10)

        # Danceability
        dance = es.Danceability()
        danceability_raw, _ = dance(audio)

        # Spectral centroid (brightness proxy, normalized)
        centroid = es.SpectralCentroidTime()
        brightness_raw = float(centroid(audio))
        brightness = min(1.0, brightness_raw / 8000)

        # Beat strength
        beat_strength = (
            float(beats_confidence.mean()) if len(beats_confidence) > 0 else 0.5
        )

        # Loudness (EBUR128)
        loudness_lufs = None
        try:
            loudness = es.LoudnessEBUR128()
            integrated, _, _, _ = loudness(audio)
            loudness_lufs = round(float(integrated), 1)
        except Exception:
            pass

        return ExtractedFeatures(
            tempo=round(float(bpm), 1),
            energy=round(energy, 3),
            danceability=round(float(danceability_raw), 3),
            brightness=round(brightness, 3),
            beat_strength=round(beat_strength, 3),
            key=key,
            scale=scale,
            loudness_lufs=loudness_lufs,
        )

    except Exception:
        logger.warning("DSP feature extraction failed", exc_info=True)
        return ExtractedFeatures()


def _extract_embedding_onnx(tmp_path: Path) -> list[float] | None:
    """Extract 1,280-dim EffNet embedding via ONNX Runtime.

    Uses mel-spectrogram preprocessing compatible with the Discogs-EffNet model.
    Returns None if ONNX model unavailable.
    """
    session = _get_onnx_session()
    if session is None:
        return None

    try:
        import essentia.standard as es
        import numpy as np

        audio = es.MonoLoader(filename=str(tmp_path), sampleRate=16000)()

        # Compute mel spectrogram (96 bands, matching EffNet training)
        w = es.Windowing(type="hann", size=512)
        spectrum = es.Spectrum(size=512)
        mel = es.MelBands(
            numberBands=96,
            sampleRate=16000,
            lowFrequencyBound=0,
            highFrequencyBound=8000,
        )

        # Frame-by-frame mel extraction
        mel_frames = []
        frame_size = 512
        hop_size = 256
        for i in range(0, len(audio) - frame_size, hop_size):
            frame = audio[i:i + frame_size]
            windowed = w(frame)
            spec = spectrum(windowed)
            mel_band = mel(spec)
            mel_frames.append(mel_band)

        if not mel_frames:
            return None

        mel_array = np.array(mel_frames, dtype=np.float32)

        # Log-compress (standard for EffNet)
        mel_array = np.log1p(mel_array * 10000)

        # EffNet expects [batch, 1, time, mel_bands]
        mel_input = mel_array[np.newaxis, np.newaxis, :, :]

        # Run ONNX inference
        input_name = session.get_inputs()[0].name
        output_name = session.get_outputs()[0].name
        result = session.run([output_name], {input_name: mel_input})

        embeddings = result[0]  # Shape: [1, embedding_dim] or [batch, embedding_dim]

        if embeddings.shape[-1] >= 128:
            # Average pool if multiple frames
            if len(embeddings.shape) > 2:
                embedding = embeddings.mean(axis=1)[0]
            elif len(embeddings.shape) == 2:
                embedding = embeddings[0]
            else:
                embedding = embeddings

            return [round(float(v), 6) for v in embedding]

    except Exception:
        logger.debug("ONNX EffNet extraction failed", exc_info=True)

    return None


def _extract_embedding_tf(es: Any, tmp_path: Path) -> list[float] | None:
    """Fallback: extract embedding via Essentia's TensorFlow API.

    Used when ONNX model is unavailable but essentia-tensorflow is installed.
    Returns 128 or 1,280 dims depending on the model file available.
    """
    try:
        from essentia.standard import (
            MonoLoader,
            TensorflowPredictEffnetDiscogs,
        )

        # EffNet needs 44.1kHz
        audio_44k = MonoLoader(filename=str(tmp_path), sampleRate=44100)()

        # Try 1280-dim model first, fall back to 128-dim
        model_candidates = [
            ("discogs-effnet-bs64-1.pb", "PartitionedCall:1"),  # 128-dim
        ]

        for graph_file, output_node in model_candidates:
            try:
                model = TensorflowPredictEffnetDiscogs(
                    graphFilename=graph_file,
                    output=output_node,
                )
                embeddings = model(audio_44k)
                if len(embeddings) > 0:
                    embedding = [
                        round(float(v), 6) for v in embeddings.mean(axis=0)
                    ]
                    logger.debug(
                        "TF EffNet: %d-dim embedding extracted", len(embedding),
                    )
                    return embedding
            except Exception:
                continue

    except ImportError:
        pass
    except Exception:
        logger.debug("TF EffNet extraction failed", exc_info=True)

    return None
