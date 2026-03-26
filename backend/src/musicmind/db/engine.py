"""Async SQLAlchemy engine factory for PostgreSQL."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_engine(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
    echo: bool = False,
) -> AsyncEngine:
    """Create an async SQLAlchemy engine with connection pooling.

    Args:
        database_url: PostgreSQL connection string (postgresql+asyncpg://...).
        pool_size: Number of persistent connections in the pool.
        max_overflow: Max temporary connections above pool_size.
        pool_timeout: Seconds to wait for a connection from the pool.
        pool_recycle: Seconds before a connection is recycled.
        echo: If True, log all SQL statements.
    """
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        echo=echo,
    )
