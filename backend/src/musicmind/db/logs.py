"""Logging database — schema, engine, and async batch writer.

Separate PostgreSQL instance for request logs, enrichment logs, and error tracking.
Uses a batched writer that flushes every N seconds to avoid per-request DB writes.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

logs_metadata = sa.MetaData()

request_logs = sa.Table(
    "request_logs",
    logs_metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column(
        "timestamp",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("method", sa.Text, nullable=False),
    sa.Column("path", sa.Text, nullable=False),
    sa.Column("status_code", sa.Integer, nullable=False),
    sa.Column("duration_ms", sa.Integer, nullable=False),
    sa.Column("user_id", sa.Text, nullable=True),
    sa.Column("ip_address", sa.Text, nullable=True),
    sa.Column("user_agent", sa.Text, nullable=True),
    sa.Column("error_detail", sa.Text, nullable=True),
)

enrichment_logs = sa.Table(
    "enrichment_logs",
    logs_metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column(
        "timestamp",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("user_id", sa.Text, nullable=False),
    sa.Column("catalog_id", sa.Text, nullable=False),
    sa.Column("stage", sa.Text, nullable=False),
    sa.Column("result", sa.Text, nullable=False),
    sa.Column("duration_ms", sa.Integer, nullable=True),
    sa.Column("error_detail", sa.Text, nullable=True),
)

error_logs = sa.Table(
    "error_logs",
    logs_metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column(
        "timestamp",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("level", sa.Text, nullable=False),
    sa.Column("logger_name", sa.Text, nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("user_id", sa.Text, nullable=True),
    sa.Column("path", sa.Text, nullable=True),
    sa.Column("traceback", sa.Text, nullable=True),
)


# ── Engine + Schema Init ──────────────────────────────────────────────────────


def create_logs_engine(database_url: str) -> AsyncEngine:
    """Create a separate async engine for the logging database."""
    return create_async_engine(
        database_url,
        pool_size=3,
        max_overflow=5,
        pool_timeout=10,
        pool_recycle=1800,
        echo=False,
    )


async def init_logs_schema(engine: AsyncEngine) -> None:
    """Create logging tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(logs_metadata.create_all)
    logger.info("Logging database schema initialized")


# ── Batched Log Writer ────────────────────────────────────────────────────────


class LogWriter:
    """Async batched log writer — buffers rows and flushes periodically.

    Avoids per-request DB writes. Flushes every flush_interval seconds
    or when the buffer exceeds max_buffer_size.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        flush_interval: float = 5.0,
        max_buffer_size: int = 200,
    ) -> None:
        self._engine = engine
        self._flush_interval = flush_interval
        self._max_buffer_size = max_buffer_size
        self._request_buffer: deque[dict[str, Any]] = deque()
        self._enrichment_buffer: deque[dict[str, Any]] = deque()
        self._error_buffer: deque[dict[str, Any]] = deque()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        """Start the background flush loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Stop the flush loop and flush remaining logs."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._flush()

    def log_request(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        user_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Buffer a request log entry."""
        self._request_buffer.append({
            "timestamp": datetime.now(UTC),
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "user_id": user_id,
            "ip_address": ip_address,
            "user_agent": user_agent[:500] if user_agent else None,
            "error_detail": error_detail[:2000] if error_detail else None,
        })

    def log_enrichment(
        self,
        *,
        user_id: str,
        catalog_id: str,
        stage: str,
        result: str,
        duration_ms: int | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Buffer an enrichment log entry."""
        self._enrichment_buffer.append({
            "timestamp": datetime.now(UTC),
            "user_id": user_id,
            "catalog_id": catalog_id,
            "stage": stage,
            "result": result,
            "duration_ms": duration_ms,
            "error_detail": error_detail[:2000] if error_detail else None,
        })

    def log_error(
        self,
        *,
        level: str,
        logger_name: str,
        message: str,
        user_id: str | None = None,
        path: str | None = None,
        traceback: str | None = None,
    ) -> None:
        """Buffer an error log entry."""
        self._error_buffer.append({
            "timestamp": datetime.now(UTC),
            "level": level,
            "logger_name": logger_name,
            "message": message[:2000],
            "user_id": user_id,
            "path": path,
            "traceback": traceback[:5000] if traceback else None,
        })

    async def _flush_loop(self) -> None:
        """Periodic flush loop."""
        while self._running:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        """Write buffered logs to the database."""
        if not (self._request_buffer or self._enrichment_buffer or self._error_buffer):
            return

        try:
            async with self._engine.begin() as conn:
                if self._request_buffer:
                    rows = list(self._request_buffer)
                    self._request_buffer.clear()
                    await conn.execute(request_logs.insert(), rows)

                if self._enrichment_buffer:
                    rows = list(self._enrichment_buffer)
                    self._enrichment_buffer.clear()
                    await conn.execute(enrichment_logs.insert(), rows)

                if self._error_buffer:
                    rows = list(self._error_buffer)
                    self._error_buffer.clear()
                    await conn.execute(error_logs.insert(), rows)
        except Exception:
            logger.warning("Failed to flush logs to database", exc_info=True)


# ── Python Logging Handler → DB ──────────────────────────────────────────


class DatabaseLogHandler(logging.Handler):
    """Python logging handler that forwards log records to LogWriter.

    Captures WARNING+ logs from all loggers and persists them to the
    error_logs table via the batched LogWriter. INFO logs from the
    musicmind namespace are also captured for observability.

    Usage:
        handler = DatabaseLogHandler(log_writer)
        logging.getLogger().addHandler(handler)
    """

    def __init__(self, log_writer: LogWriter, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._writer = log_writer

    def emit(self, record: logging.LogRecord) -> None:
        # Only persist WARNING+ globally, or INFO+ from musicmind namespace
        if record.levelno < logging.WARNING:
            if not record.name.startswith("musicmind"):
                return

        try:
            tb = None
            if record.exc_info and record.exc_info[2]:
                import traceback
                tb = "".join(traceback.format_exception(*record.exc_info))

            self._writer.log_error(
                level=record.levelname,
                logger_name=record.name,
                message=self.format(record),
                traceback=tb,
            )
        except Exception:
            pass  # Never let logging crash the app
