"""Add lastfm_similar_tracks table for collaborative filtering.

Revision ID: 014
Revises: 013
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("lastfm_similar_tracks")
