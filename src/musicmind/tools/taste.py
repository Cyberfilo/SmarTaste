"""Taste profile MCP tools — analyze, compare, and report listening patterns."""

from __future__ import annotations

from collections import Counter

from musicmind.engine.profile import build_taste_profile
from musicmind.engine.scorer import score_candidate
from musicmind.server import mcp
from musicmind.tools.helpers import extract_song_cache_data


def _ctx():
    ctx = mcp.get_context()
    lc = ctx.request_context.lifespan_context
    return lc["client"], lc["queries"]


@mcp.tool()
async def musicmind_taste_profile() -> str:
    """Build and display your music taste profile.

    Analyzes your cached library and listening history to compute:
    - Genre affinity vector (what you listen to most)
    - Top artists by affinity score
    - Audio trait preferences (lossless, Atmos, spatial)
    - Release year distribution (new vs. catalog preference)
    - Familiarity score (how adventurous your taste is)

    Call musicmind_recently_played and musicmind_library_songs first to populate the cache.

    For a more comprehensive briefing (profile + recent listening + patterns in one call),
    use musicmind_taste_deep instead.
    """
    _, queries = _ctx()

    songs = await queries.get_all_cached_songs()
    history = await queries.get_listening_history()

    if not songs:
        return (
            "No song data cached yet. Use `musicmind_library_songs` or "
            "`musicmind_recently_played` first to populate the cache."
        )

    profile = build_taste_profile(songs, history, use_temporal_decay=True)

    # Save snapshot
    await queries.save_taste_snapshot(profile)

    # Format output
    lines = ["## Your MusicMind Taste Profile"]
    lines.append(f"*Based on {profile['total_songs_analyzed']} songs, "
                 f"~{profile['listening_hours_estimated']}h estimated*\n")

    # Genre vector (top 10)
    gv = profile["genre_vector"]
    if gv:
        lines.append("### Genre Affinity")
        for genre, weight in list(gv.items())[:10]:
            bar = "█" * int(weight * 40)
            lines.append(f"- **{genre}**: {weight:.1%} {bar}")

    # Top artists (top 10)
    ta = profile["top_artists"]
    if ta:
        lines.append("\n### Top Artists")
        for i, a in enumerate(ta[:10], start=1):
            lines.append(
                f"{i}. **{a['name']}** — affinity {a['score']:.0%} "
                f"({a['song_count']} songs in library)"
            )

    # Audio traits
    atp = profile["audio_trait_preferences"]
    if atp:
        lines.append("\n### Audio Preferences")
        for trait, pct in atp.items():
            lines.append(f"- **{trait}**: {pct:.0%} of library")

    # Release year distribution (top 5)
    ryd = profile["release_year_distribution"]
    if ryd:
        lines.append("\n### Release Year Distribution")
        for year, pct in list(ryd.items())[:5]:
            lines.append(f"- **{year}**: {pct:.0%}")

    # Familiarity
    fam = profile["familiarity_score"]
    if fam < 0.3:
        fam_desc = "focused — you know what you like"
    elif fam < 0.6:
        fam_desc = "moderate — mix of favorites and exploration"
    else:
        fam_desc = "adventurous — you explore widely"
    lines.append(f"\n### Familiarity Score: {fam:.0%} ({fam_desc})")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_taste_compare(song_id: str) -> str:
    """Show how well a specific song matches your taste profile.

    Provides a per-dimension breakdown of the match.

    Args:
        song_id: Catalog song ID to compare against your taste
    """
    client, queries = _ctx()

    # Get latest profile
    snapshot = await queries.get_latest_taste_snapshot()
    if not snapshot:
        return "No taste profile yet. Run `musicmind_taste_profile` first."

    # Get song data
    cached = await queries.get_cached_song(song_id)
    if not cached:
        # Fetch from API
        resource = await client.get_song(song_id)
        cached = extract_song_cache_data(resource)
        if cached:
            await queries.upsert_song_metadata([cached])
        else:
            return f"Could not find song `{song_id}`."

    result = score_candidate(cached, snapshot)

    lines = [
        f"## Taste Match: {cached['name']}",
        f"**Artist:** {cached['artist_name']}",
        f"**Overall Match:** {result['_score']:.0%}\n",
        "### Breakdown",
    ]

    bd = result["_breakdown"]
    labels = {
        "genre_match": "Genre Match",
        "artist_match": "Artist Affinity",
        "novelty": "Novelty Bonus",
        "freshness": "Freshness",
        "diversity_penalty": "Diversity Penalty",
    }
    for key, label in labels.items():
        val = bd.get(key, 0)
        bar = "█" * int(val * 20)
        lines.append(f"- **{label}:** {val:.0%} {bar}")

    lines.append(f"\n> {result['_explanation']}")
    return "\n".join(lines)


