"""SQLAlchemy Core table definitions for MusicMind persistence.

All tables use TEXT for Apple Music IDs (they're strings like "1234567890")
and JSON columns for arrays/dicts (stored as TEXT in SQLite, native JSON in Postgres).
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

listening_history = sa.Table(
    "listening_history",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("song_id", sa.Text, nullable=False, index=True),
    sa.Column("song_name", sa.Text, nullable=False),
    sa.Column("artist_name", sa.Text, nullable=False),
    sa.Column("album_name", sa.Text, default=""),
    sa.Column("genre_names", sa.JSON, default=list),
    sa.Column("duration_ms", sa.Integer, nullable=True),
    sa.Column("observed_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.Column("position_in_recent", sa.Integer, nullable=True),
    sa.Column("source", sa.Text, nullable=False, default="recently_played"),
)

song_metadata_cache = sa.Table(
    "song_metadata_cache",
    metadata,
    sa.Column("catalog_id", sa.Text, primary_key=True),
    sa.Column("library_id", sa.Text, nullable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("artist_name", sa.Text, nullable=False),
    sa.Column("album_name", sa.Text, default=""),
    sa.Column("genre_names", sa.JSON, default=list),
    sa.Column("duration_ms", sa.Integer, nullable=True),
    sa.Column("release_date", sa.Text, nullable=True),
    sa.Column("isrc", sa.Text, nullable=True),
    sa.Column("editorial_notes", sa.Text, nullable=True),
    sa.Column("audio_traits", sa.JSON, default=list),
    sa.Column("has_lyrics", sa.Boolean, default=False),
    sa.Column("content_rating", sa.Text, nullable=True),
    sa.Column("artwork_bg_color", sa.Text, nullable=True),
    sa.Column("artwork_url_template", sa.Text, nullable=True),
    sa.Column("preview_url", sa.Text, nullable=True),
    sa.Column("user_rating", sa.Integer, nullable=True),
    sa.Column("date_added_to_library", sa.DateTime, nullable=True),
    sa.Column("fetched_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
)

artist_cache = sa.Table(
    "artist_cache",
    metadata,
    sa.Column("artist_id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("genre_names", sa.JSON, default=list),
    sa.Column("top_song_ids", sa.JSON, default=list),
    sa.Column("similar_artist_ids", sa.JSON, default=list),
    sa.Column("fetched_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
)

taste_profile_snapshots = sa.Table(
    "taste_profile_snapshots",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("computed_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.Column("genre_vector", sa.JSON, default=dict),
    sa.Column("top_artists", sa.JSON, default=list),
    sa.Column("audio_trait_preferences", sa.JSON, default=dict),
    sa.Column("release_year_distribution", sa.JSON, default=dict),
    sa.Column("familiarity_score", sa.Float, default=0.0),
    sa.Column("total_songs_analyzed", sa.Integer, default=0),
    sa.Column("listening_hours_estimated", sa.Float, default=0.0),
)

generated_playlists = sa.Table(
    "generated_playlists",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("apple_playlist_id", sa.Text, nullable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, default=""),
    sa.Column("vibe_prompt", sa.Text, default=""),
    sa.Column("track_ids", sa.JSON, default=list),
    sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.Column(
        "taste_snapshot_id",
        sa.Integer,
        sa.ForeignKey("taste_profile_snapshots.id"),
        nullable=True,
    ),
)
