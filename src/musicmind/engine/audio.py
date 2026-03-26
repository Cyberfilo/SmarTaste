"""Audio feature extraction from Apple Music preview URLs via librosa.

Extracts 7 audio dimensions from 30-second preview clips:
tempo, energy, brightness, danceability, acousticness, valence_proxy, beat_strength.

Gracefully degrades: returns None when librosa/ffmpeg unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))

try:
    import librosa

    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False
    logger.info("librosa not installed — audio analysis disabled (Tier 1 only)")


@dataclass
class AudioFeatures:
    """7-dimension audio feature vector extracted from a preview clip."""

    tempo: float  # BPM (not clamped to 0-1)
    energy: float  # 0-1, from RMS
    brightness: float  # 0-1, from spectral centroid
    danceability: float  # 0-1, onset strength + tempo proximity to 112 BPM
    acousticness: float  # 0-1, inverse spectral flatness
    valence_proxy: float  # 0-1, chroma major/minor + spectral contrast
    beat_strength: float  # 0-1, onset strength

    def __post_init__(self) -> None:
        """Clamp 0-1 fields."""
        self.energy = max(0.0, min(1.0, self.energy))
        self.brightness = max(0.0, min(1.0, self.brightness))
        self.danceability = max(0.0, min(1.0, self.danceability))
        self.acousticness = max(0.0, min(1.0, self.acousticness))
        self.valence_proxy = max(0.0, min(1.0, self.valence_proxy))
        self.beat_strength = max(0.0, min(1.0, self.beat_strength))

    def to_dict(self) -> dict[str, float]:
        return {
            "tempo": self.tempo,
            "energy": self.energy,
            "brightness": self.brightness,
            "danceability": self.danceability,
            "acousticness": self.acousticness,
            "valence_proxy": self.valence_proxy,
            "beat_strength": self.beat_strength,
        }

    def to_vector(self) -> list[float]:
        """Return normalized 7-dim vector (tempo normalized to 0-1 range 40-220 BPM)."""
        tempo_norm = max(0.0, min(1.0, (self.tempo - 40.0) / 180.0))
        return [
            tempo_norm,
            self.energy,
            self.brightness,
            self.danceability,
            self.acousticness,
            self.valence_proxy,
            self.beat_strength,
        ]


class AudioAnalyzer:
    """Extracts audio features from audio arrays or preview URLs."""

    @staticmethod
    def is_available() -> bool:
        """Check if audio analysis is available (librosa installed)."""
        return LIBROSA_AVAILABLE

    @staticmethod
    def analyze_from_array(
        audio: np.ndarray, sr: int = 22050
    ) -> AudioFeatures | None:
        """Extract features from a raw audio array.

        Returns None if audio is too short (< 1 second).
        """
        if not LIBROSA_AVAILABLE:
            return None

        if len(audio) < sr:  # less than 1 second
            return None

        # Tempo
        tempo_arr, _ = librosa.beat.beat_track(y=audio, sr=sr)
        tempo = float(tempo_arr) if np.isscalar(tempo_arr) else float(tempo_arr[0])

        # Energy from RMS
        rms = librosa.feature.rms(y=audio)[0]
        energy = float(np.mean(rms)) / 0.3  # normalize assuming max RMS ~0.3

        # Brightness from spectral centroid
        centroid = librosa.feature.spectral_centroid(y=audio, sr=sr)[0]
        brightness = float(np.mean(centroid)) / (sr / 2)  # normalize by Nyquist

        # Danceability: onset strength + tempo proximity to 112 BPM
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
        onset_mean = float(np.mean(onset_env)) / 20.0  # normalize
        tempo_factor = 1.0 - abs(tempo - 112.0) / 112.0
        danceability = 0.6 * onset_mean + 0.4 * max(0.0, tempo_factor)

        # Acousticness: inverse spectral flatness
        flatness = librosa.feature.spectral_flatness(y=audio)[0]
        acousticness = 1.0 - float(np.mean(flatness))

        # Valence proxy: major/minor from chroma + spectral contrast
        chroma = librosa.feature.chroma_stft(y=audio, sr=sr)
        # Simple major key indicator: sum of major triad chroma bins
        major = float(np.mean(chroma[0]) + np.mean(chroma[4]) + np.mean(chroma[7]))
        minor = float(np.mean(chroma[0]) + np.mean(chroma[3]) + np.mean(chroma[7]))
        mode_score = major / (major + minor + 1e-8)
        contrast = librosa.feature.spectral_contrast(y=audio, sr=sr)
        contrast_mean = float(np.mean(contrast)) / 50.0
        valence_proxy = 0.6 * mode_score + 0.4 * contrast_mean

        # Beat strength from onset envelope
        beat_strength = float(np.mean(onset_env)) / 15.0

        return AudioFeatures(
            tempo=tempo,
            energy=energy,
            brightness=brightness,
            danceability=danceability,
            acousticness=acousticness,
            valence_proxy=valence_proxy,
            beat_strength=beat_strength,
        )

    @staticmethod
    async def analyze_from_url(
        preview_url: str, client: Any = None
    ) -> AudioFeatures | None:
        """Download a preview and extract audio features.

        Args:
            preview_url: URL to an audio preview (usually M4A/AAC)
            client: Optional httpx.AsyncClient for downloading
        """
        if not LIBROSA_AVAILABLE or not preview_url:
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

            # Load audio with librosa
            audio, sr = librosa.load(
                BytesIO(audio_bytes), sr=22050, mono=True, res_type="kaiser_fast"
            )
            return AudioAnalyzer.analyze_from_array(audio, sr)
        except Exception as e:
            logger.warning("Audio analysis failed for %s: %s", preview_url, e)
            return None

    @staticmethod
    async def analyze_batch(
        preview_urls: dict[str, str], max_concurrent: int = 5
    ) -> dict[str, AudioFeatures]:
        """Analyze multiple previews in parallel.

        Args:
            preview_urls: {catalog_id: preview_url}
            max_concurrent: Max concurrent downloads

        Returns:
            {catalog_id: AudioFeatures} for successfully analyzed songs
        """
        if not LIBROSA_AVAILABLE:
            return {}

        import httpx

        results: dict[str, AudioFeatures] = {}
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _analyze_one(
            cid: str, url: str, client: httpx.AsyncClient
        ) -> None:
            async with semaphore:
                features = await AudioAnalyzer.analyze_from_url(url, client)
                if features:
                    results[cid] = features

        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [
                _analyze_one(cid, url, client)
                for cid, url in preview_urls.items()
                if url
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        return results
