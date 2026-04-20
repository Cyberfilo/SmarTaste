"""SQLAlchemy Core table definitions for SmarTaste.

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
    sa.Column("is_admin", sa.Boolean, nullable=False, server_default="false"),
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

user_api_keys = sa.Table(
    "user_api_keys",
    metadata,
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("service", sa.Text, nullable=False, server_default="anthropic"),
    sa.Column("api_key_encrypted", sa.Text, nullable=False),
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
    sa.PrimaryKeyConstraint("user_id", "service"),
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
    sa.Column("ai_caption", sa.Text, nullable=True),
    sa.Column("ai_tags", sa.JSON, nullable=True),
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
    sa.Column("artist_id", sa.Text, nullable=False),
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
    sa.PrimaryKeyConstraint("artist_id", "user_id"),
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
    sa.Column("service_source", sa.Text, nullable=False, server_default="apple_music"),
    sa.Column("audio_centroid", sa.JSON, server_default="{}"),
    sa.Column("embedding_centroid", sa.JSON, nullable=True),
    sa.Column("clap_centroid", sa.JSON, nullable=True),
    sa.Column("mert_centroid", sa.JSON, nullable=True),
    # V 6.388: normalized distribution over the mood taxonomy, summed
    # across the user's library mood_tags. Used by the `mood_match`
    # scorer dimension. Nullable while old snapshots age out.
    sa.Column("mood_distribution", sa.JSON, nullable=True),
)

audio_features_cache = sa.Table(
    "audio_features_cache",
    metadata,
    sa.Column("catalog_id", sa.Text, nullable=False),
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
    # Extended fields (added in migration 009)
    sa.Column("key", sa.Text, nullable=True),
    sa.Column("scale", sa.Text, nullable=True),
    sa.Column("instrumentalness", sa.Float, nullable=True),
    sa.Column("loudness", sa.Float, nullable=True),
    # Per-field provenance: {"tempo": "essentia", "energy": "essentia", ...}
    sa.Column("feature_source", sa.JSON, server_default="{}"),
    sa.Column(
        "analyzed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint("catalog_id", "user_id"),
)

audio_features_global = sa.Table(
    "audio_features_global",
    metadata,
    sa.Column("isrc", sa.Text, primary_key=True),
    sa.Column("tempo", sa.Float, nullable=True),
    sa.Column("energy", sa.Float, nullable=True),
    sa.Column("brightness", sa.Float, nullable=True),
    sa.Column("danceability", sa.Float, nullable=True),
    sa.Column("acousticness", sa.Float, nullable=True),
    sa.Column("valence_proxy", sa.Float, nullable=True),
    sa.Column("beat_strength", sa.Float, nullable=True),
    sa.Column("key", sa.Text, nullable=True),
    sa.Column("scale", sa.Text, nullable=True),
    sa.Column("instrumentalness", sa.Float, nullable=True),
    sa.Column("loudness", sa.Float, nullable=True),
    sa.Column("feature_source", sa.JSON, server_default="{}"),
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
    sa.Column("catalog_id", sa.Text, nullable=False),
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
    sa.PrimaryKeyConstraint("catalog_id", "user_id"),
)

chat_conversations = sa.Table(
    "chat_conversations",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("title", sa.Text, nullable=False, server_default=""),
    sa.Column("messages", sa.JSON, server_default="[]"),
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

chat_messages = sa.Table(
    "chat_messages",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "conversation_id",
        sa.Text,
        sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False, server_default=""),
    sa.Column("tool_calls", sa.JSON, nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

audio_embeddings = sa.Table(
    "audio_embeddings",
    metadata,
    sa.Column("catalog_id", sa.Text, nullable=False),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("embedding", sa.JSON, nullable=False, server_default="[]"),
    sa.Column("isrc", sa.Text, nullable=True),
    sa.Column("model_version", sa.Text, server_default="discogs-effnet-1280"),
    sa.Column("embedding_dim", sa.Integer, server_default="128"),
    sa.Column("clap_embedding", sa.JSON, nullable=True),
    sa.Column("mert_embedding", sa.JSON, nullable=True),
    sa.Column(
        "analyzed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint("catalog_id", "user_id"),
)

audio_embeddings_global = sa.Table(
    "audio_embeddings_global",
    metadata,
    sa.Column("isrc", sa.Text, primary_key=True),
    sa.Column("embedding", sa.JSON, nullable=False, server_default="[]"),
    sa.Column("embedding_dim", sa.Integer, nullable=False, server_default="1280"),
    sa.Column("model_version", sa.Text, server_default="discogs-effnet-1280"),
    sa.Column("clap_embedding", sa.JSON, nullable=True),
    sa.Column("mert_embedding", sa.JSON, nullable=True),
    sa.Column(
        "analyzed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
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
        sa.ForeignKey("taste_profile_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("service_source", sa.Text, nullable=False, server_default="apple_music"),
    # AI-powered playlist fields (added in migration 010)
    sa.Column("ai_context", sa.Text, server_default=""),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=True,
    ),
    sa.Column("track_count", sa.Integer, server_default="0"),
)

user_calibration = sa.Table(
    "user_calibration",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("calibration_type", sa.Text, nullable=False),
    sa.Column("item_id", sa.Text, nullable=False),
    sa.Column("item_name", sa.Text, server_default=""),
    sa.Column("weight", sa.Float, nullable=False, server_default="1.0"),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.UniqueConstraint("user_id", "calibration_type", "item_id", name="uq_calibration_entry"),
)

worker_status = sa.Table(
    "worker_status",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, server_default="1"),
    sa.Column("phase", sa.Text, nullable=False, server_default="idle"),
    sa.Column("detail", sa.Text, server_default=""),
    sa.Column("progress_current", sa.Integer, server_default="0"),
    sa.Column("progress_total", sa.Integer, server_default="0"),
    sa.Column("cycle", sa.Integer, server_default="0"),
    sa.Column(
        "started_at",
        sa.DateTime(timezone=True),
        nullable=True,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

# ── Per-User Indexing ────────────────────────────────────────────────────────

user_indexing_status = sa.Table(
    "user_indexing_status",
    metadata,
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("step", sa.Integer, nullable=False, server_default="0"),
    sa.Column("step_name", sa.Text, nullable=False, server_default="pending"),
    sa.Column("progress_current", sa.Integer, server_default="0"),
    sa.Column("progress_total", sa.Integer, server_default="0"),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
)

# ── Global Worker Tables ─────────────────────────────────────────────────────

artist_cobweb = sa.Table(
    "artist_cobweb",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "user_id",
        sa.Text,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("artist_name", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("priority", sa.Float, server_default="0.5"),
    sa.Column("enriched", sa.Boolean, server_default=sa.text("false")),
    sa.Column("songs_fetched", sa.Integer, server_default="0"),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.UniqueConstraint("user_id", "artist_name", name="uq_cobweb_artist"),
)

global_song_cache = sa.Table(
    "global_song_cache",
    metadata,
    sa.Column("catalog_id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, server_default=""),
    sa.Column("artist_name", sa.Text, server_default=""),
    sa.Column("album_name", sa.Text, server_default=""),
    sa.Column("genre_names", sa.JSON, server_default="[]"),
    sa.Column("isrc", sa.Text, nullable=True, index=True),
    sa.Column("duration_ms", sa.Integer, nullable=True),
    sa.Column("release_date", sa.Text, nullable=True),
    sa.Column("preview_url", sa.Text, server_default=""),
    sa.Column("artwork_url", sa.Text, server_default=""),
    sa.Column("service_source", sa.Text, server_default=""),
    sa.Column(
        "fetched_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    # V 6.388: OpenAI-classified mood tags from fixed 12-entry taxonomy.
    # Primary mood first; 1-3 tags per track; empty array when unclassified.
    sa.Column("mood_tags", sa.JSON, nullable=False, server_default="[]"),
)

playlist_items = sa.Table(
    "playlist_items",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "playlist_id",
        sa.Integer,
        sa.ForeignKey("generated_playlists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("catalog_id", sa.Text, nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("name", sa.Text, server_default=""),
    sa.Column("artist_name", sa.Text, server_default=""),
    sa.Column("album_name", sa.Text, server_default=""),
    sa.Column("artwork_url", sa.Text, server_default=""),
    sa.Column("genre_names", sa.JSON, server_default="[]"),
    sa.Column(
        "added_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("added_by", sa.Text, server_default="user"),  # user, ai_expand, ai_improve
)

# ── Preview Audio Cache ─────────────────────────────────────────────────────
# Caches raw preview audio bytes to avoid re-downloading expiring Deezer URLs.
# Worker cleans up rows where enrichment_complete=true or downloaded_at > 7 days.

preview_audio_cache = sa.Table(
    "preview_audio_cache",
    metadata,
    sa.Column("catalog_id", sa.Text, primary_key=True),
    sa.Column("audio_data", sa.LargeBinary, nullable=False),
    sa.Column("content_type", sa.Text, server_default="audio/mpeg"),
    sa.Column("source_url", sa.Text, nullable=True),
    sa.Column(
        "downloaded_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "enrichment_complete",
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    ),
)

# Per-user discovery candidate pool — populated by the worker, consumed by
# the recommendations API. The four discover_* strategies (similar_artist,
# genre_adjacent, editorial, chart) used to run live on every recommendation
# request. They now run in the worker on a refresh cadence; the API reads
# from this table + global_song_cache + audio_embeddings, scores, and ranks.
recommendation_candidates = sa.Table(
    "recommendation_candidates",
    metadata,
    sa.Column("user_id", sa.Text, nullable=False),
    sa.Column("catalog_id", sa.Text, nullable=False),
    sa.Column("strategy_source", sa.Text, nullable=False),
    sa.Column(
        "discovery_weight", sa.Float, nullable=False, server_default="0.0",
    ),
    sa.Column("service_source", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "fetched_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint(
        "user_id", "catalog_id", "strategy_source",
        name="pk_recommendation_candidates",
    ),
    sa.Index("ix_rc_user_fetched", "user_id", "fetched_at"),
)

# ── Performance Indexes ─────────────────────────────────────────────────────
# Composite PK tables index only the first column; queries filtering by
# user_id alone need explicit indexes to avoid full table scans.

sa.Index("ix_song_metadata_cache_user_id", song_metadata_cache.c.user_id)
sa.Index("ix_audio_features_cache_user_id", audio_features_cache.c.user_id)
sa.Index("ix_artist_cache_user_id", artist_cache.c.user_id)
sa.Index("ix_sound_classification_cache_user_id", sound_classification_cache.c.user_id)
sa.Index("ix_audio_embeddings_user_id", audio_embeddings.c.user_id)
