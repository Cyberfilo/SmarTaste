"""Initial schema with multi-user tables.

Revision ID: 001
Revises:
Create Date: 2026-03-26
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Core User Tables ─────────────────────────────────────────────────

    op.create_table(
        "users",
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

    op.create_table(
        "service_connections",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
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
    op.create_index("ix_service_connections_user_id", "service_connections", ["user_id"])

    # ── Adapted Data Tables ──────────────────────────────────────────────

    op.create_table(
        "listening_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("song_id", sa.Text, nullable=False),
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
    op.create_index("ix_listening_history_user_id", "listening_history", ["user_id"])
    op.create_index("ix_listening_history_song_id", "listening_history", ["song_id"])

    op.create_table(
        "song_metadata_cache",
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

    op.create_table(
        "artist_cache",
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

    op.create_table(
        "taste_profile_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
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
    op.create_index(
        "ix_taste_profile_snapshots_user_id", "taste_profile_snapshots", ["user_id"]
    )

    op.create_table(
        "recommendation_feedback",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("catalog_id", sa.Text, nullable=False),
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
    op.create_index(
        "ix_recommendation_feedback_user_id", "recommendation_feedback", ["user_id"]
    )
    op.create_index(
        "ix_recommendation_feedback_catalog_id", "recommendation_feedback", ["catalog_id"]
    )

    op.create_table(
        "audio_features_cache",
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

    op.create_table(
        "sound_classification_cache",
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

    op.create_table(
        "play_count_proxy",
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

    op.create_table(
        "generated_playlists",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
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
    op.create_index("ix_generated_playlists_user_id", "generated_playlists", ["user_id"])


def downgrade() -> None:
    # Drop in reverse order of creation
    op.drop_table("generated_playlists")
    op.drop_table("play_count_proxy")
    op.drop_table("sound_classification_cache")
    op.drop_table("audio_features_cache")
    op.drop_table("recommendation_feedback")
    op.drop_table("taste_profile_snapshots")
    op.drop_table("artist_cache")
    op.drop_table("song_metadata_cache")
    op.drop_table("listening_history")
    op.drop_table("service_connections")
    op.drop_table("users")
