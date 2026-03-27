"""Claude tool definitions and executor mapping for MusicMind chat.

Defines 8 curated tools that Claude can invoke via tool_use during chat.
Each tool maps to an existing service function (TasteService, RecommendationService,
StatsService). Executors are thin async wrappers that call the appropriate service.

TOOL_DEFINITIONS: list of Anthropic-compatible tool schemas.
TOOL_EXECUTORS: dict mapping tool name -> async executor function.
"""

from __future__ import annotations

from typing import Any

from musicmind.api.recommendations.service import RecommendationService
from musicmind.api.stats.service import StatsService
from musicmind.api.taste.service import TasteService

# ── Shared service instances ─────────────────────────────────────────────────

_taste_service = TasteService()
_recommendation_service = RecommendationService()
_stats_service = StatsService()

# ── Tool Definitions (Anthropic tool_use format) ─────────────────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_taste_profile",
        "description": (
            "Get the user's music taste profile including genre preferences, "
            "top artists, audio trait preferences, and listening patterns. "
            "Use this to understand what kind of music the user likes before "
            "making recommendations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": (
                        "Music service to analyze: 'spotify', 'apple_music', "
                        "or omit for auto-detection (uses unified if both connected)."
                    ),
                    "enum": ["spotify", "apple_music"],
                },
                "force_refresh": {
                    "type": "boolean",
                    "description": (
                        "Bypass 24-hour cache and re-fetch fresh data from the service API. "
                        "Only use if the user explicitly asks for updated data."
                    ),
                    "default": False,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_recommendations",
        "description": (
            "Get personalized music recommendations scored and ranked by the "
            "MusicMind engine. Returns tracks with match scores, explanations, "
            "and genre information. Supports different discovery strategies and "
            "mood filtering."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy": {
                    "type": "string",
                    "description": (
                        "Discovery strategy: 'all' (balanced mix), 'similar_artist' "
                        "(based on favorite artists), 'genre_adjacent' (explore nearby genres), "
                        "'editorial' (from curated playlists), 'chart' (filtered charts)."
                    ),
                    "enum": ["all", "similar_artist", "genre_adjacent", "editorial", "chart"],
                    "default": "all",
                },
                "mood": {
                    "type": "string",
                    "description": (
                        "Filter recommendations by mood: 'workout', 'chill', 'focus', "
                        "'party', 'sad', 'driving', 'energy', 'melancholy'."
                    ),
                    "enum": [
                        "workout", "chill", "focus", "party",
                        "sad", "driving", "energy", "melancholy",
                    ],
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recommendations (1-50).",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_listening_stats_tracks",
        "description": (
            "Get the user's top tracks by listening frequency for a given time period. "
            "Shows which songs the user has been playing most."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Time period: 'month' (last 4 weeks), '6months', or 'alltime'.",
                    "enum": ["month", "6months", "alltime"],
                    "default": "month",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of tracks to return.",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_listening_stats_artists",
        "description": (
            "Get the user's top artists by listening frequency for a given time period. "
            "Shows which artists the user has been listening to most."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Time period: 'month' (last 4 weeks), '6months', or 'alltime'.",
                    "enum": ["month", "6months", "alltime"],
                    "default": "month",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of artists to return.",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_top_genres",
        "description": (
            "Get the user's top genres ranked by how many tracks and artists "
            "belong to each genre. Useful for understanding musical breadth."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Time period: 'month' (last 4 weeks), '6months', or 'alltime'.",
                    "enum": ["month", "6months", "alltime"],
                    "default": "month",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of genres to return.",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": [],
        },
    },
    {
        "name": "give_feedback",
        "description": (
            "Record the user's feedback on a recommended track. This helps the "
            "recommendation engine learn and adapt its scoring weights. Use when "
            "the user says they like or dislike a specific recommendation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "catalog_id": {
                    "type": "string",
                    "description": "The catalog ID of the track to give feedback on.",
                },
                "feedback_type": {
                    "type": "string",
                    "description": "Feedback type: 'thumbs_up', 'thumbs_down', or 'skip'.",
                    "enum": ["thumbs_up", "thumbs_down", "skip"],
                },
            },
            "required": ["catalog_id", "feedback_type"],
        },
    },
    {
        "name": "get_recommendations_by_description",
        "description": (
            "Get music recommendations matching a natural language description. "
            "Interprets descriptions like 'chill beats for studying' or 'upbeat "
            "dance music for a party' and maps them to appropriate mood and strategy "
            "parameters for the recommendation engine."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "Natural language description of the kind of music wanted, "
                        "e.g., 'relaxing acoustic songs', 'high energy workout music'."
                    ),
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "adjust_taste_preferences",
        "description": (
            "Adjust recommendation preferences based on the user's feedback about "
            "their taste. Interprets adjustments like 'less mainstream pop' or 'more "
            "electronic' and returns recommendations reflecting the adjustment. "
            "Useful when the user wants to explore different directions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "adjustment": {
                    "type": "string",
                    "description": (
                        "Natural language adjustment to taste preferences, "
                        "e.g., 'less mainstream pop', 'more electronic', "
                        "'discover more Italian hip-hop'."
                    ),
                },
            },
            "required": ["adjustment"],
        },
    },
]

# ── Tool Executor Functions ──────────────────────────────────────────────────

