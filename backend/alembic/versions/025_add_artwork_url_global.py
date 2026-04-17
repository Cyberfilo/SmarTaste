"""Add artwork_url to global_song_cache (fixes missing artwork on DB-read path).

Discovery candidates and cobweb tracks persist through global_song_cache.
Without an artwork column, the recommendations API returned empty strings
for artwork on everything sourced from the worker (vs cold-start live API).

Revision ID: 025
Revises: 024
Create Date: 2026-04-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "025"
down_revision = "024"


def upgrade() -> None:
    op.add_column(
        "global_song_cache",
        sa.Column("artwork_url", sa.Text, server_default=""),
    )
    # Backfill from song_metadata_cache where possible — many global songs
    # originated as library songs or were promoted from discography.
    op.execute("""
        UPDATE global_song_cache gsc
        SET artwork_url = smc.artwork_url_template
        FROM song_metadata_cache smc
        WHERE gsc.catalog_id = smc.catalog_id
          AND (gsc.artwork_url IS NULL OR gsc.artwork_url = '')
          AND smc.artwork_url_template IS NOT NULL
          AND smc.artwork_url_template != ''
    """)


def downgrade() -> None:
    op.drop_column("global_song_cache", "artwork_url")
