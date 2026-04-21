"""Request logging middleware — logs every request with timing and user context.

Writes to both stderr (for Railway logs) and a separate PostgreSQL logging DB
via a batched async writer (flushes every 5s, no per-request DB writes).
"""

from __future__ import annotations

import logging
import time

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("musicmind.requests")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, duration, and user_id."""

    SKIP_PATHS = {"/api/health", "/health", "/favicon.ico", "/_next"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        if any(path.startswith(skip) for skip in self.SKIP_PATHS):
            return await call_next(request)

        start = time.monotonic()
        user_id = self._extract_user_id(request)
        method = request.method
        try:
            ip = request.client.host if request.client else None
        except Exception:
            ip = None
        ua = request.headers.get("user-agent")

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            logger.error(
                "%s %s -> 500 (%dms) user=%s [unhandled exception]",
                method, path, duration_ms, user_id or "-",
            )
            self._write_to_db(
                request, method=method, path=path, status_code=500,
                duration_ms=duration_ms, user_id=user_id, ip=ip, ua=ua,
                error="unhandled exception",
            )
            raise

        duration_ms = round((time.monotonic() - start) * 1000)
        status_code = response.status_code

        if status_code >= 500:
            logger.error(
                "%s %s -> %d (%dms) user=%s",
                method, path, status_code, duration_ms, user_id or "-",
            )
        elif status_code >= 400:
            logger.warning(
                "%s %s -> %d (%dms) user=%s",
                method, path, status_code, duration_ms, user_id or "-",
            )
        elif duration_ms > 5000:
            logger.warning(
                "%s %s -> %d (%dms) user=%s [SLOW]",
                method, path, status_code, duration_ms, user_id or "-",
            )
        else:
            logger.info(
                "%s %s -> %d (%dms) user=%s",
                method, path, status_code, duration_ms, user_id or "-",
            )

        # V 6.392: skip 200 OK writes — the boring success path is pure
        # noise in the DB. Everything else (201/204/3xx/4xx/5xx) gets
        # persisted, and the 500 path above already writes unconditionally.
        if status_code != 200:
            self._write_to_db(
                request, method=method, path=path, status_code=status_code,
                duration_ms=duration_ms, user_id=user_id, ip=ip, ua=ua,
            )

        return response

    @staticmethod
    def _extract_user_id(request: Request) -> str | None:
        """Extract user_id from JWT cookie with full signature verification."""
        token = request.cookies.get("access_token")
        if not token:
            return None
        try:
            settings = request.app.state.settings
            payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
            return payload.get("sub")
        except Exception:
            return None

    @staticmethod
    def _write_to_db(
        request: Request,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        user_id: str | None,
        ip: str | None,
        ua: str | None,
        error: str | None = None,
    ) -> None:
        """Write log entry to the logging DB via the batched writer."""
        log_writer = getattr(request.app.state, "log_writer", None)
        if log_writer is None:
            return
        log_writer.log_request(
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration_ms,
            user_id=user_id,
            ip_address=ip,
            user_agent=ua,
            error_detail=error,
        )
