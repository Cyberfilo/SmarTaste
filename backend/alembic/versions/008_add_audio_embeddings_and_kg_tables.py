"""Add audio_embeddings, knowledge graph, bandit, and Last.fm tables.

Supports phases 5-10: Essentia embeddings, knowledge graph, contextual
bandit, and Last.fm tag enrichment.

Revision ID: 008
Revises: 007
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Phase 5: Audio embeddings (128-dim Discogs-EffNet vectors)
    op.create_table(
        "audio_embeddings",
        sa.Column("catalog_id", sa.Text, nullable=False),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("isrc", sa.Text, nullable=True),
        sa.Column("model_version", sa.Text, server_default="discogs-effnet-bs64"),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("catalog_id", "user_id"),
    )

    # Phase 7: Knowledge graph — artists from MusicBrainz
    op.create_table(
        "kg_artists",
        sa.Column("mbid", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("disambiguation", sa.Text, server_default=""),
        sa.Column("genres", sa.JSON, server_default="[]"),
        sa.Column("embedding", sa.JSON, nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Phase 7: Knowledge graph — relationships between artists
    op.create_table(
        "kg_relationships",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_mbid", sa.Text, nullable=False, index=True),
        sa.Column("target_mbid", sa.Text, nullable=False, index=True),
        sa.Column("relation_type", sa.Text, nullable=False),
        sa.Column("weight", sa.Float, server_default="1.0"),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Phase 8: Contextual bandit — Thompson Sampling arm state
    op.create_table(
        "bandit_arms",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("context_key", sa.Text, nullable=False),
        sa.Column("alpha", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("beta", sa.Float, nullable=False, server_default="1.0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "context_key", name="uq_bandit_user_context"),
    )

    # Phase 10: Last.fm tags cache
    op.create_table(
        "lastfm_tags_cache",
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Text, nullable=False),
        sa.Column("tags", sa.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("entity_type", "entity_id"),
    )


def downgrade() -> None:
    op.drop_table("lastfm_tags_cache")
    op.drop_table("bandit_arms")
    op.drop_table("kg_relationships")
    op.drop_table("kg_artists")
    op.drop_table("audio_embeddings")
