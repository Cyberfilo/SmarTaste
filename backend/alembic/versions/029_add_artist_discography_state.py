"""Add artist_discography_state — tracks which artists have had their
full catalog walked, so the worker's artist-deepening phase doesn't
re-fetch every cycle.

Keyed by a normalized artist name (lowercased + trimmed) for reliable
cross-service matching. Records the service used, total tracks found,
last-fetched timestamp, and last-error for ops visibility.

Revision ID: 029
Revises: 028
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "029"
down_revision = "028"


def upgrade() -> None:
    op.create_table(
        "artist_discography_state",
        sa.Column("artist_name_norm", sa.Text, primary_key=True),
        sa.Column("artist_name", sa.Text, nullable=False),
        sa.Column("service_source", sa.Text, nullable=False),
        sa.Column("tracks_found", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "deepened_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("artist_discography_state")
