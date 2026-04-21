"""Essentia classifier heads that run on top of a DiscGos-EffNet embedding.

No extra model loading beyond what essentia_extractor already does — these are
tiny FC layers reading the same 1,280-dim (or 128-dim) embedding vector.

Supported classifiers (PB graph files):
  mood_aggressive   — binary: aggressive vs. not
  mood_happy        — binary: happy vs. not
  mood_party        — binary: party vs. not
  mood_relaxed      — binary: relaxed vs. not
  mood_sad          — binary: sad vs. not
  voice_instrumental — binary: instrumental vs. vocal (output[1] = instrumental prob)
  mood_acoustic     — binary: acoustic vs. not

Models are looked up by matching filename prefix in ``model_dir`` (default /models).
If a model file is missing the corresponding key is absent from the result dict.
If essentia-tensorflow is unavailable the module degrades gracefully and every
call to classify_from_embedding() returns an empty dict.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Availability flag ──────────────────────────────────────────────────────

CLASSIFIERS_AVAILABLE: bool = False

try:
    import essentia.standard  # noqa: F401 — side-effect import to probe availability

    CLASSIFIERS_AVAILABLE = True
except ImportError:
    pass

# ── Known classifier specs ─────────────────────────────────────────────────
# Each entry maps a logical key to:
#   filename_prefix  — matched against files in model_dir (*.pb)
#   output_node      — TF graph output tensor name
#   result_index     — which softmax output index is the "positive" probability
#
# Softmax output convention for binary classifiers:
#   index 0 = NOT the label, index 1 = the label
#   Exception: voice_instrumental — index 1 = instrumental probability
#
_CLASSIFIER_SPECS: dict[str, dict[str, Any]] = {
    "mood_aggressive": {
        "filename_prefix": "mood_aggressive",
        "output_node": "model/Softmax",
        "result_index": 1,
    },
    "mood_happy": {
        "filename_prefix": "mood_happy",
        "output_node": "model/Softmax",
        "result_index": 1,
    },
    "mood_party": {
        "filename_prefix": "mood_party",
        "output_node": "model/Softmax",
        "result_index": 1,
    },
    "mood_relaxed": {
        "filename_prefix": "mood_relaxed",
        "output_node": "model/Softmax",
        "result_index": 1,
    },
    "mood_sad": {
        "filename_prefix": "mood_sad",
        "output_node": "model/Softmax",
        "result_index": 1,
    },
    "voice_instrumental": {
        "filename_prefix": "voice_instrumental",
        "output_node": "model/Softmax",
        "result_index": 1,  # index 1 = instrumental probability
    },
    "mood_acoustic": {
        "filename_prefix": "mood_acoustic",
        "output_node": "model/Softmax",
        "result_index": 1,
    },
    "genre_discogs400": {
        "filename_prefix": "genre_discogs400",
        "output_node": "model/Softmax",
        "result_index": None,  # None = return full vector as dict
    },
}

# ── Model path resolution ──────────────────────────────────────────────────

_DEFAULT_MODEL_DIR = os.environ.get("ESSENTIA_CLASSIFIER_MODEL_DIR", "/models")


def _find_model_file(key: str, model_dir: str) -> Path | None:
    """Locate a .pb file whose name starts with the classifier's filename_prefix."""
    spec = _CLASSIFIER_SPECS.get(key)
    if spec is None:
        return None

    prefix = spec["filename_prefix"]
    model_path = Path(model_dir)

    # Try candidate directories in order of preference
    candidate_dirs = [
        model_path,
        Path(__file__).parents[4] / "models",
        Path.home() / ".cache" / "essentia",
    ]

    for directory in candidate_dirs:
        if not directory.is_dir():
            continue
        for pb_file in directory.glob("*.pb"):
            if pb_file.stem.startswith(prefix):
                return pb_file

    return None


# ── Session cache (lazy, one session per model) ────────────────────────────

_session_cache: dict[str, Any] = {}


def _get_classifier_session(key: str, model_dir: str) -> Any | None:
    """Lazy-load and cache a TensorflowPredict2D session for a classifier."""
    cache_key = f"{key}:{model_dir}"
    if cache_key in _session_cache:
        return _session_cache[cache_key]

    model_file = _find_model_file(key, model_dir)
    if model_file is None:
        _session_cache[cache_key] = None
        return None

    try:
        from essentia.standard import TensorflowPredict2D

        spec = _CLASSIFIER_SPECS[key]
        session = TensorflowPredict2D(
            graphFilename=str(model_file),
            output=spec["output_node"],
        )
        _session_cache[cache_key] = session
        logger.info("Loaded classifier '%s' from %s", key, model_file)
        return session

    except Exception:
        logger.warning("Failed to load classifier '%s'", key, exc_info=True)
        _session_cache[cache_key] = None
        return None


# ── Public API ─────────────────────────────────────────────────────────────


def classify_from_embedding(
    embedding: list[float],
    model_dir: str = _DEFAULT_MODEL_DIR,
) -> dict[str, float]:
    """Run all available Essentia classifier heads on a DiscGos-EffNet embedding.

    The embedding is the 1,280-dim (or 128-dim) vector produced by
    essentia_extractor.extract_all().  Classifiers are tiny FC layers that
    expect this vector as input — no audio re-loading required.

    Args:
        embedding: Float list from the EffNet forward pass.
        model_dir: Directory to scan for .pb classifier model files.

    Returns:
        Dict of classifier_key -> probability (float 0-1).
        Keys present only when the corresponding model file exists.
        Returns empty dict if essentia is unavailable or embedding is empty.

    Example::

        result = classify_from_embedding(embedding)
        # {"mood_aggressive": 0.91, "mood_happy": 0.10, "voice_instrumental": 0.95}
    """
    if not CLASSIFIERS_AVAILABLE:
        return {}

    if not embedding:
        return {}

    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy not available — cannot run classifiers")
        return {}

    # Reshape to 2D array: [1, embedding_dim] (what TensorflowPredict2D expects)
    emb_array = np.array(embedding, dtype=np.float32).reshape(1, -1)

    results: dict[str, float] = {}

    for key in _CLASSIFIER_SPECS:
        session = _get_classifier_session(key, model_dir)
        if session is None:
            continue

        try:
            output = session(emb_array)  # shape: [1, num_classes]

            spec = _CLASSIFIER_SPECS[key]
            result_index = spec["result_index"]

            if result_index is None:
                # Full vector (e.g. genre_discogs400) — skip for now, not a scalar
                continue

            # Grab the single positive-class probability
            prob = float(output[0, result_index])
            results[key] = round(prob, 4)

        except Exception:
            logger.debug("Classifier '%s' inference failed", key, exc_info=True)

    return results
