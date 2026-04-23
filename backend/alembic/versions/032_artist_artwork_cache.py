"""Add artist_artwork_cache — persistent artist-artwork URL cache (V 6.460).

The in-memory `_ARTIST_ARTWORK_CACHE` dict in taste/insights.py resets on
every Railway deploy / scale-out / process restart, forcing a full Apple
catalog fan-out on the next /taste/profile request. That's the 5.5s cold-
start cost visible in prod logs.

This table persists the same mapping (artist_name_lower -> artwork URL)
across deploys. Empty string is a valid value — means "we tried Apple and
it returned nothing"; don't keep re-fetching.

Revision ID: 032
Revises: 031
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artist_artwork_cache",
        sa.Column("artist_name_lower", sa.Text, primary_key=True),
        sa.Column("artwork_url", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("artist_artwork_cache")
