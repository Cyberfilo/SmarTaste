"""CLAP mood embeddings — text-to-audio cosine similarity for mood matching.

Replaces categorical mood matching (MOOD_PROFILES) with continuous
similarity between free-text mood descriptions and audio embeddings.
Supports natural language queries like "upbeat workout energy" or
"rainy Sunday chill".

Uses CLAP (Contrastive Language-Audio Pretraining) when available,
falls back to the categorical mood system otherwise.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Lazy-check for CLAP availability
_CLAP_AVAILABLE: bool | None = None
_CLAP_MODEL: Any = None


def _check_clap() -> bool:
    """Check if CLAP model is importable and loadable."""
    global _CLAP_AVAILABLE  # noqa: PLW0603
    if _CLAP_AVAILABLE is None:
        try:
            from msclap import CLAP  # noqa: F401
            _CLAP_AVAILABLE = True
        except ImportError:
            _CLAP_AVAILABLE = False
            logger.info("msclap not available — using categorical mood matching")
    return _CLAP_AVAILABLE


def _get_clap_model() -> Any:
    """Get or initialize the CLAP model singleton."""
    global _CLAP_MODEL  # noqa: PLW0603
    if _CLAP_MODEL is None and _check_clap():
        try:
            from msclap import CLAP
            _CLAP_MODEL = CLAP(version="2023", use_cuda=False)
            logger.info("CLAP model loaded successfully")
        except Exception:
            logger.warning("Failed to load CLAP model")
            return None
    return _CLAP_MODEL


def encode_text(text: str) -> list[float] | None:
    """Encode a text mood description to a 512-dim CLAP embedding.

    Args:
        text: Natural language mood description (e.g., "energetic workout music").

    Returns:
        512-dim embedding vector or None if CLAP unavailable.
    """
    model = _get_clap_model()
    if model is None:
        return None

    try:
        embedding = model.get_text_embeddings([text])
        if embedding is not None and len(embedding) > 0:
            vec = embedding[0]
            if hasattr(vec, "numpy"):
                vec = vec.numpy()
            return [round(float(x), 6) for x in vec]
    except Exception:
        logger.debug("CLAP text encoding failed for '%s'", text[:50])

    return None


def encode_audio(audio_bytes: bytes) -> list[float] | None:
    """Encode audio to a 512-dim CLAP embedding.

    Args:
        audio_bytes: Raw audio file content.

    Returns:
        512-dim embedding vector or None.
    """
    model = _get_clap_model()
    if model is None:
        return None

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        try:
            embedding = model.get_audio_embeddings([tmp.name])
            if embedding is not None and len(embedding) > 0:
                vec = embedding[0]
                if hasattr(vec, "numpy"):
                    vec = vec.numpy()
                return [round(float(x), 6) for x in vec]
        except Exception:
            logger.debug("CLAP audio encoding failed")

    return None


def mood_cosine_similarity(
    text_embedding: list[float] | None,
    audio_embedding: list[float] | None,
) -> float:
    """Cosine similarity between a text mood embedding and audio embedding.

    Returns 0.5 (neutral) when either is None.
    """
    if not text_embedding or not audio_embedding:
        return 0.5
    if len(text_embedding) != len(audio_embedding):
        return 0.5

    a = np.array(text_embedding)
    b = np.array(audio_embedding)
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0 or norm_b == 0:
        return 0.5
    sim = float(np.dot(a, b) / (norm_a * norm_b))
    # Shift from [-1, 1] to [0, 1]
    return (sim + 1.0) / 2.0


# ── Pre-computed text embeddings for standard moods ────────────────────────

# These are computed once and cached in memory for the 6 standard moods
_MOOD_TEXT_CACHE: dict[str, list[float] | None] = {}

MOOD_DESCRIPTIONS: dict[str, str] = {
    "workout": "high energy fast tempo workout exercise music with strong beats",
    "chill": "calm relaxing ambient chill lounge music with soft sounds",
    "focus": "minimal ambient concentration background music without vocals",
    "party": "upbeat dance party electronic pop music with catchy rhythms",
    "sad": "melancholic emotional slow sad music with piano and strings",
    "driving": "energetic road trip driving rock and electronic music",
}


def get_mood_text_embedding(mood: str) -> list[float] | None:
    """Get or compute text embedding for a standard mood.

    Caches results to avoid recomputing CLAP embeddings.
    """
    if mood in _MOOD_TEXT_CACHE:
        return _MOOD_TEXT_CACHE[mood]

    description = MOOD_DESCRIPTIONS.get(mood)
    if description is None:
        # Custom mood query — encode directly
        embedding = encode_text(mood)
        return embedding

    embedding = encode_text(description)
    _MOOD_TEXT_CACHE[mood] = embedding
    return embedding


def score_mood_clap(
    mood_query: str,
    candidate_clap_embedding: list[float] | None,
) -> float:
    """Score how well a candidate matches a mood using CLAP embeddings.

    Falls back to 0.5 (neutral) when CLAP is unavailable.

    Args:
        mood_query: Standard mood name or natural language description.
        candidate_clap_embedding: 512-dim CLAP embedding of the candidate.

    Returns:
        Mood match score 0-1.
    """
    if candidate_clap_embedding is None:
        return 0.5

    text_emb = get_mood_text_embedding(mood_query)
    if text_emb is None:
        return 0.5

    return mood_cosine_similarity(text_emb, candidate_clap_embedding)


def filter_candidates_by_mood_clap(
    candidates: list[dict[str, Any]],
    mood_query: str,
    clap_embeddings_map: dict[str, list[float]] | None = None,
    min_keep_ratio: float = 0.3,
) -> list[dict[str, Any]]:
    """Filter and boost candidates using CLAP mood similarity.

    Replaces categorical mood filtering when CLAP is available.
    Falls back to setting neutral mood_boost when CLAP is unavailable.

    Args:
        candidates: List of candidate dicts.
        mood_query: Mood name or natural language query.
        clap_embeddings_map: Dict of catalog_id -> CLAP embedding.
        min_keep_ratio: Minimum fraction of candidates to keep.

    Returns:
        Filtered and boosted candidate list.
    """
    if not _check_clap() or not clap_embeddings_map:
        # Fall back to neutral — categorical system handles it
        for c in candidates:
            c.setdefault("_mood_boost", 0.0)
        return candidates

    text_emb = get_mood_text_embedding(mood_query)
    if text_emb is None:
        for c in candidates:
            c.setdefault("_mood_boost", 0.0)
        return candidates

    scored: list[tuple[float, dict[str, Any]]] = []
    for c in candidates:
        cid = c.get("catalog_id", "")
        audio_emb = clap_embeddings_map.get(cid)
        match_score = mood_cosine_similarity(text_emb, audio_emb)
        c["_mood_boost"] = round(match_score - 0.5, 3)
        scored.append((match_score, c))

    scored.sort(key=lambda x: x[0], reverse=True)

    min_keep = max(1, int(len(scored) * min_keep_ratio))
    threshold = 0.3
    kept = [c for score, c in scored if score >= threshold]
    if len(kept) < min_keep:
        kept = [c for _, c in scored[:min_keep]]

    return kept
