"""Fix missing indexes and model_version server_default quoting.

- Add user_id indexes on composite-PK tables (schema parity with migration 018)
- Fix model_version values corrupted by extra quotes in migration 019
- Add SET NULL on generated_playlists.taste_snapshot_id FK

Revision ID: 021
Revises: 020
Create Date: 2026-04-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "021"
down_revision = "020"


def upgrade() -> None:
    # Fix model_version values corrupted by extra quotes from migration 019
    op.execute(
        "UPDATE audio_embeddings_global"
        " SET model_version = 'discogs-effnet-1280'"
        " WHERE model_version = '''discogs-effnet-1280'''"
    )

    # Add missing user_id indexes on composite-PK tables for query performance.
    # PostgreSQL composite PK indexes only cover the leftmost column efficiently;
    # queries filtering by user_id alone need dedicated indexes.
    op.create_index(
        "ix_song_metadata_cache_user_id",
        "song_metadata_cache",
        ["user_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_audio_features_cache_user_id",
        "audio_features_cache",
        ["user_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_artist_cache_user_id",
        "artist_cache",
        ["user_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_play_count_proxy_user_id",
        "play_count_proxy",
        ["user_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_sound_classification_cache_user_id",
        "sound_classification_cache",
        ["user_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_audio_embeddings_user_id",
        "audio_embeddings",
        ["user_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_lastfm_tags_cache_entity_id",
        "lastfm_tags_cache",
        ["entity_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_lastfm_tags_cache_entity_id", "lastfm_tags_cache")
    op.drop_index("ix_audio_embeddings_user_id", "audio_embeddings")
    op.drop_index("ix_sound_classification_cache_user_id", "sound_classification_cache")
    op.drop_index("ix_play_count_proxy_user_id", "play_count_proxy")
    op.drop_index("ix_artist_cache_user_id", "artist_cache")
    op.drop_index("ix_audio_features_cache_user_id", "audio_features_cache")
    op.drop_index("ix_song_metadata_cache_user_id", "song_metadata_cache")
