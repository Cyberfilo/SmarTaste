"""Database lifecycle management — async SQLite connection with auto-creation."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from musicmind.db.schema import metadata

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))


class DatabaseManager:
    """Manages the async SQLite database connection and schema."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._engine: AsyncEngine | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._engine

    async def initialize(self) -> None:
        """Create the database and all tables if they don't exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{self._db_path}"
        self._engine = create_async_engine(url, echo=False)

        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

        logger.info("Database initialized at %s", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            logger.info("Database connection closed")
