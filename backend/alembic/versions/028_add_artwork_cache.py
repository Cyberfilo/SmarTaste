"""Add artwork_cache table for caching downloaded artwork image bytes.

Populated by the worker's artwork_backfill phase (between ISRC backfill
and audio-bytes backfill). Artwork URLs are stable so no TTL cleanup.

Revision ID: 028
Revises: 027
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "028"
down_revision = "027"


def upgrade() -> None:
    op.create_table(
        "artwork_cache",
        sa.Column("catalog_id", sa.Text, primary_key=True),
        sa.Column("image_data", sa.LargeBinary, nullable=False),
        sa.Column("content_type", sa.Text, server_default="image/jpeg"),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("artwork_cache")
