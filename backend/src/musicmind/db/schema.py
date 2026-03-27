"""SQLAlchemy Core table definitions for MusicMind Web.

Multi-user PostgreSQL schema with user_id foreign keys on all data tables.
Adapted from the original single-user SQLite schema with timezone-aware timestamps
and native PostgreSQL JSON/Boolean types.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

# ── Core User Tables ─────────────────────────────────────────────────────────

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("email", sa.Text, nullable=False, unique=True),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

service_connections = sa.Table(
    "service_connections",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("service", sa.Text, nullable=False),
    sa.Column("access_token_encrypted", sa.Text, nullable=False),
    sa.Column("refresh_token_encrypted", sa.Text, nullable=True),
    sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("service_user_id", sa.Text, nullable=True),
    sa.Column(
        "connected_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.UniqueConstraint("user_id", "service", name="uq_user_service"),
)

refresh_tokens = sa.Table(
    "refresh_tokens",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

# ── Adapted Data Tables (all with user_id FK) ───────────────────────────────

listening_history = sa.Table(
    "listening_history",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("song_id", sa.Text, nullable=False, index=True),
    sa.Column("song_name", sa.Text, nullable=False),
    sa.Column("artist_name", sa.Text, nullable=False),
    sa.Column("album_name", sa.Text, server_default=""),
    sa.Column("genre_names", sa.JSON, server_default="[]"),
    sa.Column("duration_ms", sa.Integer, nullable=True),
    sa.Column(
        "observed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("position_in_recent", sa.Integer, nullable=True),
    sa.Column("source", sa.Text, nullable=False, server_default="recently_played"),
    sa.Column("service_source", sa.Text, nullable=False, server_default="apple_music"),
)

song_metadata_cache = sa.Table(
    "song_metadata_cache",
    metadata,
    sa.Column("catalog_id", sa.Text, nullable=False),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("library_id", sa.Text, nullable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("artist_name", sa.Text, nullable=False),
    sa.Column("album_name", sa.Text, server_default=""),
    sa.Column("genre_names", sa.JSON, server_default="[]"),
    sa.Column("duration_ms", sa.Integer, nullable=True),
    sa.Column("release_date", sa.Text, nullable=True),
    sa.Column("isrc", sa.Text, nullable=True),
    sa.Column("editorial_notes", sa.Text, nullable=True),
    sa.Column("audio_traits", sa.JSON, server_default="[]"),
    sa.Column("has_lyrics", sa.Boolean, server_default=sa.text("false")),
    sa.Column("content_rating", sa.Text, nullable=True),
    sa.Column("artwork_bg_color", sa.Text, nullable=True),
    sa.Column("artwork_url_template", sa.Text, nullable=True),
    sa.Column("preview_url", sa.Text, nullable=True),
    sa.Column("user_rating", sa.Integer, nullable=True),
    sa.Column("date_added_to_library", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "fetched_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("service_source", sa.Text, nullable=False, server_default="apple_music"),
    sa.PrimaryKeyConstraint("catalog_id", "user_id"),
)

artist_cache = sa.Table(
    "artist_cache",
    metadata,
    sa.Column("artist_id", sa.Text, primary_key=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("genre_names", sa.JSON, server_default="[]"),
    sa.Column("top_song_ids", sa.JSON, server_default="[]"),
    sa.Column("similar_artist_ids", sa.JSON, server_default="[]"),
    sa.Column(
        "fetched_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("service_source", sa.Text, nullable=False, server_default="apple_music"),
)

taste_profile_snapshots = sa.Table(
    "taste_profile_snapshots",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "computed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("genre_vector", sa.JSON, server_default="{}"),
    sa.Column("top_artists", sa.JSON, server_default="[]"),
    sa.Column("audio_trait_preferences", sa.JSON, server_default="{}"),
    sa.Column("release_year_distribution", sa.JSON, server_default="{}"),
    sa.Column("familiarity_score", sa.Float, server_default="0.0"),
    sa.Column("total_songs_analyzed", sa.Integer, server_default="0"),
    sa.Column("listening_hours_estimated", sa.Float, server_default="0.0"),
)

recommendation_feedback = sa.Table(
    "recommendation_feedback",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("catalog_id", sa.Text, nullable=False, index=True),
    sa.Column("recommendation_id", sa.Text, nullable=True),
    sa.Column("feedback_type", sa.Text, nullable=False),
    sa.Column("predicted_score", sa.Float, nullable=True),
    sa.Column("weight_snapshot", sa.JSON, server_default="{}"),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

audio_features_cache = sa.Table(
    "audio_features_cache",
    metadata,
    sa.Column("catalog_id", sa.Text, primary_key=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("tempo", sa.Float, nullable=True),
    sa.Column("energy", sa.Float, nullable=True),
    sa.Column("brightness", sa.Float, nullable=True),
    sa.Column("danceability", sa.Float, nullable=True),
    sa.Column("acousticness", sa.Float, nullable=True),
    sa.Column("valence_proxy", sa.Float, nullable=True),
    sa.Column("beat_strength", sa.Float, nullable=True),
    sa.Column(
        "analyzed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

sound_classification_cache = sa.Table(
    "sound_classification_cache",
    metadata,
    sa.Column("catalog_id", sa.Text, primary_key=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("labels", sa.JSON, server_default="{}"),
    sa.Column(
        "analyzed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("analyzer_version", sa.Text, server_default=""),
)

play_count_proxy = sa.Table(
    "play_count_proxy",
    metadata,
    sa.Column("song_id", sa.Text, nullable=False),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("seen_count", sa.Integer, nullable=False, server_default="1"),
    sa.Column(
        "first_seen",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "last_seen",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint("song_id", "user_id"),
)

generated_playlists = sa.Table(
    "generated_playlists",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("apple_playlist_id", sa.Text, nullable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, server_default=""),
    sa.Column("vibe_prompt", sa.Text, server_default=""),
    sa.Column("track_ids", sa.JSON, server_default="[]"),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "taste_snapshot_id",
        sa.Integer,
        sa.ForeignKey("taste_profile_snapshots.id"),
        nullable=True,
    ),
    sa.Column("service_source", sa.Text, nullable=False, server_default="apple_music"),
)