# Mood keyword mapping for description-based recommendations
_DESCRIPTION_MOOD_MAP: dict[str, str] = {
    "chill": "chill",
    "relax": "chill",
    "calm": "chill",
    "study": "focus",
    "focus": "focus",
    "concentrate": "focus",
    "work": "focus",
    "party": "party",
    "dance": "party",
    "club": "party",
    "workout": "workout",
    "gym": "workout",
    "exercise": "workout",
    "run": "workout",
    "energy": "workout",
    "sad": "sad",
    "melancholy": "sad",
    "emotional": "sad",
    "drive": "driving",
    "road": "driving",
}


def _infer_mood_from_description(description: str) -> str | None:
    """Infer a mood keyword from a natural language description."""
    lower = description.lower()
    for keyword, mood in _DESCRIPTION_MOOD_MAP.items():
        if keyword in lower:
            return mood
    return None


async def _execute_get_taste_profile(
    *, engine, encryption, settings, user_id: str, **kwargs: Any,
) -> dict[str, Any]:
    """Execute get_taste_profile tool."""
    service = kwargs.get("service")
    force_refresh = kwargs.get("force_refresh", False)
    return await _taste_service.get_profile(
        engine, encryption, settings,
        user_id=user_id, service=service, force_refresh=force_refresh,
    )


async def _execute_get_recommendations(
    *, engine, encryption, settings, user_id: str, **kwargs: Any,
) -> dict[str, Any]:
    """Execute get_recommendations tool."""
    strategy = kwargs.get("strategy", "all")
    mood = kwargs.get("mood")
    limit = kwargs.get("limit", 10)
    return await _recommendation_service.get_recommendations(
        engine, encryption, settings,
        user_id=user_id, strategy=strategy, mood=mood, limit=limit,
    )


async def _execute_get_listening_stats_tracks(
    *, engine, encryption, settings, user_id: str, **kwargs: Any,
) -> dict[str, Any]:
    """Execute get_listening_stats_tracks tool."""
    period = kwargs.get("period", "month")
    limit = kwargs.get("limit", 20)
    return await _stats_service.get_top_tracks(
        engine, encryption, settings,
        user_id=user_id, period=period, limit=limit,
    )


async def _execute_get_listening_stats_artists(
    *, engine, encryption, settings, user_id: str, **kwargs: Any,
) -> dict[str, Any]:
    """Execute get_listening_stats_artists tool."""
    period = kwargs.get("period", "month")
    limit = kwargs.get("limit", 20)
    return await _stats_service.get_top_artists(
        engine, encryption, settings,
        user_id=user_id, period=period, limit=limit,
    )


async def _execute_get_top_genres(
    *, engine, encryption, settings, user_id: str, **kwargs: Any,
) -> dict[str, Any]:
    """Execute get_top_genres tool."""
    period = kwargs.get("period", "month")
    limit = kwargs.get("limit", 20)
    return await _stats_service.get_top_genres(
        engine, encryption, settings,
        user_id=user_id, period=period, limit=limit,
    )


async def _execute_give_feedback(
    *, engine, user_id: str, **kwargs: Any,
) -> dict[str, Any]:
    """Execute give_feedback tool."""
    catalog_id = kwargs["catalog_id"]
    feedback_type = kwargs["feedback_type"]
    await _recommendation_service.submit_feedback(
        engine, user_id=user_id, catalog_id=catalog_id, feedback_type=feedback_type,
    )
    return {
        "status": "recorded",
        "catalog_id": catalog_id,
        "feedback_type": feedback_type,
    }


async def _execute_get_recommendations_by_description(
    *, engine, encryption, settings, user_id: str, **kwargs: Any,
) -> dict[str, Any]:
    """Execute get_recommendations_by_description tool.

    Maps the natural language description to a mood parameter and fetches
    recommendations. If no mood can be inferred, uses the default strategy.
    """
    description = kwargs["description"]
    mood = _infer_mood_from_description(description)
    return await _recommendation_service.get_recommendations(
        engine, encryption, settings,
        user_id=user_id, strategy="all", mood=mood, limit=10,
    )


async def _execute_adjust_taste_preferences(
    *, engine, encryption, settings, user_id: str, **kwargs: Any,
) -> dict[str, Any]:
    """Execute adjust_taste_preferences tool.

    Interprets the adjustment text and maps it to a recommendation strategy.
    Uses genre_adjacent strategy for genre exploration, similar_artist for
    artist-based adjustments, and all for general adjustments.
    """
    adjustment = kwargs["adjustment"]
    lower = adjustment.lower()

    # Determine strategy based on adjustment text
    if any(word in lower for word in ("genre", "electronic", "jazz", "rock", "pop", "hip-hop")):
        strategy = "genre_adjacent"
    elif any(word in lower for word in ("artist", "similar", "like")):
        strategy = "similar_artist"
    else:
        strategy = "all"

    mood = _infer_mood_from_description(adjustment)

    return await _recommendation_service.get_recommendations(
        engine, encryption, settings,
        user_id=user_id, strategy=strategy, mood=mood, limit=10,
    )


# ── Executor Mapping ─────────────────────────────────────────────────────────

TOOL_EXECUTORS: dict[str, Any] = {
    "get_taste_profile": _execute_get_taste_profile,
    "get_recommendations": _execute_get_recommendations,
    "get_listening_stats_tracks": _execute_get_listening_stats_tracks,
    "get_listening_stats_artists": _execute_get_listening_stats_artists,
    "get_top_genres": _execute_get_top_genres,
    "give_feedback": _execute_give_feedback,
    "get_recommendations_by_description": _execute_get_recommendations_by_description,
    "adjust_taste_preferences": _execute_adjust_taste_preferences,
}