@mcp.tool()
async def musicmind_listening_stats() -> str:
    """Get aggregate listening statistics from your cached data.

    Shows total songs, estimated hours, genre breakdown, and most-heard artists.
    """
    _, queries = _ctx()

    stats = await queries.get_cache_stats()
    songs = await queries.get_all_cached_songs()
    history = await queries.get_listening_history()

    lines = ["## MusicMind Listening Stats"]
    lines.append(f"- **Songs cached:** {stats['songs_cached']}")
    lines.append(f"- **Artists cached:** {stats['artists_cached']}")
    lines.append(f"- **Listening history entries:** {stats['listening_history_entries']}")
    lines.append(f"- **Taste snapshots:** {stats['taste_snapshots']}")
    lines.append(f"- **Generated playlists:** {stats['generated_playlists']}")

    # Estimate total hours
    total_ms = sum((s.get("duration_ms") or 0) for s in songs)
    hours = total_ms / 3_600_000
    lines.append(f"- **Estimated library duration:** {hours:.1f} hours")

    # Most heard artists from history
    if history:
        artist_counts: Counter[str] = Counter()
        for entry in history:
            artist = entry.get("artist_name", "")
            if artist:
                artist_counts[artist] += 1

        lines.append("\n### Most Heard Artists (from history)")
        for artist, count in artist_counts.most_common(10):
            lines.append(f"- **{artist}**: {count} plays")

    # Genre breakdown from cache
    if songs:
        genre_counts: Counter[str] = Counter()
        for s in songs:
            genres = s.get("genre_names") or []
            if isinstance(genres, list):
                for g in genres:
                    genre_counts[g] += 1

        lines.append("\n### Top Genres (from library)")
        for genre, count in genre_counts.most_common(10):
            lines.append(f"- **{genre}**: {count} songs")

    return "\n".join(lines)


@mcp.tool()
async def musicmind_taste_deep() -> str:
    """Get a comprehensive taste briefing in one call.

    Combines: full taste profile, recent listening, top genres, top artists,
    listening patterns, and familiarity score. Use this FIRST before creating
    curated playlists — it gives Claude maximum context about the user's taste.

    Returns a structured briefing designed for Claude to understand the user's
    musical identity: what scenes they're into, what artists define their taste,
    how adventurous they are, and what they've been playing lately.
    """
    _, queries = _ctx()

    songs = await queries.get_all_cached_songs()
    history = await queries.get_listening_history()

    if not songs:
        return (
            "No data cached yet. Run `musicmind_recently_played` and "
            "`musicmind_library_songs` first to populate the cache."
        )

    profile = build_taste_profile(songs, history, use_temporal_decay=True)
    await queries.save_taste_snapshot(profile)

    stats = await queries.get_cache_stats()

    # Recent tracks from history (no API call needed)
    recent = sorted(
        history, key=lambda h: h.get("observed_at", ""), reverse=True
    )[:30]

    recent_artists: Counter[str] = Counter()
    recent_genres: Counter[str] = Counter()
    for h in recent:
        artist = h.get("artist_name", "")
        if artist:
            recent_artists[artist] += 1
        for g in h.get("genre_names") or []:
            recent_genres[g] += 1

    lib_genres: Counter[str] = Counter()
    for s in songs:
        for g in s.get("genre_names") or []:
            lib_genres[g] += 1

    lines = ["## Deep Taste Briefing"]
    lines.append(
        f"*{profile['total_songs_analyzed']} songs analyzed, "
        f"~{profile['listening_hours_estimated']}h estimated, "
        f"{stats['listening_history_entries']} history entries*\n"
    )

    # Genre identity
    gv = profile["genre_vector"]
    if gv:
        lines.append("### Genre Identity (weighted by recency)")
        for genre, weight in list(gv.items())[:15]:
            bar = "\u2588" * int(weight * 40)
            count = lib_genres.get(genre, 0)
            lines.append(f"- **{genre}**: {weight:.1%} {bar} ({count} songs)")

    # Top artists with context
    ta = profile["top_artists"]
    if ta:
        lines.append("\n### Core Artists (by affinity)")
        for i, a in enumerate(ta[:15], start=1):
            recent_count = recent_artists.get(a["name"], 0)
            recent_tag = (
                f" — {recent_count}x in recent" if recent_count > 0 else ""
            )
            lines.append(
                f"{i}. **{a['name']}** — affinity {a['score']:.0%} "
                f"({a['song_count']} songs){recent_tag}"
            )

    # Recent listening pattern
    if recent:
        lines.append("\n### Recent Listening (last 30 tracks)")
        for artist, count in recent_artists.most_common(10):
            lines.append(f"- **{artist}**: {count} plays")

        lines.append("\n### Recent Genres")
        for genre, count in recent_genres.most_common(10):
            lines.append(f"- **{genre}**: {count} tracks")

    # Recent specific tracks (last 10)
    if recent:
        lines.append("\n### Last 10 Tracks Played")
        for i, h in enumerate(recent[:10], start=1):
            lines.append(
                f"{i}. **{h.get('song_name', '?')}** — "
                f"{h.get('artist_name', '?')}"
            )

    # Familiarity and exploration tendency
    fam = profile["familiarity_score"]
    if fam < 0.3:
        fam_desc = (
            "very focused — knows exactly what they like, stick to known scenes"
        )
    elif fam < 0.6:
        fam_desc = "moderate — open to discovery within familiar territory"
    else:
        fam_desc = "explorer — wide taste, open to anything"
    lines.append(f"\n### Taste Profile: {fam:.0%} ({fam_desc})")

    # Release year preference
    ryd = profile["release_year_distribution"]
    if ryd:
        lines.append("\n### Era Preference")
        for year, pct in list(ryd.items())[:5]:
            lines.append(f"- **{year}**: {pct:.0%}")

    # Feedback summary if available
    all_feedback = await queries.get_all_feedback()
    if all_feedback:
        thumbs_up = sum(
            1 for f in all_feedback if f.get("feedback_type") == "thumbs_up"
        )
        thumbs_down = sum(
            1 for f in all_feedback if f.get("feedback_type") == "thumbs_down"
        )
        lines.append(
            f"\n### Feedback History: {thumbs_up} up / {thumbs_down} down"
        )

    return "\n".join(lines)
