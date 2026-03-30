"""Fix composite primary keys for multi-user isolation.

artist_cache, audio_features_cache, and sound_classification_cache all had
single-column PKs that caused data collisions between users. Add user_id
to each composite primary key.

Revision ID: 006
Revises: 005
"""

from __future__ import annotations

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # artist_cache: artist_id -> (artist_id, user_id)
    op.drop_constraint("artist_cache_pkey", "artist_cache", type_="primary")
    op.create_primary_key("artist_cache_pkey", "artist_cache", ["artist_id", "user_id"])

    # audio_features_cache: catalog_id -> (catalog_id, user_id)
    op.drop_constraint("audio_features_cache_pkey", "audio_features_cache", type_="primary")
    op.create_primary_key(
        "audio_features_cache_pkey", "audio_features_cache", ["catalog_id", "user_id"]
    )

    # sound_classification_cache: catalog_id -> (catalog_id, user_id)
    op.drop_constraint(
        "sound_classification_cache_pkey", "sound_classification_cache", type_="primary"
    )
    op.create_primary_key(
        "sound_classification_cache_pkey", "sound_classification_cache", ["catalog_id", "user_id"]
    )


def downgrade() -> None:
    # Revert to single-column PKs (may fail if duplicate rows exist)
    op.drop_constraint(
        "sound_classification_cache_pkey", "sound_classification_cache", type_="primary"
    )
    op.create_primary_key(
        "sound_classification_cache_pkey", "sound_classification_cache", ["catalog_id"]
    )

    op.drop_constraint("audio_features_cache_pkey", "audio_features_cache", type_="primary")
    op.create_primary_key(
        "audio_features_cache_pkey", "audio_features_cache", ["catalog_id"]
    )

    op.drop_constraint("artist_cache_pkey", "artist_cache", type_="primary")
    op.create_primary_key("artist_cache_pkey", "artist_cache", ["artist_id"])
