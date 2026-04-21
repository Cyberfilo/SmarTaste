"""Drop 10 dead tables no longer used by any code.

Tables removed:
- lastfm_tags_cache — was populated by Last.fm API, never used in scoring
- kg_artists — was populated by MusicBrainz, never used in scoring
- kg_relationships — was populated by MusicBrainz credits, never used in scoring
- acousticbrainz_cache — AcousticBrainz API shut down
- lastfm_similar_tracks — was populated by Last.fm, never used in scoring
- isrc_spotify_mapping — was used by MusicBrainz resolve_spotify_id, now removed
- bandit_arms — multi-armed bandit state, never used in any code
- listening_history — defined in schema, never populated or queried
- play_count_proxy — defined in schema, never populated or queried
- recommendation_feedback — defined in schema, never populated or queried

Revision ID: 022
Revises: 021
Create Date: 2026-04-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "022"
down_revision = "021"


def upgrade() -> None:
    # Drop indexes on tables being removed (if they exist)
    op.drop_index("ix_lastfm_tags_cache_entity_id", "lastfm_tags_cache", if_exists=True)
    op.drop_index("ix_play_count_proxy_user_id", "play_count_proxy", if_exists=True)
    op.drop_index("ix_lastfm_similar_source", "lastfm_similar_tracks", if_exists=True)
    op.drop_index("ix_kg_relationships_source_mbid", "kg_relationships", if_exists=True)
    op.drop_index("ix_kg_relationships_target_mbid", "kg_relationships", if_exists=True)
    op.drop_index("ix_bandit_arms_user_id", "bandit_arms", if_exists=True)
    op.drop_index("ix_listening_history_user_id", "listening_history", if_exists=True)
    op.drop_index("ix_listening_history_song_id", "listening_history", if_exists=True)
    op.drop_index("ix_recommendation_feedback_user_id", "recommendation_feedback", if_exists=True)
    op.drop_index(
        "ix_recommendation_feedback_catalog_id", "recommendation_feedback", if_exists=True
    )

    # Drop the tables
    op.drop_table("lastfm_similar_tracks")
    op.drop_table("lastfm_tags_cache")
    op.drop_table("acousticbrainz_cache")
    op.drop_table("kg_relationships")
    op.drop_table("kg_artists")
    op.drop_table("isrc_spotify_mapping")
    op.drop_table("bandit_arms")
    op.drop_table("listening_history")
    op.drop_table("play_count_proxy")
    op.drop_table("recommendation_feedback")


def downgrade() -> None:
    # Recreate listening_history
    op.create_table(
        "listening_history",
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

    # Recreate recommendation_feedback
    op.create_table(
        "recommendation_feedback",
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

    # Recreate play_count_proxy
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
    op.create_index("ix_play_count_proxy_user_id", "play_count_proxy", ["user_id"])

    # Recreate isrc_spotify_mapping
    op.create_table(
        "isrc_spotify_mapping",
        sa.Column("isrc", sa.Text, primary_key=True),
        sa.Column("spotify_id", sa.Text, nullable=True),
        sa.Column("resolved_via", sa.Text, nullable=False, server_default="unknown"),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Recreate kg_artists
    op.create_table(
        "kg_artists",
        sa.Column("mbid", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("disambiguation", sa.Text, server_default=""),
        sa.Column("genres", sa.JSON, server_default="[]"),
        sa.Column("embedding", sa.JSON, nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Recreate kg_relationships
    op.create_table(
        "kg_relationships",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_mbid", sa.Text, nullable=False, index=True),
        sa.Column("target_mbid", sa.Text, nullable=False, index=True),
        sa.Column("relation_type", sa.Text, nullable=False),
        sa.Column("weight", sa.Float, server_default="1.0"),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Recreate bandit_arms
    op.create_table(
        "bandit_arms",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("context_key", sa.Text, nullable=False),
        sa.Column("alpha", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("beta", sa.Float, nullable=False, server_default="1.0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "context_key", name="uq_bandit_user_context"),
    )

    # Recreate lastfm_tags_cache
    op.create_table(
        "lastfm_tags_cache",
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Text, nullable=False),
        sa.Column("tags", sa.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("entity_type", "entity_id"),
    )
    op.create_index("ix_lastfm_tags_cache_entity_id", "lastfm_tags_cache", ["entity_id"])

    # Recreate acousticbrainz_cache
    op.create_table(
        "acousticbrainz_cache",
        sa.Column("mbid", sa.Text, primary_key=True),
        sa.Column("mood_aggressive", sa.Float, nullable=True),
        sa.Column("mood_happy", sa.Float, nullable=True),
        sa.Column("mood_relaxed", sa.Float, nullable=True),
        sa.Column("mood_sad", sa.Float, nullable=True),
        sa.Column("mood_party", sa.Float, nullable=True),
        sa.Column("mood_electronic", sa.Float, nullable=True),
        sa.Column("mood_acoustic", sa.Float, nullable=True),
        sa.Column("danceability", sa.Float, nullable=True),
        sa.Column("gender", sa.Text, nullable=True),
        sa.Column("voice_instrumental", sa.Text, nullable=True),
        sa.Column("tonal_atonal", sa.Text, nullable=True),
        sa.Column("genre_probabilities", sa.JSON, server_default="{}"),
        sa.Column("average_loudness", sa.Float, nullable=True),
        sa.Column("bpm", sa.Float, nullable=True),
        sa.Column("key", sa.Text, nullable=True),
        sa.Column("scale", sa.Text, nullable=True),
    )

    # Recreate lastfm_similar_tracks
    op.create_table(
        "lastfm_similar_tracks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_artist", sa.Text, nullable=False),
        sa.Column("source_title", sa.Text, nullable=False),
        sa.Column("similar_artist", sa.Text, nullable=False),
        sa.Column("similar_title", sa.Text, nullable=False),
        sa.Column("similarity_score", sa.Float, nullable=False),
        sa.Column("similar_mbid", sa.Text, nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_lastfm_similar_source",
        "lastfm_similar_tracks",
        ["source_artist", "source_title"],
    )
