"""Add indexer + worker cobweb tables.

Splits enrichment into two systems:
1. Per-user indexing (user_indexing_status) — backend-managed, prioritized
2. Global worker (artist_cobweb, global_song_cache) — always running, no user_id on songs

Revision ID: 017
Revises: 016
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_indexing_status",
        sa.Column("user_id", sa.Text, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("step", sa.Integer, nullable=False, server_default="0"),
        sa.Column("step_name", sa.Text, nullable=False, server_default="pending"),
        sa.Column("progress_current", sa.Integer, server_default="0"),
        sa.Column("progress_total", sa.Integer, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "artist_cobweb",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("artist_name", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("priority", sa.Float, server_default="0.5"),
        sa.Column("enriched", sa.Boolean, server_default=sa.text("false")),
        sa.Column("songs_fetched", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "artist_name", name="uq_cobweb_artist"),
    )

    op.create_table(
        "global_song_cache",
        sa.Column("catalog_id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, server_default=""),
        sa.Column("artist_name", sa.Text, server_default=""),
        sa.Column("album_name", sa.Text, server_default=""),
        sa.Column("genre_names", sa.JSON, server_default="[]"),
        sa.Column("isrc", sa.Text, nullable=True, index=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("release_date", sa.Text, nullable=True),
        sa.Column("preview_url", sa.Text, server_default=""),
        sa.Column("service_source", sa.Text, server_default=""),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("global_song_cache")
    op.drop_table("artist_cobweb")
    op.drop_table("user_indexing_status")
