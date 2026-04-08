"""Add worker_status table for live worker heartbeat.

Single-row table the worker updates with current phase, progress,
and timestamp. The admin dashboard reads this to show real-time
worker activity.

Revision ID: 016
Revises: 015
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_status",
        sa.Column("id", sa.Integer, primary_key=True, server_default="1"),
        sa.Column("phase", sa.Text, nullable=False, server_default="idle"),
        sa.Column("detail", sa.Text, server_default=""),
        sa.Column("progress_current", sa.Integer, server_default="0"),
        sa.Column("progress_total", sa.Integer, server_default="0"),
        sa.Column("cycle", sa.Integer, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Insert the singleton row
    op.execute("INSERT INTO worker_status (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("worker_status")
