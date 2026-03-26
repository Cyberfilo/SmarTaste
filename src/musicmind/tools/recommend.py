"""Smart recommendation MCP tools — discovery, smart playlists, and explanations."""

from __future__ import annotations

import logging
import sys
from typing import Any

from musicmind.engine.discovery import (
    chart_filter,
    editorial_mining,
    genre_adjacent_explore,
    similar_artist_crawl,
)
from musicmind.engine.mood import filter_candidates_by_mood
from musicmind.engine.profile import build_audio_centroid, build_taste_profile
from musicmind.engine.scorer import rank_candidates, score_candidate
from musicmind.engine.weights import DEFAULT_WEIGHTS, optimize_weights
from musicmind.server import mcp
from musicmind.tools.helpers import extract_song_cache_data

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))


def _ctx():
    ctx = mcp.get_context()
    lc = ctx.request_context.lifespan_context
    return lc["client"], lc["queries"]


def _get_profile_and_check(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot or not snapshot.get("genre_vector"):
        return None
    return snapshot


async def _get_or_build_profile(queries: Any) -> dict[str, Any] | None:
    """Get latest profile or build one from cached data."""
    snapshot = await queries.get_latest_taste_snapshot()
    profile = _get_profile_and_check(snapshot)
    if not profile:
        songs = await queries.get_all_cached_songs()
        history = await queries.get_listening_history()
        if not songs:
            return None
        profile = build_taste_profile(songs, history, use_temporal_decay=True)
        await queries.save_taste_snapshot(profile)
    return profile


def _tag_strategy(candidates: list[dict[str, Any]], strategy: str) -> None:
    """Tag each candidate with its source strategy."""
    for c in candidates:
        c["_source_strategy"] = strategy


def _count_strategies(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate candidates and count how many strategies found each."""
    by_id: dict[str, dict[str, Any]] = {}
    strategy_counts: dict[str, int] = {}

    for c in candidates:
        cid = c.get("catalog_id", "")
        if not cid:
            continue
        if cid not in by_id:
            by_id[cid] = c
            strategy_counts[cid] = 1
        else:
            strategy_counts[cid] += 1

    for cid, c in by_id.items():
        c["_strategy_count"] = strategy_counts.get(cid, 1)

    return list(by_id.values())


@mcp.tool()
async def musicmind_discover(
    count: int = 15,
    strategy: str = "auto",
    mood: str | None = None,
) -> str:
    """Discover new songs personalized to your taste.

    Uses your taste profile to find and rank songs you'll likely enjoy.
    Supports mood filtering and adaptive scoring from feedback.

    Args:
        count: Number of songs to discover (default 15, max 50)
        strategy: Discovery strategy:
            - "similar_artists" — crawl artists similar to your favorites
            - "genre_adjacent" — explore your top genres in the catalog
            - "editorial" — mine editorial playlists and best-of searches
            - "charts" — filter chart songs by your taste
            - "auto" — combine all strategies (recommended)
        mood: Optional mood filter: "workout", "chill", "focus", "party", "sad", "driving"
    """
    client, queries = _ctx()
    count = min(count, 50)

    profile = await _get_or_build_profile(queries)
    if not profile:
        return (
            "No data to build taste profile. Use `musicmind_library_songs` "
            "and `musicmind_recently_played` first."
        )

    # Collect candidates
    candidates: list[dict[str, Any]] = []
    top_artists = profile.get("top_artists", [])
    seed_ids = []

    cached_artists = await queries.get_all_cached_artists()
    artist_name_to_id = {a["name"].lower(): a["artist_id"] for a in cached_artists}
    for a in top_artists[:5]:
        aid = artist_name_to_id.get(a["name"].lower())
        if aid:
            seed_ids.append(aid)

    strategies_used = []

    if strategy in ("similar_artists", "auto") and seed_ids:
        try:
            new = await similar_artist_crawl(client, queries, seed_ids, depth=1)
            _tag_strategy(new, "similar_artists")
            candidates.extend(new)
            strategies_used.append("similar artists")
        except Exception as e:
            logger.warning("Similar artist crawl failed: %s", e)

    if strategy in ("genre_adjacent", "auto"):
        try:
            new = await genre_adjacent_explore(client, queries, profile)
            _tag_strategy(new, "genre_adjacent")
            candidates.extend(new)
            strategies_used.append("genre exploration")
        except Exception as e:
            logger.warning("Genre exploration failed: %s", e)

    if strategy in ("editorial", "auto"):
        try:
            new = await editorial_mining(client, queries, profile)
            _tag_strategy(new, "editorial")
            candidates.extend(new)
            strategies_used.append("editorial mining")
        except Exception as e:
            logger.warning("Editorial mining failed: %s", e)

    if strategy in ("charts", "auto"):
        try:
            new = await chart_filter(client, queries, profile)
            _tag_strategy(new, "charts")
            candidates.extend(new)
            strategies_used.append("charts")
        except Exception as e:
            logger.warning("Chart filter failed: %s", e)

    if not candidates:
        return "No candidates found. Try populating your cache with more library data first."

    # Deduplicate with cross-strategy counting
    unique = _count_strategies(candidates)

    # Load adaptive weights from feedback
    all_feedback = await queries.get_all_feedback()
    weights = optimize_weights(all_feedback) if all_feedback else dict(DEFAULT_WEIGHTS)

    # Load audio features for candidates
    cids = [c.get("catalog_id", "") for c in unique if c.get("catalog_id")]
    audio_map = await queries.get_audio_features_bulk(cids)

    # Build user audio centroid from cached features
    all_audio = list(audio_map.values())
    centroid = build_audio_centroid(all_audio) if all_audio else None

    # Load recent recommendations for anti-staleness
    recent_recs = await queries.get_recent_recommendations(days=30)

    # Apply mood filter if specified
    if mood:
        unique = filter_candidates_by_mood(unique, mood, audio_map)

    # Rank with all features
    ranked = rank_candidates(
        unique, profile, count=count,
        weights=weights,
        audio_features_map=audio_map,
        user_audio_centroid=centroid,
        recent_recommendations=recent_recs,
    )

    lines = [
        f"## Discovered Songs ({len(ranked)})",
        f"*Strategies: {', '.join(strategies_used)}*",
    ]
    if mood:
        lines.append(f"*Mood: {mood}*")
    lines.append("")

    for i, song in enumerate(ranked, start=1):
        name = song.get("name", "Unknown")
        artist = song.get("artist_name", "Unknown")
        score = song.get("_score", 0)
        explanation = song.get("_explanation", "")
        genres = ", ".join((song.get("genre_names") or [])[:3])

        lines.append(f"{i}. **{name}** — {artist} (match: {score:.0%})")
        if genres:
            lines.append(f"   Genres: {genres}")
        if explanation:
            lines.append(f"   *{explanation}*")
        lines.append(f"   ID: `{song.get('catalog_id', '')}`")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_feedback(
    song_id: str,
    feedback: str,
) -> str:
    """Give feedback on a recommended song to improve future recommendations.

    This trains the adaptive scoring engine to better match your preferences.

    Args:
        song_id: Catalog song ID
        feedback: One of: "thumbs_up", "thumbs_down", "added_to_library", "skipped"
    """
    _, queries = _ctx()

    valid_types = {"thumbs_up", "thumbs_down", "added_to_library", "skipped"}
    if feedback not in valid_types:
        return f"Invalid feedback type '{feedback}'. Use one of: {', '.join(sorted(valid_types))}"

    # Get current weights for snapshot
    all_fb = await queries.get_all_feedback()
    weights = optimize_weights(all_fb) if all_fb else dict(DEFAULT_WEIGHTS)

    # Get predicted score if we have the song cached and a profile
    predicted = None
    cached = await queries.get_cached_song(song_id)
    if cached:
        snapshot = await queries.get_latest_taste_snapshot()
        if snapshot and snapshot.get("genre_vector"):
            result = score_candidate(cached, snapshot, weights=weights)
            predicted = result["_score"]

    await queries.insert_feedback({
        "catalog_id": song_id,
        "feedback_type": feedback,
        "predicted_score": predicted,
        "weight_snapshot": weights,
    })

    emoji_map = {
        "thumbs_up": "Loved",
        "thumbs_down": "Disliked",
        "added_to_library": "Added to library",
        "skipped": "Skipped",
    }
    return (
        f"**{emoji_map[feedback]}** song `{song_id}`. "
        f"Feedback recorded — future recommendations will adapt."
    )


@mcp.tool()
async def musicmind_smart_playlist(
    name: str,
    vibe: str,
    count: int = 20,
) -> str:
    """Create a personalized playlist from a natural language vibe description.

    Describe the mood, energy, or scenario and it will find matching songs,
    score them against your taste profile, and create a real Apple Music playlist.

    Args:
        name: Playlist name
        vibe: Natural language description (e.g., "underground drill for a late night drive",
              "chill atmospheric stuff for studying", "high energy workout bangers")
        count: Number of tracks (default 20, max 50)
    """
    client, queries = _ctx()
    count = min(count, 50)

    profile = await _get_or_build_profile(queries)
    if not profile:
        return (
            "No data to build taste profile. Populate your cache first with "
            "`musicmind_library_songs` and `musicmind_recently_played`."
        )

    search_terms = _parse_vibe(vibe)

    candidates: list[dict[str, Any]] = []
    for term in search_terms:
        try:
            result = await client.search_catalog(term, types="songs", limit=25)
            for song in result.songs.data:
                cache = extract_song_cache_data(song)
                if cache:
                    candidates.append(cache)
                    await queries.upsert_song_metadata([cache])
        except Exception as e:
            logger.warning("Search for '%s' failed: %s", term, e)

    # Also get recs from similar artists
    top_artists = profile.get("top_artists", [])
    cached_artists = await queries.get_all_cached_artists()
    artist_name_to_id = {a["name"].lower(): a["artist_id"] for a in cached_artists}
    seed_ids = []
    for a in top_artists[:3]:
        aid = artist_name_to_id.get(a["name"].lower())
        if aid:
            seed_ids.append(aid)

    if seed_ids:
        try:
            similar = await similar_artist_crawl(
                client, queries, seed_ids, depth=1, songs_per_artist=3
            )
            candidates.extend(similar)
        except Exception as e:
            logger.warning("Similar artist crawl failed: %s", e)

    if not candidates:
        return f"No songs found for vibe '{vibe}'. Try a different description."

    # Deduplicate
    unique = _count_strategies(candidates)

    # Load adaptive weights and audio
    all_fb = await queries.get_all_feedback()
    weights = optimize_weights(all_fb) if all_fb else dict(DEFAULT_WEIGHTS)
    cids = [c.get("catalog_id", "") for c in unique if c.get("catalog_id")]
    audio_map = await queries.get_audio_features_bulk(cids)
    centroid = build_audio_centroid(list(audio_map.values())) if audio_map else None
    recent_recs = await queries.get_recent_recommendations(days=30)

    # Rank
    ranked = rank_candidates(
        unique, profile, count=count,
        weights=weights,
        audio_features_map=audio_map,
        user_audio_centroid=centroid,
        recent_recommendations=recent_recs,
    )

    track_ids = [s.get("catalog_id", "") for s in ranked if s.get("catalog_id")]
    description = f"Generated by MusicMind: {vibe}"

    try:
        playlist_resource = await client.create_playlist(name, description, track_ids)
        apple_id = playlist_resource.id or "(pending)"
    except Exception as e:
        logger.error("Failed to create playlist: %s", e)
        apple_id = "(failed)"

    snapshot_id = None
    latest = await queries.get_latest_taste_snapshot()
    if latest:
        snapshot_id = latest.get("id")

    await queries.save_generated_playlist({
        "apple_playlist_id": apple_id,
        "name": name,
        "description": description,
        "vibe_prompt": vibe,
        "track_ids": track_ids,
        "taste_snapshot_id": snapshot_id,
    })

    lines = [
        f"## Playlist Created: {name}",
        f"**Vibe:** {vibe}",
        f"**Tracks:** {len(ranked)}",
        f"**Apple Music ID:** `{apple_id}`\n",
        "### Track List",
    ]
    for i, song in enumerate(ranked, start=1):
        sname = song.get("name", "Unknown")
        artist = song.get("artist_name", "Unknown")
        score = song.get("_score", 0)
        lines.append(f"{i}. **{sname}** — {artist} ({score:.0%} match)")

    return "\n".join(lines)


def _parse_vibe(vibe: str) -> list[str]:
    """Parse a natural language vibe into search terms."""
    terms = [vibe]

    stop_words = {
        "for", "a", "the", "my", "some", "like", "stuff", "things",
        "songs", "music", "tracks", "playlist", "vibes", "vibe",
        "with", "and", "or", "but", "to", "of", "in", "on", "at",
        "that", "this", "something", "good", "great", "best", "new",
    }
    words = [w.lower().strip(".,!?\"'") for w in vibe.split()]
    keywords = [w for w in words if w not in stop_words and len(w) > 2]

    if len(keywords) >= 2:
        terms.append(" ".join(keywords[:3]))
    if len(keywords) >= 3:
        terms.append(" ".join(keywords[1:4]))

    genre_hints = {
        "drill": "drill rap", "trap": "trap music", "lofi": "lo-fi hip hop",
        "lo-fi": "lo-fi hip hop", "chill": "chill vibes",
        "ambient": "ambient electronic", "rock": "rock", "pop": "pop hits",
        "r&b": "r&b soul", "rnb": "r&b soul", "jazz": "jazz",
        "classical": "classical", "edm": "electronic dance",
        "house": "house music", "techno": "techno", "reggaeton": "reggaeton",
        "latin": "latin music", "indie": "indie alternative",
        "metal": "metal", "punk": "punk rock", "country": "country",
        "folk": "folk acoustic",
    }
    for word in keywords:
        if word in genre_hints:
            terms.append(genre_hints[word])

    return terms[:5]


@mcp.tool()
async def musicmind_refresh_cache() -> str:
    """Force refresh: re-fetch recently played tracks and rebuild taste profile.

    Useful to run periodically to keep your taste profile up to date.
    Now uses temporal decay for more accurate taste modeling.
    """
    client, queries = _ctx()

    from musicmind.tools.playback import musicmind_recently_played

    await musicmind_recently_played(limit=50)

    songs = await queries.get_all_cached_songs()
    history = await queries.get_listening_history()

    if not songs:
        return "Cache is still empty after refresh. Try browsing your library first."

    profile = build_taste_profile(songs, history, use_temporal_decay=True)
    await queries.save_taste_snapshot(profile)

    stats = await queries.get_cache_stats()
    return (
        f"## Cache Refreshed\n"
        f"- Songs: {stats['songs_cached']}\n"
        f"- Artists: {stats['artists_cached']}\n"
        f"- History entries: {stats['listening_history_entries']}\n"
        f"- Taste profile updated with {profile['total_songs_analyzed']} songs"
    )


@mcp.tool()
async def musicmind_why_this_song(song_id: str) -> str:
    """Explain why a specific song matches or doesn't match your taste profile.

    Provides a detailed breakdown of each scoring dimension.

    Args:
        song_id: Catalog song ID to analyze
    """
    client, queries = _ctx()

    snapshot = await queries.get_latest_taste_snapshot()
    if not snapshot or not snapshot.get("genre_vector"):
        return "No taste profile yet. Run `musicmind_taste_profile` first."

    cached = await queries.get_cached_song(song_id)
    if not cached:
        resource = await client.get_song(song_id)
        cached = extract_song_cache_data(resource)
        if cached:
            await queries.upsert_song_metadata([cached])
        else:
            return f"Could not find song `{song_id}`."

    # Load audio features and adaptive weights
    audio = await queries.get_audio_features(song_id)
    all_fb = await queries.get_all_feedback()
    weights = optimize_weights(all_fb) if all_fb else None

    result = score_candidate(
        cached, snapshot,
        weights=weights,
        audio_features=audio,
    )
    bd = result["_breakdown"]

    lines = [
        f"## Why This Song: {cached['name']}",
        f"**Artist:** {cached['artist_name']}",
        f"**Genres:** {', '.join(cached.get('genre_names', []))}",
        f"**Overall Score:** {result['_score']:.0%}\n",
    ]

    # Genre analysis
    gm = bd["genre_match"]
    if gm > 0.6:
        lines.append(f"**Genre Match ({gm:.0%}):** Strong overlap with your genres.")
    elif gm > 0.3:
        lines.append(f"**Genre Match ({gm:.0%}):** Some overlap with your taste.")
    else:
        lines.append(f"**Genre Match ({gm:.0%}):** Outside your usual genres.")

    # Artist
    am = bd["artist_match"]
    if am > 0.5:
        lines.append(f"**Artist Affinity ({am:.0%}):** You listen to "
                      f"{cached['artist_name']}.")
    elif bd["novelty"] > 0.3:
        lines.append(f"**Novelty ({bd['novelty']:.0%}):** New artist in a familiar genre!")
    else:
        lines.append(f"**Artist Affinity ({am:.0%}):** Not in your rotation.")

    # Audio
    asim = bd["audio_similarity"]
    if audio and asim != 0.5:
        lines.append(f"**Audio Similarity ({asim:.0%}):** "
                      f"{'Matches' if asim > 0.6 else 'Differs from'} your audio preferences.")

    lines.append(f"\n> **Summary:** {result['_explanation']}")
    return "\n".join(lines)
