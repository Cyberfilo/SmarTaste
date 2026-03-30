"""SmarTaste backend (musicmind package)."""

from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    """Read version from the VERSION file.

    Searches: repo root (local dev) → /app (Docker) → fallback.
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "VERSION",  # repo root: musicmind/VERSION
        here.parents[2] / "VERSION",  # Docker /app/VERSION
    ]
    for path in candidates:
        if path.exists():
            return path.read_text().strip()
    return "0.1.0"


__version__ = _read_version()
