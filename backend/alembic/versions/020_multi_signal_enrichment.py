"""Add multi-signal enrichment columns for CLAP, MERT, captions, and AI tags.

Revision ID: 020
Revises: 019
Create Date: 2026-04-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "020"
down_revision = "019"


def upgrade() -> None:
    # Per-user enrichment: add to audio_embeddings
    op.add_column("audio_embeddings", sa.Column("clap_embedding", sa.JSON, nullable=True))
    op.add_column("audio_embeddings", sa.Column("mert_embedding", sa.JSON, nullable=True))

    # Global enrichment: add to audio_embeddings_global
    op.add_column("audio_embeddings_global", sa.Column("clap_embedding", sa.JSON, nullable=True))
    op.add_column("audio_embeddings_global", sa.Column("mert_embedding", sa.JSON, nullable=True))

    # Song-level AI enrichment: add to song_metadata_cache
    op.add_column("song_metadata_cache", sa.Column("ai_caption", sa.Text, nullable=True))
    op.add_column("song_metadata_cache", sa.Column("ai_tags", sa.JSON, nullable=True))

    # Taste profile: add CLAP and MERT centroids
    op.add_column("taste_profile_snapshots", sa.Column("clap_centroid", sa.JSON, nullable=True))
    op.add_column("taste_profile_snapshots", sa.Column("mert_centroid", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("taste_profile_snapshots", "mert_centroid")
    op.drop_column("taste_profile_snapshots", "clap_centroid")
    op.drop_column("song_metadata_cache", "ai_tags")
    op.drop_column("song_metadata_cache", "ai_caption")
    op.drop_column("audio_embeddings_global", "mert_embedding")
    op.drop_column("audio_embeddings_global", "clap_embedding")
    op.drop_column("audio_embeddings", "mert_embedding")
    op.drop_column("audio_embeddings", "clap_embedding")
