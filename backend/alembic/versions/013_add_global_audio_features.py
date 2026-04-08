"""Add global audio features cache keyed by ISRC.

Shared across all users — enrichment cost paid once per song.
Populated from existing per-user audio_features_cache data.

Revision ID: 013
Revises: 012
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audio_features_global",
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

    # Backfill from existing per-user data: take the best enrichment per ISRC
    op.execute("""
        INSERT INTO audio_features_global (isrc, tempo, energy, brightness, danceability,
            acousticness, valence_proxy, beat_strength, key, scale, instrumentalness,
            loudness, feature_source, analyzed_at)
        SELECT DISTINCT ON (smc.isrc)
            smc.isrc, af.tempo, af.energy, af.brightness, af.danceability,
            af.acousticness, af.valence_proxy, af.beat_strength, af.key, af.scale,
            af.instrumentalness, af.loudness, af.feature_source, af.analyzed_at
        FROM audio_features_cache af
        JOIN song_metadata_cache smc ON af.catalog_id = smc.catalog_id AND af.user_id = smc.user_id
        WHERE smc.isrc IS NOT NULL AND smc.isrc != '' AND af.energy IS NOT NULL
        ORDER BY smc.isrc, af.analyzed_at DESC
        ON CONFLICT (isrc) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("audio_features_global")
