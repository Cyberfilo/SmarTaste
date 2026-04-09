"""Add standalone user_id indexes for per-user query performance.

Tables with composite PK (catalog_id, user_id) only have an index on
catalog_id as the leading column. Queries filtering by user_id alone
(enrichment status, profile builds, admin dashboard) do full table scans.
This migration adds standalone user_id indexes to all affected tables.

Revision ID: 018
Revises: 017
Create Date: 2026-04-09
"""

from __future__ import annotations

from alembic import op

revision = "018"
down_revision = "017"


def upgrade() -> None:
    # song_metadata_cache: 37MB, most queried per-user table
    op.create_index(
        "ix_song_metadata_cache_user_id",
        "song_metadata_cache",
        ["user_id"],
        if_not_exists=True,
    )
    # audio_features_cache: 2MB, queried for enrichment status
    op.create_index(
        "ix_audio_features_cache_user_id",
        "audio_features_cache",
        ["user_id"],
        if_not_exists=True,
    )
    # artist_cache: small but queried per-user
    op.create_index(
        "ix_artist_cache_user_id",
        "artist_cache",
        ["user_id"],
        if_not_exists=True,
    )
    # play_count_proxy: queried per-user for profile building
    op.create_index(
        "ix_play_count_proxy_user_id",
        "play_count_proxy",
        ["user_id"],
        if_not_exists=True,
    )
    # sound_classification_cache: small but per-user
    op.create_index(
        "ix_sound_classification_cache_user_id",
        "sound_classification_cache",
        ["user_id"],
        if_not_exists=True,
    )
    # audio_embeddings: per-user
    op.create_index(
        "ix_audio_embeddings_user_id",
        "audio_embeddings",
        ["user_id"],
        if_not_exists=True,
    )
    # recommendation_feedback: queried per-user for weight optimization
    op.create_index(
        "ix_recommendation_feedback_user_id",
        "recommendation_feedback",
        ["user_id"],
        if_not_exists=True,
    )
    # lastfm_tags_cache: queried by entity_id frequently in backfill loops
    op.create_index(
        "ix_lastfm_tags_cache_entity_id",
        "lastfm_tags_cache",
        ["entity_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_song_metadata_cache_user_id", "song_metadata_cache")
    op.drop_index("ix_audio_features_cache_user_id", "audio_features_cache")
    op.drop_index("ix_artist_cache_user_id", "artist_cache")
    op.drop_index("ix_play_count_proxy_user_id", "play_count_proxy")
    op.drop_index("ix_sound_classification_cache_user_id", "sound_classification_cache")
    op.drop_index("ix_audio_embeddings_user_id", "audio_embeddings")
    op.drop_index("ix_recommendation_feedback_user_id", "recommendation_feedback")
    op.drop_index("ix_lastfm_tags_cache_entity_id", "lastfm_tags_cache")
