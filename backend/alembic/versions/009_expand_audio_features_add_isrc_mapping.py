"""Expand audio_features_cache with provenance + add isrc_spotify_mapping table.

Revision ID: 009
Revises: 008
Create Date: 2026-03-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand audio_features_cache with new columns
    op.add_column("audio_features_cache", sa.Column("key", sa.Text, nullable=True))
    op.add_column("audio_features_cache", sa.Column("scale", sa.Text, nullable=True))
    op.add_column(
        "audio_features_cache", sa.Column("instrumentalness", sa.Float, nullable=True)
    )
    op.add_column(
        "audio_features_cache", sa.Column("loudness", sa.Float, nullable=True)
    )
    op.add_column(
        "audio_features_cache",
        sa.Column("feature_source", sa.JSON, server_default="{}"),
    )
    op.add_column(
        "audio_features_cache",
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Add audio_centroid to taste profile snapshots
    op.add_column(
        "taste_profile_snapshots",
        sa.Column("audio_centroid", sa.JSON, server_default="{}"),
    )

    # New table: permanent ISRC → Spotify ID cache
    op.create_table(
        "isrc_spotify_mapping",
        sa.Column("isrc", sa.Text, primary_key=True),
        sa.Column("spotify_id", sa.Text, nullable=True),
        sa.Column(
            "resolved_via", sa.Text, nullable=False, server_default="unknown"
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("isrc_spotify_mapping")
    op.drop_column("taste_profile_snapshots", "audio_centroid")
    op.drop_column("audio_features_cache", "enriched_at")
    op.drop_column("audio_features_cache", "feature_source")
    op.drop_column("audio_features_cache", "loudness")
    op.drop_column("audio_features_cache", "instrumentalness")
    op.drop_column("audio_features_cache", "scale")
    op.drop_column("audio_features_cache", "key")
