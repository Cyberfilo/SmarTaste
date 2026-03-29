"""Rate limiting configuration using slowapi.

Limits:
- Auth (signup/login): 5/minute per IP
- Recommendations: 30/minute per user
- Chat: 20/minute per user
- General API: 60/minute per IP
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _get_user_or_ip(request: Request) -> str:
    """Extract user_id from JWT cookie if available, fall back to IP."""
    # Try to get user_id from the already-decoded token (set by auth dependency)
    # For unauthenticated endpoints, fall back to IP
    try:
        import jwt
        settings = request.app.state.settings
        token = request.cookies.get("access_token")
        if token:
            payload = jwt.decode(
                token, settings.jwt_secret_key, algorithms=["HS256"],
                options={"verify_exp": False},
            )
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
    except Exception:
        pass
    return get_remote_address(request)


limiter = Limiter(key_func=_get_user_or_ip)

# Rate limit constants for use in route decorators
AUTH_LIMIT = "5/minute"
RECOMMENDATIONS_LIMIT = "30/minute"
CHAT_LIMIT = "20/minute"
GENERAL_LIMIT = "60/minute"
