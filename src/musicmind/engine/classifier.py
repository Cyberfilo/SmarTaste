"""Optional SoundAnalysis integration via Swift CLI helper (macOS only).

Calls a compiled Swift binary that uses Apple's SoundAnalysis framework
to classify audio. Returns classification labels with confidence scores.

This is Tier 3 — the system works without it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))

# Expected location of the Swift binary
_SWIFT_BINARY_PATHS = [
    Path(__file__).parent.parent.parent.parent / "swift" / "MusicMindAnalyzer"
    / ".build" / "release" / "MusicMindAnalyzer",
]


class SoundClassifier:
    """Interface to the SoundAnalysis Swift CLI tool."""

    @staticmethod
    def is_available() -> bool:
        """Check if the Swift binary exists."""
        for p in _SWIFT_BINARY_PATHS:
            if p.exists() and p.is_file():
                return True
        return False

    @staticmethod
    def _get_binary_path() -> Path | None:
        for p in _SWIFT_BINARY_PATHS:
            if p.exists():
                return p
        return None

    @staticmethod
    async def classify(audio_path: str) -> dict[str, float] | None:
        """Classify an audio file using SoundAnalysis.

        Returns a dict of {label: confidence} or None if unavailable.
        """
        binary = SoundClassifier._get_binary_path()
        if not binary:
            return None

        try:
            proc = await asyncio.create_subprocess_exec(
                str(binary),
                audio_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

            if proc.returncode != 0:
                logger.warning(
                    "SoundAnalysis failed: %s", stderr.decode().strip()
                )
                return None

            result = json.loads(stdout.decode())
            if "error" in result:
                logger.warning("SoundAnalysis error: %s", result["error"])
                return None

            return result.get("labels", {})
        except (TimeoutError, json.JSONDecodeError, OSError) as e:
            logger.warning("SoundClassifier error: %s", e)
            return None

    @staticmethod
    async def classify_from_url(
        preview_url: str, client: Any = None
    ) -> dict[str, float] | None:
        """Download a preview to a temp file and classify it."""
        if not SoundClassifier.is_available():
            return None

        try:
            import httpx

            if client is None:
                async with httpx.AsyncClient(timeout=30.0) as c:
                    resp = await c.get(preview_url)
                    resp.raise_for_status()
                    audio_bytes = resp.content
            else:
                resp = await client.get(preview_url)
                resp.raise_for_status()
                audio_bytes = resp.content

            # Write to temp file
            with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as f:
                f.write(audio_bytes)
                temp_path = f.name

            labels = await SoundClassifier.classify(temp_path)

            # Clean up
            Path(temp_path).unlink(missing_ok=True)
            return labels
        except Exception as e:
            logger.warning("classify_from_url failed: %s", e)
            return None
