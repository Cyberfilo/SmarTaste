"""Rate limiting configuration using slowapi."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

# Per-endpoint rate limits
AUTH_LIMIT = "10/minute"
CHAT_LIMIT = "20/minute"
RECOMMENDATIONS_LIMIT = "30/minute"
TASTE_LIMIT = "20/minute"
CALIBRATION_LIMIT = "10/minute"
SERVICES_LIMIT = "15/minute"
PLAYLISTS_LIMIT = "30/minute"
STATS_LIMIT = "30/minute"
