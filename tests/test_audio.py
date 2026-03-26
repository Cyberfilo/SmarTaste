"""Tests for audio feature extraction."""

from __future__ import annotations

import numpy as np
import pytest

from musicmind.engine.audio import AudioAnalyzer, AudioFeatures


class TestAudioFeatures:
    def test_dataclass_fields(self) -> None:
        f = AudioFeatures(
            tempo=128.0,
            energy=0.8,
            brightness=0.6,
            danceability=0.7,
            acousticness=0.3,
            valence_proxy=0.5,
            beat_strength=0.65,
        )
        assert f.tempo == 128.0
        assert f.energy == 0.8

    def test_to_vector_length(self) -> None:
        f = AudioFeatures(120.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        vec = f.to_vector()
        assert len(vec) == 7
        assert all(isinstance(v, float) for v in vec)

    def test_to_vector_tempo_normalized(self) -> None:
        f = AudioFeatures(130.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        vec = f.to_vector()
        # tempo 130 → (130-40)/180 = 0.5
        assert 0.0 <= vec[0] <= 1.0

    def test_clamping(self) -> None:
        f = AudioFeatures(200.0, 1.5, -0.1, 2.0, -1.0, 1.1, 0.5)
        assert f.energy == 1.0
        assert f.brightness == 0.0
        assert f.danceability == 1.0
        assert f.acousticness == 0.0
        assert f.valence_proxy == 1.0

    def test_to_dict(self) -> None:
        f = AudioFeatures(120.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        d = f.to_dict()
        assert "tempo" in d
        assert "energy" in d
        assert len(d) == 7


class TestAudioAnalyzer:
    def test_is_available(self) -> None:
        # Should return bool regardless
        result = AudioAnalyzer.is_available()
        assert isinstance(result, bool)

    @pytest.mark.skipif(
        not AudioAnalyzer.is_available(),
        reason="librosa not installed",
    )
    def test_analyze_sine_wave(self) -> None:
        """Synthetic 440Hz sine wave should produce valid features."""
        sr = 22050
        duration = 5.0
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        audio = 0.3 * np.sin(2 * np.pi * 440 * t).astype(np.float32)

        features = AudioAnalyzer.analyze_from_array(audio, sr)
        assert features is not None
        assert features.tempo >= 0  # sine wave may not have detectable beats
        assert 0.0 <= features.energy <= 1.0
        assert 0.0 <= features.brightness <= 1.0

    @pytest.mark.skipif(
        not AudioAnalyzer.is_available(),
        reason="librosa not installed",
    )
    def test_silent_audio_low_energy(self) -> None:
        """Silent audio should have low energy."""
        sr = 22050
        audio = np.zeros(sr * 3, dtype=np.float32)  # 3 seconds of silence
        features = AudioAnalyzer.analyze_from_array(audio, sr)
        assert features is not None
        assert features.energy < 0.1

    def test_too_short_returns_none(self) -> None:
        """Audio shorter than 1 second should return None."""
        if not AudioAnalyzer.is_available():
            return
        audio = np.zeros(100, dtype=np.float32)
        assert AudioAnalyzer.analyze_from_array(audio, 22050) is None
