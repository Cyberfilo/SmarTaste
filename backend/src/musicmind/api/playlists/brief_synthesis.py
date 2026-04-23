"""Synthesize a target-vector for a playlist brief via gpt-5.4.

V 6.441 step 5b of the playlist-chat rework. Turns the user's freeform
brief text + the enrichment (mood tags, tempo, energy, valence, genres)
of the songs they mentioned inline into a structured audio-trait target
that the playlist recommender can optimize against.

Output shape (persisted as `playlist_briefs.target_vector` JSONB):

    {
      "summary":               "one-sentence restatement of the user's ask",
      "mood_tags":             ["driving", "reflective", ...],        # <= 4
      "tempo_target":          118.0,     # bpm, nullable
      "tempo_min":              90.0,     # bpm, nullable
      "tempo_max":             140.0,     # bpm, nullable
      "energy_target":           0.72,    # 0-1, nullable
      "valence_target":          0.35,    # 0-1, nullable
      "danceability_target":     0.55,    # 0-1, nullable
      "genre_emphasis":        ["italian rap", "hip-hop"],            # <= 5
      "avoid_tags":            ["overly-aggressive", "commercial"],   # <= 4
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa

from musicmind.db.schema import audio_features_global, global_song_cache
from musicmind.engine.mood_tagger import MOOD_TAGS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""You help build music playlists. Given a user's
freeform description of what they want in a playlist + a few reference
songs they called out (with enrichment data), output a STRUCTURED target
vector the recommender will use to find matching tracks.

Reference songs come with their audio features (tempo bpm, energy 0-1,
valence 0-1 where 1=positive) and mood tags from a 12-tag taxonomy:
{", ".join(sorted(MOOD_TAGS))}.

RULES:
1. Output STRICTLY the JSON schema requested.
2. `mood_tags` MUST be drawn from the 12-tag taxonomy above — no synonyms.
3. Numeric targets (tempo, energy, valence, danceability) should reflect
   the CENTER of where matching tracks should sit, not min/max. Only
   return values you can justify from the brief + references.
4. `tempo_min` / `tempo_max` together define the acceptable BPM band.
   If the user is explicit ("around 120 bpm", "fast workout energy"),
   set both. Otherwise leave null.
5. `genre_emphasis` should be specific and useful (e.g. "italian rap",
   "boom bap", "synthwave"). Avoid too-broad tags ("music", "pop").
6. `avoid_tags` captures what the user explicitly doesn't want. Leave
   empty if nothing was ruled out.
7. `summary` is a one-sentence restatement in English of what the user
   asked for — shown back to the user for confirmation.
8. If the user's request is vague, infer from the reference songs'
   enrichment. If there are no references and the brief is vague,
   output conservative nulls for numerics and a reasonable mood guess.
"""

TARGET_VECTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "mood_tags": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(MOOD_TAGS)},
            "maxItems": 4,
        },
        "tempo_target": {"type": ["number", "null"]},
        "tempo_min": {"type": ["number", "null"]},
        "tempo_max": {"type": ["number", "null"]},
        "energy_target": {"type": ["number", "null"], "minimum": 0.0, "maximum": 1.0},
        "valence_target": {"type": ["number", "null"], "minimum": 0.0, "maximum": 1.0},
        "danceability_target": {
            "type": ["number", "null"], "minimum": 0.0, "maximum": 1.0,
        },
        "genre_emphasis": {
            "type": "array", "items": {"type": "string"}, "maxItems": 5,
        },
        "avoid_tags": {
            "type": "array", "items": {"type": "string"}, "maxItems": 4,
        },
    },
    # Strict mode requires every property in `required`.
    "required": [
        "summary", "mood_tags", "tempo_target", "tempo_min", "tempo_max",
        "energy_target", "valence_target", "danceability_target",
        "genre_emphasis", "avoid_tags",
    ],
    "additionalProperties": False,
}


