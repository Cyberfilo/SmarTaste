"""Local CLAP + MERT enrichment server (mirrors Modal handler.py).

Runs on your Mac, exposes the same HTTP interface as the Modal endpoint.
Use Cloudflare Tunnel or ngrok to expose it to Railway — then set
MUSICMIND_GPU_MODE=LOCAL and MUSICMIND_LOCAL_GPU_ENDPOINT_URL=<tunnel URL>
on both the backend and worker Railway services.

Usage:
    cd gpu-worker-local
    uv venv
    source .venv/bin/activate
    uv pip install -r requirements.txt
    python server.py

Or one-shot:
    uv run --with-requirements requirements.txt python server.py

Accepted POST body (same as Modal endpoint):
    {"audio_items":  ["<base64>", ...]}  → returns {"results": [...]}
    {"preview_urls": ["<url>", ...]}     → returns {"results": [...]}
    {"preview_url":  "<url>"}             → returns enrichment dict
    {"text":         "<query>"}           → returns {"clap_512": [...]}
"""
from __future__ import annotations

import base64
import logging
import os
import tempfile
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import torch
import uvicorn
from fastapi import FastAPI

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("gpu-worker-local")

HOST = os.environ.get("GPU_WORKER_HOST", "0.0.0.0")
PORT = int(os.environ.get("GPU_WORKER_PORT", "8765"))

_models: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load CLAP + MERT on startup (keeps them in memory across requests)."""
    # Patch torch.load for CLAP — checkpoint contains numpy globals which
    # PyTorch 2.6+ blocks by default when weights_only=True.
    _orig_load = torch.load
    torch.load = lambda *a, **kw: _orig_load(
        *a, **{**kw, "weights_only": False},
    )

    # Pick best device: Apple Silicon MPS > CUDA > CPU
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    logger.info("Using device: %s", device)

    logger.info("Loading CLAP (HTSAT-tiny) ...")
    import laion_clap
    clap = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-tiny")
    clap.load_ckpt()  # ~600MB on first run
    _models["clap"] = clap

    logger.info("Loading MERT (v1-95M) ...")
    from transformers import AutoModel, Wav2Vec2FeatureExtractor
    _models["mert_processor"] = Wav2Vec2FeatureExtractor.from_pretrained(
        "m-a-p/MERT-v1-95M", trust_remote_code=True,
    )
    mert = AutoModel.from_pretrained(
        "m-a-p/MERT-v1-95M", trust_remote_code=True,
    )
    mert.eval()
    # MERT on CPU is slow but safe. MPS sometimes has operator gaps for this model.
    _models["mert"] = mert.to(device) if device != "mps" else mert.to("cpu")
    _models["device"] = device if device != "mps" else "cpu"

    logger.info("Models loaded. Ready on http://%s:%d", HOST, PORT)
    yield
    logger.info("Shutting down")


app = FastAPI(title="SmarTaste Local GPU Worker", lifespan=lifespan)


def _process_audio_bytes(audio_bytes: bytes) -> dict:
    """Core enrichment: audio bytes → CLAP (512) + MERT (768) embeddings."""
    if len(audio_bytes) < 1000:
        return {
            "error": "Audio too small",
            "clap_512": None,
            "mert_768": None,
        }

    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)

    try:
        result: dict = {"error": None}

        # CLAP embedding (512-dim)
        try:
            embedding = _models["clap"].get_audio_embedding_from_filelist(
                [str(tmp_path)], use_tensor=False,
            )
            result["clap_512"] = [
                round(float(v), 6) for v in embedding[0]
            ]
        except Exception as e:
            result["clap_512"] = None
            result["error"] = f"CLAP failed: {e}"

        # MERT embedding (768-dim)
        try:
            import librosa
            audio, sr = librosa.load(str(tmp_path), sr=None, mono=True)
            if sr != 24000:
                import torchaudio
                audio_tensor = torch.tensor(
                    audio, dtype=torch.float32,
                ).unsqueeze(0)
                resampler = torchaudio.transforms.Resample(sr, 24000)
                audio_tensor = resampler(audio_tensor)
                audio = audio_tensor.squeeze(0).numpy()

            inputs = _models["mert_processor"](
                audio, sampling_rate=24000, return_tensors="pt",
            )
            device = _models["device"]
            inputs = {k: v.to(device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = _models["mert"](
                    **inputs, output_hidden_states=True,
                )

            last_hidden = outputs.hidden_states[-1]
            embedding = (
                last_hidden.mean(dim=1).squeeze(0).cpu().numpy()
            )
            result["mert_768"] = [
                round(float(v), 6) for v in embedding
            ]
        except Exception as e:
            result["mert_768"] = None
            if not result["error"]:
                result["error"] = f"MERT failed: {e}"

        return result
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/health")
async def health() -> dict:
    """Simple health check."""
    return {
        "status": "ok",
        "device": _models.get("device", "?"),
        "clap_loaded": "clap" in _models,
        "mert_loaded": "mert" in _models,
    }


@app.post("/")
async def enrich(data: dict) -> dict:
    """Mirrors the Modal /enrich endpoint contract."""
    # Batch bytes (preferred — no URL download on the server)
    if "audio_items" in data:
        results = []
        for b64 in data["audio_items"]:
            try:
                audio = base64.b64decode(b64)
                results.append(_process_audio_bytes(audio))
            except Exception as e:
                results.append({
                    "error": f"Decode failed: {e}",
                    "clap_512": None,
                    "mert_768": None,
                })
        logger.info("Batch-bytes: %d items processed", len(results))
        return {"results": results}

    # Batch URLs
    if "preview_urls" in data:
        results = []
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True,
        ) as client:
            for url in data["preview_urls"]:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    results.append(_process_audio_bytes(resp.content))
                except Exception as e:
                    results.append({
                        "error": f"Download failed: {e}",
                        "clap_512": None,
                        "mert_768": None,
                    })
        logger.info("Batch-URLs: %d items processed", len(results))
        return {"results": results}

    # Single URL
    if "preview_url" in data:
        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True,
            ) as client:
                resp = await client.get(data["preview_url"])
                resp.raise_for_status()
                return _process_audio_bytes(resp.content)
        except Exception as e:
            return {
                "error": f"Download failed: {e}",
                "clap_512": None,
                "mert_768": None,
            }

    # CLAP text encoding (for semantic search)
    if "text" in data:
        try:
            emb = _models["clap"].get_text_embedding(
                [data["text"]], use_tensor=False,
            )
            return {"clap_512": [round(float(v), 6) for v in emb[0]]}
        except Exception as e:
            return {"error": f"Text encoding failed: {e}"}

    return {
        "error": "Provide one of: audio_items, preview_urls, preview_url, text",
    }


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
