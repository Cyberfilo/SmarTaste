"""Add is_admin column to users + set initial admin.

Revision ID: 011
Revises: 010
Create Date: 2026-03-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default="false"),
    )
    # Set the initial admin user
    op.execute(
        "UPDATE users SET is_admin = true WHERE email = 'filo.gametech@gmail.com'"
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
