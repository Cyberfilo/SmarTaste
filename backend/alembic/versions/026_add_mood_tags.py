"""Add mood_tags to global_song_cache + mood_distribution to taste_profile_snapshots.

V 6.388 — OpenAI-backed structured mood tagging so the scorer has a real
affect/valence signal (separates sad-reflective rap from hype rap even
when CLAP sees them as neighbors).

Revision ID: 026
Revises: 025
Create Date: 2026-04-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "026"
down_revision = "025"


def upgrade() -> None:
    op.add_column(
        "global_song_cache",
        sa.Column(
            "mood_tags",
            sa.JSON,
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "taste_profile_snapshots",
        sa.Column(
            "mood_distribution",
            sa.JSON,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("taste_profile_snapshots", "mood_distribution")
    op.drop_column("global_song_cache", "mood_tags")
