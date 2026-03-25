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
from musicmind.engine.profile import build_taste_profile
from musicmind.engine.scorer import rank_candidates, score_candidate
from musicmind.server import mcp
from musicmind.tools.helpers import extract_song_cache_data

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))


def _ctx():
    ctx = mcp.get_context()
    lc = ctx.request_context.lifespan_context
    return lc["client"], lc["queries"]


def _get_profile_and_check(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a taste profile snapshot."""
    if not snapshot or not snapshot.get("genre_vector"):
        return None
    return snapshot


@mcp.tool()
async def musicmind_discover(
    count: int = 15,
    strategy: str = "auto",
) -> str:
    """Discover new songs personalized to your taste.

    Uses your taste profile to find and rank songs you'll likely enjoy.

    Args:
        count: Number of songs to discover (default 15, max 50)
        strategy: Discovery strategy:
            - "similar_artists" — crawl artists similar to your favorites
            - "genre_adjacent" — explore your top genres in the catalog
            - "editorial" — mine editorial playlists and best-of searches
            - "charts" — filter chart songs by your taste
            - "auto" — combine all strategies (recommended)
    """
    client, queries = _ctx()
    count = min(count, 50)

    # Get or build taste profile
    snapshot = await queries.get_latest_taste_snapshot()
    profile = _get_profile_and_check(snapshot)
    if not profile:
        songs = await queries.get_all_cached_songs()
        history = await queries.get_listening_history()
        if not songs:
            return (
                "No data to build taste profile. Use `musicmind_library_songs` "
                "and `musicmind_recently_played` first."
            )
        profile = build_taste_profile(songs, history)
        await queries.save_taste_snapshot(profile)

    # Collect candidates from chosen strategy
    candidates: list[dict[str, Any]] = []
    top_artists = profile.get("top_artists", [])
    seed_ids = []

    # Try to get artist IDs from cache
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
            candidates.extend(new)
            strategies_used.append("similar artists")
        except Exception as e:
            logger.warning("Similar artist crawl failed: %s", e)

    if strategy in ("genre_adjacent", "auto"):
        try:
            new = await genre_adjacent_explore(client, queries, profile)
            candidates.extend(new)
            strategies_used.append("genre exploration")
        except Exception as e:
            logger.warning("Genre exploration failed: %s", e)

    if strategy in ("editorial", "auto"):
        try:
            new = await editorial_mining(client, queries, profile)
            candidates.extend(new)
            strategies_used.append("editorial mining")
        except Exception as e:
            logger.warning("Editorial mining failed: %s", e)

    if strategy in ("charts", "auto"):
        try:
            new = await chart_filter(client, queries, profile)
            candidates.extend(new)
            strategies_used.append("charts")
        except Exception as e:
            logger.warning("Chart filter failed: %s", e)

    if not candidates:
        return "No candidates found. Try populating your cache with more library data first."

    # Deduplicate by catalog_id
    seen = set()
    unique = []
    for c in candidates:
        cid = c.get("catalog_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(c)

    # Rank and select top N
    ranked = rank_candidates(unique, profile, count=count)

    lines = [
        f"## Discovered Songs ({len(ranked)})",
        f"*Strategies: {', '.join(strategies_used)}*\n",
    ]
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
async def musicmind_smart_playlist(
    name: str,
    vibe: str,
    count: int = 20,
) -> str:
    """Create a personalized playlist from a natural language vibe description.

    This is MusicMind's signature tool. Describe the mood, energy, or scenario
    and it will find matching songs from the catalog, score them against your
    taste profile, and create a real Apple Music playlist.

    Args:
        name: Playlist name
        vibe: Natural language description (e.g., "underground drill for a late night drive",
              "chill atmospheric stuff for studying", "high energy workout bangers")
        count: Number of tracks (default 20, max 50)
    """
    client, queries = _ctx()
    count = min(count, 50)

    # Get or build taste profile
    snapshot = await queries.get_latest_taste_snapshot()
    profile = _get_profile_and_check(snapshot)
    if not profile:
        songs = await queries.get_all_cached_songs()
        history = await queries.get_listening_history()
        if not songs:
            return (
                "No data to build taste profile. Populate your cache first with "
                "`musicmind_library_songs` and `musicmind_recently_played`."
            )
        profile = build_taste_profile(songs, history)
        await queries.save_taste_snapshot(profile)

    # Parse vibe into search terms
    search_terms = _parse_vibe(vibe)

    # Collect candidates from multiple searches
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

    # Also get recommendations from similar artists if we have them
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
    seen = set()
    unique = []
    for c in candidates:
        cid = c.get("catalog_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(c)

    # Rank with taste profile + diversity
    ranked = rank_candidates(unique, profile, count=count)

    # Create the playlist on Apple Music
    track_ids = [s.get("catalog_id", "") for s in ranked if s.get("catalog_id")]
    description = f"Generated by MusicMind: {vibe}"

    try:
        playlist_resource = await client.create_playlist(
            name, description, track_ids
        )
        apple_id = playlist_resource.id or "(pending)"
    except Exception as e:
        logger.error("Failed to create playlist: %s", e)
        apple_id = "(failed)"

    # Save to generated_playlists
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

    # Format output
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
    """Parse a natural language vibe into search terms.

    Generates multiple search queries to cast a wide net.
    """
    terms = [vibe]  # Always use the raw vibe as one search

    # Extract meaningful words (skip common filler)
    stop_words = {
        "for", "a", "the", "my", "some", "like", "stuff", "things",
        "songs", "music", "tracks", "playlist", "vibes", "vibe",
        "with", "and", "or", "but", "to", "of", "in", "on", "at",
        "that", "this", "something", "good", "great", "best", "new",
    }
    words = [w.lower().strip(".,!?\"'") for w in vibe.split()]
    keywords = [w for w in words if w not in stop_words and len(w) > 2]

    # Add keyword combinations as additional searches
    if len(keywords) >= 2:
        terms.append(" ".join(keywords[:3]))
    if len(keywords) >= 3:
        terms.append(" ".join(keywords[1:4]))

    # Genre-specific search if genre keywords detected
    genre_hints = {
        "drill": "drill rap",
        "trap": "trap music",
        "lofi": "lo-fi hip hop",
        "lo-fi": "lo-fi hip hop",
        "chill": "chill vibes",
        "ambient": "ambient electronic",
        "rock": "rock",
        "pop": "pop hits",
        "r&b": "r&b soul",
        "rnb": "r&b soul",
        "jazz": "jazz",
        "classical": "classical",
        "edm": "electronic dance",
        "house": "house music",
        "techno": "techno",
        "reggaeton": "reggaeton",
        "latin": "latin music",
        "indie": "indie alternative",
        "metal": "metal",
        "punk": "punk rock",
        "country": "country",
        "folk": "folk acoustic",
    }
    for word in keywords:
        if word in genre_hints:
            terms.append(genre_hints[word])

    return terms[:5]  # Cap at 5 searches


@mcp.tool()
async def musicmind_refresh_cache() -> str:
    """Force refresh: re-fetch recently played tracks and rebuild taste profile.

    Useful to run periodically to keep your taste profile up to date.
    """
    client, queries = _ctx()

    # Fetch recently played
    from musicmind.tools.playback import musicmind_recently_played

    await musicmind_recently_played(limit=50)

    # Rebuild taste profile
    songs = await queries.get_all_cached_songs()
    history = await queries.get_listening_history()

    if not songs:
        return "Cache is still empty after refresh. Try browsing your library first."

    profile = build_taste_profile(songs, history)
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

    result = score_candidate(cached, snapshot)
    bd = result["_breakdown"]

    lines = [
        f"## Why This Song: {cached['name']}",
        f"**Artist:** {cached['artist_name']}",
        f"**Genres:** {', '.join(cached.get('genre_names', []))}",
        f"**Overall Score:** {result['_score']:.0%}\n",
    ]

    # Genre analysis
    genre_match = bd["genre_match"]
    if genre_match > 0.6:
        lines.append(f"**Genre Match ({genre_match:.0%}):** Strong overlap with your "
                      "preferred genres.")
    elif genre_match > 0.3:
        lines.append(f"**Genre Match ({genre_match:.0%}):** Some overlap with your taste.")
    else:
        lines.append(f"**Genre Match ({genre_match:.0%}):** This is outside your "
                      "usual genre territory.")

    # Artist analysis
    artist_match = bd["artist_match"]
    if artist_match > 0.5:
        lines.append(f"**Artist Affinity ({artist_match:.0%}):** You already listen to "
                      f"{cached['artist_name']}.")
    elif bd["novelty"] > 0:
        lines.append(f"**Novelty Bonus ({bd['novelty']:.0%}):** New artist in a "
                      "genre you enjoy — discovery opportunity!")
    else:
        lines.append(f"**Artist Affinity ({artist_match:.0%}):** This artist isn't in "
                      "your regular rotation.")

    # Freshness
    freshness = bd["freshness"]
    lines.append(f"**Freshness ({freshness:.0%}):** "
                 f"{'Matches' if freshness > 0.3 else 'Doesnt match'} your "
                 "release year preferences.")

    lines.append(f"\n> **Summary:** {result['_explanation']}")
    return "\n".join(lines)
