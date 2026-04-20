"""OpenAI-backed structured mood classifier (V 6.388 → V 6.389 hybrid).

The scoring engine's CLAP/MERT/EffNet dimensions capture timbre and
production style but not emotional affect — a reflective Italian rap
track and a hype Italian rap track share instrumentation, tempo and
vocal style, so CLAP sees them as neighbors even when the mood is
opposite.

V 6.389 upgrades the classifier to a **hybrid output**:
- `primary`: a single enum tag (for UI display / logging)
- `moods`: a sparse {tag: score_0_to_1} vector (for the mood_match cosine)

The score vector carries intensity information that the discrete 1-3-tag
list was quantizing away. Cosine similarity between the user's mood
distribution and the candidate's score vector is now the same mathematical
operation used for CLAP/MERT/EffNet, but in a human-interpretable 12-dim
semantic space.

All results are persisted to `global_song_cache.mood_tags` (primary-first
list) and `global_song_cache.mood_scores` (sparse dict) so both the UI
and the scorer get what they need without recomputation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Fixed taxonomy ──────────────────────────────────────────────────────────
# These 12 tags are the entire allowed output space. Both the system prompt
# AND the JSON schema enum enforce this — the model cannot invent new tags.

MOOD_TAGS: list[str] = [
    "happy",
    "sad",
    "melancholic",
    "reflective",
    "chill",
    "nostalgic",
    "romantic",
    "energetic",
    "hype",
    "anthemic",
    "aggressive",
    "dark",
]

# ── Prompt ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a music mood classifier. For each track in the \
user message, output:

  - `primary`: the single most-defining mood tag (from the 12-tag taxonomy).
  - `moods`: a COMPLETE object with a score for ALL 12 mood tags. Score \
0.0 means the mood does not apply; 1.0 means the mood defines the track. \
Use 0.0 for inapplicable moods; use values in (0, 1] only for moods that \
genuinely color the track. The `primary` mood must correspond to the \
HIGHEST score. Scores should reflect RELATIVE strength within this track.

TAXONOMY (exactly these 12 tags, lowercase, no synonyms):

- happy: upbeat, joyful, celebratory, bright affect
- sad: sorrowful, heartbroken, grieving, low valence
- melancholic: bittersweet, yearning, wistful — sadness with tenderness
- reflective: introspective, thoughtful, contemplative, often low-to-mid energy
- chill: relaxed, mellow, laid-back, uncomplicated calm
- nostalgic: throwback, memory-laden, "yesteryear" feel
- romantic: love, longing, affection, intimacy
- energetic: high-energy, kinetic, driving — without being angry
- hype: pump-up, confidence-booster, "gasa" energy, flex/swagger, braggadocio
- anthemic: stadium feel, big chorus, unifying, heroic
- aggressive: intense, hard, angry, forceful, combative
- dark: moody, sinister, menacing, ominous

CALIBRATION GUIDE (avoid extremes; prefer graded values):

- 0.0 = mood is absent / irrelevant to the track
- 0.2-0.3 = a faint colour (minor undertone)
- 0.4-0.6 = clearly present secondary mood
- 0.7-0.8 = strong supporting mood
- 0.9-1.0 = the defining mood (matches `primary`)

Example for a confident trap banger ("Shotta 2" by Baby Gang style):
  primary = "hype"
  moods   = {hype: 0.95, energetic: 0.7, aggressive: 0.5, anthemic: 0.4,
             dark: 0.25, happy: 0.2, chill: 0.0, reflective: 0.0,
             melancholic: 0.0, sad: 0.0, nostalgic: 0.0, romantic: 0.0}

Example for a reflective Italian rap track ("Estate in città" style):
  primary = "reflective"
  moods   = {reflective: 0.9, melancholic: 0.7, nostalgic: 0.5, sad: 0.4,
             chill: 0.25, romantic: 0.2, dark: 0.15, hype: 0.0,
             happy: 0.0, energetic: 0.0, anthemic: 0.0, aggressive: 0.0}

RULES:
1. Output STRICTLY the JSON schema requested.
2. ALL 12 mood keys must be present in `moods` (set inapplicable to 0.0).
3. `primary` MUST equal the tag with the highest `moods` score.
4. Use only the 12 tags above — no synonyms, no translations, no additions.
5. Base judgment on: artist reputation + track title semantics + provided \
genre tags + scalar audio features (tempo BPM, energy 0-1 where 1=high, \
valence 0-1 where 1=positive).
6. For rap/hip-hop: distinguish hype (confident, flex, adrenaline) from \
reflective (introspective, narrative, low-valence) from aggressive \
(combative, angry). Do not default to hype just because it's a rap track.
7. Low valence (<0.4) + mid tempo usually → reflective / melancholic / sad \
as primary, with hype and anthemic near 0. High valence + high energy → \
happy / hype / anthemic / energetic.
8. No hedging explanations. Return ONLY the JSON object.
"""

