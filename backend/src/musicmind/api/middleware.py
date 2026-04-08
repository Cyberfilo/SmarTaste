"""Request logging middleware — logs every request with timing and user context."""

from __future__ import annotations

import logging
import time

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("musicmind.requests")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, duration, and user_id.

    Extracts user_id from the access_token cookie (best-effort, no auth check).
    Skips health check and static asset requests to reduce noise.
    """

    SKIP_PATHS = {"/api/health", "/favicon.ico", "/_next"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Skip noisy paths
        if any(path.startswith(skip) for skip in self.SKIP_PATHS):
            return await call_next(request)

        start = time.monotonic()
        user_id = self._extract_user_id(request)
        method = request.method

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            logger.error(
                "%s %s -> 500 (%dms) user=%s [unhandled exception]",
                method, path, duration_ms, user_id or "-",
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
            # Flag slow requests
            logger.warning(
                "%s %s -> %d (%dms) user=%s [SLOW]",
                method, path, status_code, duration_ms, user_id or "-",
            )
        else:
            logger.info(
                "%s %s -> %d (%dms) user=%s",
                method, path, status_code, duration_ms, user_id or "-",
            )

        return response

    @staticmethod
    def _extract_user_id(request: Request) -> str | None:
        """Best-effort user_id extraction from JWT cookie. No auth check."""
        token = request.cookies.get("access_token")
        if not token:
            return None
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
            return payload.get("sub")
        except Exception:
            return None
