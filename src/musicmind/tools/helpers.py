"""Shared helpers for MCP tools — resource extraction and formatting."""

from __future__ import annotations

from musicmind.models import Resource


def extract_song_cache_data(resource: Resource) -> dict | None:
    """Extract a flat dict suitable for song_metadata_cache from an API Resource.

    Handles both library songs (with catalog relationship) and catalog songs.
    Returns None if no catalog ID can be determined.
    """
    attrs = resource.attributes
    catalog_id = ""
    library_id = None

    if resource.type == "songs":
        catalog_id = resource.id
    elif resource.type == "library-songs":
        library_id = resource.id
        # Try to get catalog ID from relationships
        catalog_data = (
            resource.relationships.get("catalog", {}).get("data", [])
        )
        if catalog_data:
            catalog_id = catalog_data[0].get("id", "")
            # Merge catalog attributes if available
            catalog_attrs = catalog_data[0].get("attributes", {})
            if catalog_attrs:
                attrs = {**attrs, **catalog_attrs}
        else:
            # Fall back to library ID as catalog ID
            catalog_id = resource.id

    if not catalog_id:
        return None

    editorial = attrs.get("editorialNotes", {})
    editorial_text = ""
    if isinstance(editorial, dict):
        editorial_text = editorial.get("standard", editorial.get("short", ""))

    previews = attrs.get("previews", [])
    preview_url = previews[0].get("url", "") if previews else ""

    artwork = attrs.get("artwork", {})
    bg_color = ""
    artwork_url = ""
    if isinstance(artwork, dict):
        bg_color = artwork.get("bgColor", "")
        artwork_url = artwork.get("url", "")

    return {
        "catalog_id": catalog_id,
        "library_id": library_id,
        "name": attrs.get("name", ""),
        "artist_name": attrs.get("artistName", ""),
        "album_name": attrs.get("albumName", ""),
        "genre_names": attrs.get("genreNames", []),
        "duration_ms": attrs.get("durationInMillis"),
        "release_date": attrs.get("releaseDate"),
        "isrc": attrs.get("isrc"),
        "editorial_notes": editorial_text,
        "audio_traits": attrs.get("audioTraits", []),
        "has_lyrics": attrs.get("hasLyrics", False),
        "content_rating": attrs.get("contentRating"),
        "artwork_bg_color": bg_color,
        "artwork_url_template": artwork_url,
        "preview_url": preview_url,
    }


def extract_artist_cache_data(resource: Resource) -> dict:
    """Extract a flat dict suitable for artist_cache from an API Resource."""
    attrs = resource.attributes
    artist_id = resource.id

    # Extract top songs from views if available
    top_song_ids = []
    top_songs_view = resource.views.get("top-songs", {})
    if isinstance(top_songs_view, dict):
        for item in top_songs_view.get("data", []):
            top_song_ids.append(item.get("id", ""))

    # Extract similar artists from views
    similar_ids = []
    similar_view = resource.views.get("similar-artists", {})
    if isinstance(similar_view, dict):
        for item in similar_view.get("data", []):
            similar_ids.append(item.get("id", ""))

    return {
        "artist_id": artist_id,
        "name": attrs.get("name", ""),
        "genre_names": attrs.get("genreNames", []),
        "top_song_ids": top_song_ids,
        "similar_artist_ids": similar_ids,
    }


def format_song_md(resource: Resource, index: int | None = None) -> str:
    """Format a song Resource as a markdown line, always including the catalog ID."""
    attrs = resource.attributes
    name = attrs.get("name", "Unknown")
    artist = attrs.get("artistName", "Unknown")
    album = attrs.get("albumName", "")
    genres = ", ".join(attrs.get("genreNames", []))
    duration_ms = attrs.get("durationInMillis", 0) or 0
    duration = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}" if duration_ms else ""

    # Determine catalog ID — works for both catalog songs and library songs
    catalog_id = ""
    if resource.type == "songs":
        catalog_id = resource.id
    elif resource.type == "library-songs":
        catalog_data = resource.relationships.get("catalog", {}).get("data", [])
        if catalog_data:
            catalog_id = catalog_data[0].get("id", "")

    prefix = f"{index}. " if index is not None else "- "
    line = f"{prefix}**{name}** — {artist}"
    if album:
        line += f" ({album})"
    if duration:
        line += f" [{duration}]"
    if genres:
        line += f"\n  Genres: {genres}"
    if catalog_id:
        line += f"\n  ID: `{catalog_id}`"
    return line


def format_album_md(resource: Resource, index: int | None = None) -> str:
    """Format an album Resource as a markdown line, including the album ID."""
    attrs = resource.attributes
    name = attrs.get("name", "Unknown")
    artist = attrs.get("artistName", "Unknown")
    tracks = attrs.get("trackCount", 0)
    prefix = f"{index}. " if index is not None else "- "
    line = f"{prefix}**{name}** — {artist}"
    if tracks:
        line += f" ({tracks} tracks)"
    if resource.id:
        line += f"\n  ID: `{resource.id}`"
    return line


def format_artist_md(resource: Resource, index: int | None = None) -> str:
    """Format an artist Resource as a markdown line, including the artist ID."""
    attrs = resource.attributes
    name = attrs.get("name", "Unknown")
    genres = ", ".join(attrs.get("genreNames", []))
    prefix = f"{index}. " if index is not None else "- "
    line = f"{prefix}**{name}**"
    if genres:
        line += f" — {genres}"
    if resource.id:
        line += f"\n  ID: `{resource.id}`"
    return line


def format_playlist_md(resource: Resource, index: int | None = None) -> str:
    """Format a playlist Resource as a markdown line."""
    attrs = resource.attributes
    name = attrs.get("name", "Unknown")
    prefix = f"{index}. " if index is not None else "- "
    return f"{prefix}**{name}** (id: `{resource.id}`)"
