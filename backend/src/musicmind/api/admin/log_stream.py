"""In-memory log buffer that broadcasts to admin SSE clients.

Captures Python logging messages and stores the last N in a ring buffer.
Admin SSE endpoint reads from this buffer and streams new entries.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Ring buffer of recent log entries (last 500)
_log_buffer: deque[dict[str, Any]] = deque(maxlen=500)

# Counter for dropped log entries (slow SSE consumers)
_dropped_logs: int = 0

# Connected admin SSE clients get notified of new logs
_subscribers: list[asyncio.Queue[dict[str, Any]]] = []


class AdminLogHandler(logging.Handler):
    """Custom logging handler that captures messages for admin streaming."""

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        _log_buffer.append(entry)

        # Notify all connected subscribers (non-blocking)
        for queue in _subscribers:
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                global _dropped_logs
                _dropped_logs += 1
                if _dropped_logs % 100 == 0:
                    # Can't use logger here (recursive), write to stderr directly
                    import sys
                    print(
                        f"WARNING: {_dropped_logs} admin log entries dropped (slow consumer)",
                        file=sys.stderr,
                    )


def install_admin_handler() -> None:
    """Install the admin log handler on both musicmind and root loggers."""
    handler = AdminLogHandler()
    handler.setLevel(logging.INFO)
    # Attach to musicmind namespace (catches all musicmind.* loggers)
    logging.getLogger("musicmind").addHandler(handler)
    # Also attach to root logger to catch uvicorn, sqlalchemy, etc.
    logging.getLogger().addHandler(handler)


def get_recent_logs(limit: int = 100) -> list[dict[str, Any]]:
    """Get the most recent log entries from the buffer."""
    entries = list(_log_buffer)
    return entries[-limit:]


def subscribe() -> asyncio.Queue[dict[str, Any]]:
    """Create a new subscriber queue for SSE streaming."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    _subscribers.append(queue)
    return queue


def unsubscribe(queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Remove a subscriber queue."""
    try:
        _subscribers.remove(queue)
    except ValueError:
        pass
