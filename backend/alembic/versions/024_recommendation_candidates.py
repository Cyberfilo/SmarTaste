"""Add recommendation_candidates table for worker-driven discovery.

Stores per-user discovery candidates (similar/genre/editorial/chart) so the
recommendations API becomes a read-only score+rank instead of live API calls.
Worker populates this table; API consumes it.

Revision ID: 024
Revises: 023
Create Date: 2026-04-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "024"
down_revision = "023"


def upgrade() -> None:
    op.create_table(
        "recommendation_candidates",
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("catalog_id", sa.Text, nullable=False),
        sa.Column("strategy_source", sa.Text, nullable=False),
        sa.Column(
            "discovery_weight", sa.Float, nullable=False, server_default="0.0",
        ),
        sa.Column("service_source", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "catalog_id", "strategy_source",
            name="pk_recommendation_candidates",
        ),
    )
    op.create_index(
        "ix_rc_user_fetched",
        "recommendation_candidates",
        ["user_id", "fetched_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rc_user_fetched", table_name="recommendation_candidates")
    op.drop_table("recommendation_candidates")
