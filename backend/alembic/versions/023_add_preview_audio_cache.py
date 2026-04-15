"""Add preview_audio_cache table for caching downloaded preview audio bytes.

Deezer preview URLs expire after ~24h, causing 403s during enrichment.
This table stores the raw audio bytes so re-enrichment doesn't require
a fresh download. Fully-enriched entries are cleaned up by the worker.

Revision ID: 023
Revises: 022
Create Date: 2026-04-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "023"
down_revision = "022"


def upgrade() -> None:
    op.create_table(
        "preview_audio_cache",
        sa.Column("catalog_id", sa.Text, primary_key=True),
        sa.Column("audio_data", sa.LargeBinary, nullable=False),
        sa.Column("content_type", sa.Text, server_default="audio/mpeg"),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "enrichment_complete",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_table("preview_audio_cache")
