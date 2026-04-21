"""Add mood_scores to global_song_cache for hybrid-output mood classifier.

V 6.389 — upgrade from discrete 1-3 tags to hybrid (primary tag + sparse
score vector). `mood_tags` stays for UI display and backward compat; the
new `mood_scores` column powers the mood_match cosine with actual
intensity information.

Revision ID: 027
Revises: 026
Create Date: 2026-04-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "027"
down_revision = "026"


def upgrade() -> None:
    op.add_column(
        "global_song_cache",
        sa.Column(
            "mood_scores",
            sa.JSON,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("global_song_cache", "mood_scores")
