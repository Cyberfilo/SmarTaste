"""Add playlist_briefs + playlist_brief_songs (V 6.440 — playlist chat).

Stores the user's freeform "what kind of music do you want for this
playlist" brief + the songs they mentioned inline. target_vector is
populated by a gpt-5.4 synthesis call in a follow-up commit (5b).

Revision ID: 031
Revises: 030
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playlist_briefs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("playlist_id", sa.Text, nullable=False),
        sa.Column("service", sa.Text, nullable=False),
        sa.Column("brief_text", sa.Text, nullable=False),
        sa.Column("target_vector", sa.JSON, nullable=True),
        sa.Column("synthesis_error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_playlist_briefs_user_playlist",
        "playlist_briefs",
        ["user_id", "playlist_id", "created_at"],
    )

    op.create_table(
        "playlist_brief_songs",
        sa.Column(
            "brief_id",
            sa.Text,
            sa.ForeignKey("playlist_briefs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("catalog_id", sa.Text, nullable=False),
        sa.Column("isrc", sa.Text, nullable=True),
        sa.Column(
            "role",
            sa.Text,
            nullable=False,
            server_default="referenced",
            comment="referenced | target_example | anti_example",
        ),
        sa.Column("reason_text", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("brief_id", "position"),
    )


def downgrade() -> None:
    op.drop_table("playlist_brief_songs")
    op.drop_index("ix_playlist_briefs_user_playlist", table_name="playlist_briefs")
    op.drop_table("playlist_briefs")
