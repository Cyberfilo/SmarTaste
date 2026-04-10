"""SmarTaste GPU Worker — Modal serverless function for CLAP + MERT enrichment.

Processes 30-second audio previews and returns:
- CLAP 512-dim embedding (text-audio shared space for semantic search)
- MERT 768-dim embedding (music-native for structural similarity)

Usage:
    modal deploy handler.py          # Deploy to Modal cloud
    modal run handler.py             # Test locally
"""
from __future__ import annotations

import modal

app = modal.App("smartaste-gpu-worker")

# Build image with all dependencies
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "laion-clap==1.1.6",
        "transformers>=4.40",
        "torch>=2.0",
        "torchaudio>=2.0",
        "numpy",
        "httpx",
        "soundfile",
    )
)


@app.cls(image=image, gpu="A100", timeout=300, min_containers=0)
class AudioEnricher:
    """GPU-accelerated audio enrichment with model caching."""

    @modal.enter()
    def load_models(self) -> None:
        """Load models once at container startup (cached across calls)."""
        import laion_clap
        import torch
        from transformers import AutoModel, Wav2Vec2FeatureExtractor

        # CLAP — music checkpoint
        self.clap_model = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-base")
        self.clap_model.load_ckpt()  # Downloads ~600MB on first run, cached after

        # MERT — music understanding transformer
        self.mert_processor = Wav2Vec2FeatureExtractor.from_pretrained(
            "m-a-p/MERT-v1-95M", trust_remote_code=True,
        )
        self.mert_model = AutoModel.from_pretrained(
            "m-a-p/MERT-v1-95M", trust_remote_code=True,
        )
        self.mert_model.eval()
        if torch.cuda.is_available():
            self.mert_model = self.mert_model.cuda()

    @modal.method()
    def enrich_track(self, preview_url: str) -> dict:
        """Full Tier 2 enrichment for a single track.

        Args:
            preview_url: URL to 30-second M4A/MP3 preview.

        Returns:
            Dict with clap_512 and mert_768 embedding lists.
        """
        import tempfile
        from pathlib import Path

        import httpx
        import numpy as np  # noqa: F401

        # Download preview
        try:
            resp = httpx.get(preview_url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            audio_bytes = resp.content
        except Exception as e:
            return {"error": f"Download failed: {e}", "clap_512": None, "mert_768": None}

        if len(audio_bytes) < 1000:
            return {"error": "Audio too small", "clap_512": None, "mert_768": None}

        # Write to tempfile
        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)

        try:
            result: dict = {"error": None}

            # CLAP embedding (512-dim)
            try:
                embedding = self.clap_model.get_audio_embedding_from_filelist(
                    [str(tmp_path)], use_tensor=False,
                )
                result["clap_512"] = [round(float(v), 6) for v in embedding[0]]
            except Exception as e:
                result["clap_512"] = None
                result["error"] = f"CLAP failed: {e}"

            # MERT embedding (768-dim)
            try:
                import soundfile as sf
                import torch

                audio, sr = sf.read(str(tmp_path))
                if len(audio.shape) > 1:
                    audio = audio.mean(axis=1)  # mono
                # Resample to 24kHz for MERT
                if sr != 24000:
                    import torchaudio
                    audio_tensor = torch.tensor(audio, dtype=torch.float32).unsqueeze(0)
                    resampler = torchaudio.transforms.Resample(sr, 24000)
                    audio_tensor = resampler(audio_tensor)
                    audio = audio_tensor.squeeze(0).numpy()

                inputs = self.mert_processor(
                    audio, sampling_rate=24000, return_tensors="pt",
                )
                if torch.cuda.is_available():
                    inputs = {k: v.cuda() for k, v in inputs.items()}

                with torch.no_grad():
                    outputs = self.mert_model(**inputs, output_hidden_states=True)

                # Average last hidden state across time
                last_hidden = outputs.hidden_states[-1]
                embedding = last_hidden.mean(dim=1).squeeze(0).cpu().numpy()
                result["mert_768"] = [round(float(v), 6) for v in embedding]
            except Exception as e:
                result["mert_768"] = None
                if not result["error"]:
                    result["error"] = f"MERT failed: {e}"

            return result

        finally:
            tmp_path.unlink(missing_ok=True)

    @modal.method()
    def encode_text_clap(self, text: str) -> list[float] | None:
        """Encode a text query to CLAP 512-dim embedding for semantic search."""
        try:
            embedding = self.clap_model.get_text_embedding([text], use_tensor=False)
            return [round(float(v), 6) for v in embedding[0]]
        except Exception:
            return None

    @modal.method()
    def enrich_batch(self, preview_urls: list[str]) -> list[dict]:
        """Batch enrichment for efficiency."""
        return [self.enrich_track(url) for url in preview_urls]


# ── HTTP endpoints for Railway ─────────────────────────────────────────────────

@app.function(image=image, timeout=30)
@modal.fastapi_endpoint(method="POST")
def enrich(data: dict) -> dict:
    """HTTP endpoint for Railway worker to call.

    Body: {"preview_url": "https://..."} or {"preview_urls": ["..."]}
    Returns: enrichment result(s)
    """
    enricher = AudioEnricher()

    if "preview_urls" in data:
        return {"results": enricher.enrich_batch.remote(data["preview_urls"])}
    elif "preview_url" in data:
        return enricher.enrich_track.remote(data["preview_url"])
    elif "text" in data:
        embedding = enricher.encode_text_clap.remote(data["text"])
        return {"clap_512": embedding}
    else:
        return {"error": "Provide preview_url, preview_urls, or text"}


@app.function(image=image, timeout=30)
@modal.fastapi_endpoint(method="POST")
def encode_text(data: dict) -> dict:
    """HTTP endpoint for text-to-CLAP encoding (for search)."""
    enricher = AudioEnricher()
    text = data.get("text", "")
    if not text:
        return {"error": "Provide text field"}
    embedding = enricher.encode_text_clap.remote(text)
    return {"clap_512": embedding}
