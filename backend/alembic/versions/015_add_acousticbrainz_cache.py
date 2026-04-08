"""Add acousticbrainz_cache table for bulk high-level descriptors.

Stores mood probabilities, danceability, genre predictions from the
AcousticBrainz CC0 dump (~7M recordings). Keyed by MusicBrainz recording ID.

Revision ID: 015
Revises: 014
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("acousticbrainz_cache")
