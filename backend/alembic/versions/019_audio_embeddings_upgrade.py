"""Upgrade audio embeddings for 1,280-dim Essentia EffNet ONNX.

- Add embedding_dim column to audio_embeddings (distinguishes 128 vs 1280)
- Mark existing rows as legacy 128-dim
- Add audio_embeddings_global table (ISRC-keyed, shared across users)
- Add embedding_centroid column to taste_profile_snapshots

Revision ID: 019
Revises: 018
Create Date: 2026-04-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"


def upgrade() -> None:
    # Add embedding_dim to audio_embeddings
    op.add_column(
        "audio_embeddings",
        sa.Column("embedding_dim", sa.Integer, server_default="128"),
    )

    # Mark existing rows as legacy 128-dim
    op.execute(
        "UPDATE audio_embeddings SET model_version = 'discogs-effnet-bs64-128'"
        " WHERE model_version = 'discogs-effnet-bs64'"
        " OR model_version IS NULL"
    )

    # Create ISRC-keyed global embeddings table
    op.create_table(
        "audio_embeddings_global",
        sa.Column("isrc", sa.Text, primary_key=True),
        sa.Column("embedding", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("embedding_dim", sa.Integer, nullable=False, server_default="1280"),
        sa.Column("model_version", sa.Text, server_default="discogs-effnet-1280"),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Add embedding centroid to taste profile snapshots
    op.add_column(
        "taste_profile_snapshots",
        sa.Column("embedding_centroid", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("taste_profile_snapshots", "embedding_centroid")
    op.drop_table("audio_embeddings_global")
    op.drop_column("audio_embeddings", "embedding_dim")