# JSON schema with strict=True. OpenAI structured outputs require every
# property to appear in `required` and `additionalProperties` to be false.
# That means we declare all 12 mood keys as required numbers; the model
# sets irrelevant ones to 0.0.
_MOODS_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        tag: {"type": "number", "minimum": 0.0, "maximum": 1.0}
        for tag in MOOD_TAGS
    },
    "required": list(MOOD_TAGS),
    "additionalProperties": False,
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "catalog_id": {"type": "string"},
                    "primary": {"type": "string", "enum": MOOD_TAGS},
                    "moods": _MOODS_OBJECT_SCHEMA,
                },
                "required": ["catalog_id", "primary", "moods"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}

# 30 tracks per call: ~5k input tokens (well below limits) and amortizes
# the taxonomy / calibration guide across a full batch. Output is larger
# now (~12 scores per track) but still cheap.
BATCH_SIZE = 30


def _format_track_line(track: dict[str, Any]) -> str:
    """Render one track as a compact one-liner for the user message."""
    cid = track.get("catalog_id", "")
    name = (track.get("name") or "").strip()
    artist = (track.get("artist") or "").strip()
    genres = track.get("genres") or []
    if isinstance(genres, str):
        genres = [genres]
    genre_str = ", ".join(genres[:3])

    parts = [f'{cid}: "{name}" by {artist}']
    if genre_str:
        parts.append(f"genres=[{genre_str}]")
    scalars = []
    tempo = track.get("tempo")
    energy = track.get("energy")
    valence = track.get("valence")
    if isinstance(tempo, (int, float)):
        scalars.append(f"tempo={tempo:.0f}")
    if isinstance(energy, (int, float)):
        scalars.append(f"energy={energy:.2f}")
    if isinstance(valence, (int, float)):
        scalars.append(f"valence={valence:.2f}")
    if scalars:
        parts.append("[" + ", ".join(scalars) + "]")
    return " ".join(parts)


def _sparse_scores(full_scores: dict[str, float]) -> dict[str, float]:
    """Drop zeros from a dense 12-key score dict for compact storage."""
    return {
        k: round(float(v), 3)
        for k, v in full_scores.items()
        if isinstance(v, (int, float)) and v > 0.0
    }


def _ordered_tags_from_scores(
    scores: dict[str, float], *, max_tags: int = 3, floor: float = 0.1,
) -> list[str]:
    """Return primary-first list of up to `max_tags` moods above `floor`."""
    items = sorted(
        ((k, float(v)) for k, v in scores.items() if v >= floor),
        key=lambda kv: kv[1], reverse=True,
    )
    return [k for k, _ in items[:max_tags]]