async def _load_mention_enrichment(
    engine, mentioned_songs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pull name/artist/genres/mood_tags + audio features for each mention.

    Matches via `catalog_id` in global_song_cache (primary) with ISRC join
    into audio_features_global (scalars). Unmatched mentions pass through
    with whatever metadata was supplied in the request body — the model
    still sees the title + role + reason even without DB enrichment.
    """
    if not mentioned_songs:
        return []

    cids = [s["catalog_id"] for s in mentioned_songs if s.get("catalog_id")]
    isrcs = [s["isrc"] for s in mentioned_songs if s.get("isrc")]

    rows_by_cid: dict[str, Any] = {}
    rows_by_isrc: dict[str, Any] = {}
    if cids or isrcs:
        async with engine.begin() as conn:
            if cids:
                res = await conn.execute(
                    sa.select(
                        global_song_cache.c.catalog_id,
                        global_song_cache.c.isrc,
                        global_song_cache.c.name,
                        global_song_cache.c.artist_name,
                        global_song_cache.c.genre_names,
                        global_song_cache.c.mood_tags,
                    ).where(global_song_cache.c.catalog_id.in_(cids))
                )
                for r in res:
                    rows_by_cid[r.catalog_id] = r
                    if r.isrc:
                        rows_by_isrc[r.isrc] = r

            all_isrcs = set(isrcs) | {
                r.isrc for r in rows_by_cid.values() if r.isrc
            }
            af_by_isrc: dict[str, Any] = {}
            if all_isrcs:
                res = await conn.execute(
                    sa.select(
                        audio_features_global.c.isrc,
                        audio_features_global.c.tempo,
                        audio_features_global.c.energy,
                        audio_features_global.c.valence_proxy,
                        audio_features_global.c.danceability,
                    ).where(
                        audio_features_global.c.isrc.in_(all_isrcs)
                    )
                )
                for r in res:
                    af_by_isrc[r.isrc] = r

    out: list[dict[str, Any]] = []
    for s in mentioned_songs:
        cid = s.get("catalog_id")
        isrc = s.get("isrc")
        row = rows_by_cid.get(cid) if cid else None
        if row is None and isrc:
            row = rows_by_isrc.get(isrc)
        af = af_by_isrc.get((row.isrc if row else isrc) or "")

        name = row.name if row else ""
        artist = row.artist_name if row else ""
        genres = row.genre_names if row else []
        if isinstance(genres, str):
            try:
                genres = json.loads(genres)
            except (json.JSONDecodeError, TypeError):
                genres = []
        moods = row.mood_tags if row else []
        if isinstance(moods, str):
            try:
                moods = json.loads(moods)
            except (json.JSONDecodeError, TypeError):
                moods = []

        out.append({
            "catalog_id": cid,
            "name": name or "",
            "artist": artist or "",
            "role": s.get("role") or "referenced",
            "reason_text": s.get("reason_text") or "",
            "genre_names": genres or [],
            "mood_tags": moods or [],
            "tempo": float(af.tempo) if af and af.tempo is not None else None,
            "energy": float(af.energy) if af and af.energy is not None else None,
            "valence": (
                float(af.valence_proxy)
                if af and af.valence_proxy is not None else None
            ),
            "danceability": (
                float(af.danceability)
                if af and af.danceability is not None else None
            ),
        })
    return out


def _format_user_prompt(
    brief_text: str, enriched: list[dict[str, Any]],
) -> str:
    """Format the user-visible prompt from the brief + enriched mentions."""
    lines: list[str] = [f"USER'S REQUEST:\n{brief_text.strip()}"]
    if enriched:
        lines.append("\nREFERENCE SONGS:")
        for i, s in enumerate(enriched, 1):
            parts: list[str] = []
            name = s["name"] or "<unknown>"
            artist = s["artist"] or "<unknown>"
            parts.append(f'{i}. "{name}" — {artist}')
            tags: list[str] = []
            if s["genre_names"]:
                tags.append("genres=" + ", ".join(s["genre_names"][:3]))
            if s["mood_tags"]:
                tags.append("moods=" + ", ".join(s["mood_tags"][:3]))
            if s["tempo"] is not None:
                tags.append(f"tempo={s['tempo']:.0f}bpm")
            if s["energy"] is not None:
                tags.append(f"energy={s['energy']:.2f}")
            if s["valence"] is not None:
                tags.append(f"valence={s['valence']:.2f}")
            if s["danceability"] is not None:
                tags.append(f"danceability={s['danceability']:.2f}")
            if tags:
                parts.append("   [" + " ".join(tags) + "]")
            if s["role"] != "referenced":
                parts.append(f"   role: {s['role']}")
            if s["reason_text"]:
                parts.append(f'   user note: "{s["reason_text"]}"')
            lines.append("\n".join(parts))
    lines.append("\nReturn JSON only.")
    return "\n".join(lines)


async def synthesize_target_vector(
    engine,
    *,
    api_key: str,
    brief_text: str,
    mentioned_songs: list[dict[str, Any]],
    model: str = "gpt-5.4",
    timeout: float = 30.0,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run the synthesis. Returns `(target_vector, error_msg)`.

    `target_vector` is None on any failure (import, network, schema mismatch,
    JSON decode). `error_msg` is a short human-readable hint to persist to
    `playlist_briefs.synthesis_error` for diagnostics; None on success.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None, "openai package not installed"

    enriched = await _load_mention_enrichment(engine, mentioned_songs)
    user_prompt = _format_user_prompt(brief_text, enriched)

    client = AsyncOpenAI(api_key=api_key, timeout=timeout)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "playlist_target_vector",
                    "strict": True,
                    "schema": TARGET_VECTOR_SCHEMA,
                },
            },
        )
    except Exception as e:
        logger.warning("Brief synthesis failed: %s", e, exc_info=True)
        return None, str(e)[:500]

    content = resp.choices[0].message.content
    if not content:
        return None, "empty response from model"
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError) as e:
        return None, f"non-json response: {e}"

    return data, None
