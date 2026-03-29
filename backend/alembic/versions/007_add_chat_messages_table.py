"""Add chat_messages table for normalized chat storage.

Replaces JSON blob storage in chat_conversations.messages with a proper
relational table. The old messages column is kept for backward compatibility
during migration — new writes go to chat_messages, reads check both.

Revision ID: 007
Revises: 006
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "conversation_id",
            sa.Text,
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("tool_calls", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_messages")
