"""MusicMind Web backend."""

from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    """Read version from the root VERSION file."""
    version_file = Path(__file__).resolve().parents[3] / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return "0.0.0"


__version__ = _read_version()
