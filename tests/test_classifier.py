"""Tests for SoundAnalysis classifier integration."""

from __future__ import annotations

from musicmind.engine.classifier import SoundClassifier


class TestSoundClassifier:
    def test_is_available_returns_bool(self) -> None:
        result = SoundClassifier.is_available()
        assert isinstance(result, bool)

    async def test_classify_unavailable_returns_none(self) -> None:
        """When binary not available, classify returns None gracefully."""
        if SoundClassifier.is_available():
            return  # can't test graceful degradation when it IS available
        result = await SoundClassifier.classify("/nonexistent/file.m4a")
        assert result is None

    async def test_classify_from_url_unavailable_returns_none(self) -> None:
        if SoundClassifier.is_available():
            return
        result = await SoundClassifier.classify_from_url("https://example.com/preview.m4a")
        assert result is None
