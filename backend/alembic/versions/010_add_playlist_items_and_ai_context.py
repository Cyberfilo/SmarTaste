"""Add playlist_items table and AI context to generated_playlists.

Revision ID: 010
Revises: 009
Create Date: 2026-03-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand generated_playlists with AI fields
    op.add_column(
        "generated_playlists",
        sa.Column("ai_context", sa.Text, server_default=""),
    )
    op.add_column(
        "generated_playlists",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "generated_playlists",
        sa.Column("track_count", sa.Integer, server_default="0"),
    )

    # New table: individual playlist tracks with metadata
    op.create_table(
        "playlist_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "playlist_id",
            sa.Integer,
            sa.ForeignKey("generated_playlists.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("catalog_id", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, server_default=""),
        sa.Column("artist_name", sa.Text, server_default=""),
        sa.Column("album_name", sa.Text, server_default=""),
        sa.Column("artwork_url", sa.Text, server_default=""),
        sa.Column("genre_names", sa.JSON, server_default="[]"),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("added_by", sa.Text, server_default="user"),
    )


def downgrade() -> None:
    op.drop_table("playlist_items")
    op.drop_column("generated_playlists", "track_count")
    op.drop_column("generated_playlists", "updated_at")
    op.drop_column("generated_playlists", "ai_context")
