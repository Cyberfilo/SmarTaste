"""OpenAI-backed structured mood classifier (V 6.388).

The scoring engine's CLAP/MERT/EffNet dimensions capture timbre and
production style well but cannot reliably separate emotional affect —
"Estate in città" (reflective Italian rap) and "Shotta 2" (hype Italian
rap) share instrumentation, tempo and vocal style, so CLAP sees them as
neighbors even though the mood is opposite.

This module closes the gap with a fixed 12-entry mood taxonomy and
batch-classifies tracks via GPT-5.4 structured outputs. The tagger uses
the internal `MUSICMIND_OPENAI_API_KEY` (not user BYOK) and persists
results to `global_song_cache.mood_tags` so tagging is shared across
users.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Fixed taxonomy ──────────────────────────────────────────────────────────
# These 12 tags are the entire allowed output space. The system prompt +
# structured-output JSON schema both enforce this enum so the model cannot
# invent new tags. Ordering is roughly by perceived energy (calm → intense)
# but the mood semantics, not the position, are what matters.

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

SYSTEM_PROMPT = """You are a music mood classifier. For each track provided \
in the user message, return 1-3 mood tags from the fixed taxonomy below that \
best capture the EMOTIONAL AFFECT of the track — not the genre, not the \
production style. Two tracks in the same genre with opposite valence should \
receive different moods.

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

RULES:
1. Output STRICTLY the JSON schema requested — an array of {catalog_id, moods}.
2. 1-3 moods per track, primary (most defining) tag FIRST.
3. Use only the 12 tags above — no synonyms, no translations, no additions.
4. Base judgment on: artist reputation + track title semantics + provided \
genre tags + scalar audio features (tempo in BPM, energy 0-1 where 1=high, \
valence 0-1 where 1=positive).
5. For rap/hip-hop specifically: distinguish hype (confident, flex, \
adrenaline) from reflective (introspective, narrative, low-valence) from \
aggressive (combative, angry). Hype and reflective are often the MOST \
important distinction for Italian rap and trap — do not default to hype \
just because it's a rap track.
6. Low valence (<0.4) + mid tempo usually → reflective, melancholic, or sad \
(not hype). High valence + high energy → happy, hype, anthemic, or energetic \
depending on genre/title context.
7. Do not add hedging explanations. Return ONLY the JSON.
"""

# JSON schema for structured output. `strict=True` on the response_format
# forces the model to produce exactly this shape.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "catalog_id": {"type": "string"},
                    "moods": {
                        "type": "array",
                        "items": {"type": "string", "enum": MOOD_TAGS},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                },
                "required": ["catalog_id", "moods"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}

# Per-call batch size. 30 tracks per call keeps token count ~4k input, well
# under any rate limit, and lets us drain a 2000-track backfill in ~70 calls.
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


async def classify_batch(
    tracks: list[dict[str, Any]],
    *,
    api_key: str,
    model: str = "gpt-5.4",
    timeout: float = 60.0,
) -> dict[str, list[str]]:
    """Classify a batch of tracks in a single structured OpenAI call.

    Args:
        tracks: list of dicts with keys:
            - catalog_id (required, string)
            - name, artist, genres (optional metadata)
            - tempo, energy, valence (optional Essentia scalars)
        api_key: Internal OpenAI key (MUSICMIND_OPENAI_API_KEY).
        model: defaults to "gpt-5.4" per user spec.
        timeout: per-request timeout; one batch call should complete well
            within a minute.

    Returns:
        {catalog_id: [mood_tag, ...]} — only successfully classified rows.
        Invalid tags silently dropped; empty list if classification failed.
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
        "Classify the emotional mood of these tracks and return JSON:\n\n"
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
                    "name": "mood_classification",
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

    out: dict[str, list[str]] = {}
    valid = set(MOOD_TAGS)
    for item in data.get("results", []):
        cid = item.get("catalog_id")
        moods = item.get("moods", [])
        if not cid or not isinstance(moods, list):
            continue
        clean: list[str] = []
        seen: set[str] = set()
        for m in moods:
            if isinstance(m, str) and m in valid and m not in seen:
                clean.append(m)
                seen.add(m)
            if len(clean) >= 3:
                break
        if clean:
            out[cid] = clean
    return out


def aggregate_mood_distribution(
    library_mood_tags: list[list[str]],
) -> dict[str, float]:
    """Build a normalized probability distribution from a library's mood_tags.

    Positional weighting: primary mood = 0.5, secondary = 0.3, tertiary = 0.2.
    Sum across all library songs, then L1-normalize to a probability
    distribution over the taxonomy. Empty input → empty dict.
    """
    pos_weights = [0.5, 0.3, 0.2]  # primary, secondary, tertiary
    counts: dict[str, float] = {}
    for tags in library_mood_tags:
        if not tags:
            continue
        for i, tag in enumerate(tags[:3]):
            if tag in MOOD_TAGS:
                counts[tag] = counts.get(tag, 0.0) + pos_weights[i]
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def mood_similarity(
    user_dist: dict[str, float] | None,
    candidate_tags: list[str] | None,
) -> float:
    """Cosine similarity between the user's mood distribution and a candidate.

    Candidate tags are converted to a normalized position-weighted
    distribution (same weights as `aggregate_mood_distribution`) so
    single-tag tracks don't sweep the score.

    Returns 0.0 when either input is missing — the caller's weight
    redistributor picks up the slack.
    """
    if not user_dist or not candidate_tags:
        return 0.0
    pos_weights = [0.5, 0.3, 0.2]
    cand_raw: dict[str, float] = {}
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