async def classify_batch(
    tracks: list[dict[str, Any]],
    *,
    api_key: str,
    model: str = "gpt-5.4",
    timeout: float = 90.0,
) -> dict[str, dict[str, Any]]:
    """Classify a batch of tracks in a single hybrid-structured OpenAI call.

    Args:
        tracks: list of dicts with keys:
            - catalog_id (required, string)
            - name, artist, genres (optional metadata)
            - tempo, energy, valence (optional Essentia scalars)
        api_key: Internal OpenAI key (MUSICMIND_OPENAI_API_KEY).
        model: defaults to "gpt-5.4" per user spec.
        timeout: per-request timeout; 90s accommodates batches of 30 where
            each track contributes 12 scores to the output.

    Returns:
        {catalog_id: {"tags": [primary, ...], "scores": {mood: score, ...}}}
        for each successfully classified track. `tags` is a primary-first
        list of up to 3 moods (for UI / backward-compat); `scores` is the
        sparse non-zero score dict (for the mood_match cosine). Empty dict
        on classification failure.
    """
    if not tracks:
        return {}

    try:
        from openai import AsyncOpenAI  # deferred import — optional dep
    except ImportError:
        logger.warning("openai package not installed — skipping mood classify")
        return {}

    client = AsyncOpenAI(api_key=api_key, timeout=timeout)

    user_msg = (
        "Classify the emotional mood of these tracks. Return ALL 12 scores "
        "per track (zeros for inapplicable moods). Return JSON only.\n\n"
        + "\n".join(f"- {_format_track_line(t)}" for t in tracks)
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "mood_classification_v2",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            },
        )
    except Exception:
        logger.warning(
            "Mood classify batch (%d tracks) failed against %s",
            len(tracks), model, exc_info=True,
        )
        return {}

    content = response.choices[0].message.content
    if not content:
        return {}

    import json
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Mood classify returned non-JSON: %s", content[:200])
        return {}

    out: dict[str, dict[str, Any]] = {}
    valid = set(MOOD_TAGS)
    for item in data.get("results", []):
        cid = item.get("catalog_id")
        primary = item.get("primary")
        moods = item.get("moods") or {}
        if not cid or not isinstance(moods, dict):
            continue
        sparse = _sparse_scores({
            k: v for k, v in moods.items()
            if isinstance(k, str) and k in valid
        })
        if not sparse:
            continue
        tags = _ordered_tags_from_scores(sparse, max_tags=3, floor=0.2)
        # Ensure primary leads the list even if floor pruned ties.
        if (
            primary and primary in valid
            and primary in sparse and primary not in tags
        ):
            tags.insert(0, primary)
        elif primary and tags and tags[0] != primary and primary in sparse:
            tags = [primary, *(t for t in tags if t != primary)][:3]
        if not tags:
            continue
        out[cid] = {"tags": tags, "scores": sparse}
    return out


def aggregate_mood_distribution(
    library_scores: list[dict[str, float] | None],
    library_tags_fallback: list[list[str]] | None = None,
) -> dict[str, float]:
    """Build a normalized mood distribution from a library's mood signals.

    Primary input is per-song sparse `mood_scores` dicts (V 6.389).
    Fallback (for rows tagged pre-V6.389 that only have `mood_tags`):
    positional weighting 0.5/0.3/0.2 on the tag list.

    Returns an L1-normalized distribution over the taxonomy — sums to 1.0
    across non-zero moods. Empty input → empty dict.
    """
    counts: dict[str, float] = {}
    pos_weights = [0.5, 0.3, 0.2]

    for i, scores in enumerate(library_scores):
        if scores and isinstance(scores, dict):
            for tag, val in scores.items():
                if tag in MOOD_TAGS:
                    counts[tag] = counts.get(tag, 0.0) + float(val)
        elif library_tags_fallback and i < len(library_tags_fallback):
            tags = library_tags_fallback[i] or []
            for j, tag in enumerate(tags[:3]):
                if tag in MOOD_TAGS:
                    counts[tag] = counts.get(tag, 0.0) + pos_weights[j]

    total = sum(counts.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def mood_similarity(
    user_dist: dict[str, float] | None,
    candidate_scores: dict[str, float] | None = None,
    candidate_tags: list[str] | None = None,
) -> float:
    """Cosine similarity between user mood distribution and candidate.

    Preferred input: `candidate_scores` (V 6.389 sparse score vector).
    Fallback: `candidate_tags` with positional weights 0.5/0.3/0.2 for
    rows classified before V 6.389 or when the scores column is missing.

    Returns 0.0 when either input is missing — the caller's weight
    redistributor picks up the slack.
    """
    if not user_dist:
        return 0.0

    cand_raw: dict[str, float] = {}
    if candidate_scores and isinstance(candidate_scores, dict):
        for k, v in candidate_scores.items():
            if k in MOOD_TAGS and isinstance(v, (int, float)) and v > 0:
                cand_raw[k] = float(v)
    elif candidate_tags:
        pos_weights = [0.5, 0.3, 0.2]
        for i, tag in enumerate(candidate_tags[:3]):
            if tag in MOOD_TAGS:
                cand_raw[tag] = cand_raw.get(tag, 0.0) + pos_weights[i]

    total = sum(cand_raw.values())
    if total <= 0:
        return 0.0
    cand_dist = {k: v / total for k, v in cand_raw.items()}

    keys = set(user_dist) | set(cand_dist)
    dot = sum(user_dist.get(k, 0.0) * cand_dist.get(k, 0.0) for k in keys)
    norm_u = sum(v * v for v in user_dist.values()) ** 0.5
    norm_c = sum(v * v for v in cand_dist.values()) ** 0.5
    if norm_u == 0 or norm_c == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_u * norm_c)))
