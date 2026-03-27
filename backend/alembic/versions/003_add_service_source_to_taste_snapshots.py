"""Add service_source column to taste_profile_snapshots for per-service staleness isolation.

Revision ID: 003
Revises: 002
Create Date: 2026-03-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "taste_profile_snapshots",
        sa.Column(
            "service_source",
            sa.Text,
            nullable=False,
            server_default="apple_music",
        ),
    )


def downgrade() -> None:
    op.drop_column("taste_profile_snapshots", "service_source")
