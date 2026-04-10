"""AI-powered track descriptions and recommendation explanations.

Uses OpenAI GPT-5.4 to generate:
1. Track captions from structured audio features
2. "Why you'll like this" recommendation explanations

Requires MUSICMIND_OPENAI_API_KEY env var. Gracefully returns None if unavailable.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def generate_track_caption(
    tags: dict[str, Any],
    api_key: str,
) -> str | None:
    """Generate a natural language track description from structured audio tags.

    Input: {bpm, key, scale, energy, danceability, moods: {}, genres: {}, ...}
    Output: "Dark Italian drill track at 142 BPM in C minor. Aggressive atmosphere..."

    Cost: ~$0.001 per caption with GPT-5.4
    """
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)

        # Build structured prompt from tags
        parts = []
        if tags.get("tempo"):
            parts.append(f"BPM: {tags['tempo']}")
        if tags.get("key") and tags.get("scale"):
            parts.append(f"Key: {tags['key']} {tags['scale']}")
        if tags.get("energy") is not None:
            parts.append(f"Energy: {tags['energy']:.2f}")
        if tags.get("danceability") is not None:
            parts.append(f"Danceability: {tags['danceability']:.2f}")
        if tags.get("brightness") is not None:
            parts.append(f"Brightness: {tags['brightness']:.2f}")

        # Mood scores
        moods = {}
        for mood_key in ["mood_aggressive", "mood_happy", "mood_party", "mood_relaxed", "mood_sad"]:
            val = tags.get(mood_key)
            if val is not None and val > 0.3:
                moods[mood_key.replace("mood_", "")] = round(val, 2)
        if moods:
            parts.append(f"Moods: {moods}")

        # Genre info
        genres = tags.get("genre_names") or tags.get("genres") or tags.get("genre_tags")
        if genres:
            if isinstance(genres, list):
                parts.append(f"Genres: {', '.join(genres[:5])}")
            elif isinstance(genres, dict):
                top = sorted(genres.items(), key=lambda x: -x[1])[:5]
                parts.append(f"Genres: {', '.join(f'{k} ({v:.0%})' for k, v in top)}")

        if tags.get("voice_instrumental") is not None:
            vi = tags["voice_instrumental"]
            parts.append(f"Vocal: {'instrumental' if vi < 0.3 else 'has vocals'}")

        feature_text = "\n".join(parts)

        resp = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[{
                "role": "user",
                "content": (
                    "Describe this song in 2-3 concise sentences based on these audio features. "
                    "Be specific about production style, atmosphere, and musical characteristics. "
                    "Don't hedge or use qualifiers like 'appears to be'.\n\n"
                    f"{feature_text}"
                ),
            }],
            max_tokens=150,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()

    except ImportError:
        logger.debug("openai package not installed")
        return None
    except Exception:
        logger.debug("Track caption generation failed", exc_info=True)
        return None


async def generate_recommendation_explanation(
    seed_tags: dict[str, Any],
    candidate_tags: dict[str, Any],
    seed_name: str,
    seed_artist: str,
    candidate_name: str,
    candidate_artist: str,
    api_key: str,
) -> str | None:
    """Generate a "why you'll like this" explanation for a recommendation.

    Compares two tracks' features and generates a natural language explanation
    of why they're similar and why the user would enjoy the recommendation.

    Cost: ~$0.001 per explanation with GPT-5.4
    """
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)

        # Find shared attributes
        shared = []

        # BPM similarity
        bpm_a = seed_tags.get("tempo")
        bpm_b = candidate_tags.get("tempo")
        if bpm_a and bpm_b and abs(bpm_a - bpm_b) < 15:
            shared.append(f"similar tempo (~{int(bpm_b)} BPM)")

        # Key compatibility
        key_a = seed_tags.get("key")
        key_b = candidate_tags.get("key")
        if key_a and key_b and key_a == key_b:
            scale = candidate_tags.get("scale", "")
            shared.append(f"same key ({key_b} {scale})")

        # Mood overlap
        for mood in ["aggressive", "happy", "party", "relaxed", "sad"]:
            val_a = seed_tags.get(f"mood_{mood}", 0)
            val_b = candidate_tags.get(f"mood_{mood}", 0)
            if val_a > 0.5 and val_b > 0.5:
                shared.append(f"both {mood}")

        # Energy similarity
        e_a = seed_tags.get("energy", 0)
        e_b = candidate_tags.get("energy", 0)
        if e_a and e_b and abs(e_a - e_b) < 0.2:
            level = "high" if e_b > 0.7 else "moderate" if e_b > 0.4 else "low"
            shared.append(f"{level} energy")

        shared_text = ", ".join(shared) if shared else "similar overall vibe"

        resp = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[{
                "role": "user",
                "content": (
                    f"Write ONE sentence explaining why someone who likes "
                    f'"{seed_name}" by {seed_artist} would also enjoy '
                    f'"{candidate_name}" by {candidate_artist}. '
                    f"They share: {shared_text}. "
                    f"Be specific and musical. No hedging."
                ),
            }],
            max_tokens=80,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()

    except ImportError:
        logger.debug("openai package not installed")
        return None
    except Exception:
        logger.debug("Recommendation explanation failed", exc_info=True)
        return None
