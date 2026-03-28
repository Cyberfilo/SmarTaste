"""Add user_api_keys table.

Revision ID: 005
Revises: 004
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_api_keys",
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service", sa.Text, nullable=False, server_default="anthropic"),
        sa.Column("api_key_encrypted", sa.Text, nullable=False),
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
        sa.PrimaryKeyConstraint("user_id", "service"),
    )


def downgrade() -> None:
    op.drop_table("user_api_keys")
