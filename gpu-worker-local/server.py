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
    # Try MPS first (Apple Silicon GPU), fall back to CPU on operator errors.
    # MERT's Conv1D + layer_norm variants usually work on MPS in recent PyTorch.
    mert_device = device
    if device == "mps":
        try:
            mert = mert.to("mps")
            # Sanity check: run a tiny forward pass
            with torch.no_grad():
                dummy = torch.randn(1, 24000, device="mps")
                inputs = _models["mert_processor"](
                    dummy.cpu().numpy(), sampling_rate=24000, return_tensors="pt",
                )
                inputs = {k: v.to("mps") for k, v in inputs.items()}
                _ = mert(**inputs)
            logger.info("MERT running on MPS (Apple Silicon GPU)")
        except Exception as e:
            logger.warning("MPS failed for MERT (%s), falling back to CPU", e)
            mert = mert.to("cpu")
            mert_device = "cpu"
    else:
        mert = mert.to(device)
    # float16 on MPS halves RAM and speeds up matmul ~1.5x. Safe for
    # audio embedding models; fall back to float32 on any issue.
    mert_dtype = torch.float32
    if mert_device == "mps":
        try:
            mert_half = mert.half()
            with torch.no_grad():
                dummy = torch.randn(1, 24000).numpy()
                inputs = _models["mert_processor"](
                    dummy, sampling_rate=24000, return_tensors="pt",
                )
                inputs = {k: v.to("mps") for k, v in inputs.items()}
                _ = mert_half(**inputs)
            mert = mert_half
            mert_dtype = torch.float16
            logger.info("MERT running in float16 on MPS")
        except Exception as e:
            logger.warning("float16 failed (%s), staying on float32", e)

    _models["mert"] = mert
    _models["device"] = device
    _models["mert_device"] = mert_device
    _models["mert_dtype"] = mert_dtype

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
            mert_device = _models["mert_device"]
            mert_dtype = _models.get("mert_dtype", torch.float32)
            inputs = {
                k: v.to(mert_device).to(mert_dtype)
                if v.dtype.is_floating_point else v.to(mert_device)
                for k, v in inputs.items()
            }

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
    """Mirrors the Modal /enrich endpoint contract.

    Optional `models` field in the request body restricts which embeddings
    are computed. Default = ["clap", "mert"]. Pass ["mert"] to skip the
    CLAP forward pass entirely (useful when backfilling MERT for rows
    that already have CLAP). Skipped models return None in their slot.
    """
    # Normalize + validate the models selector — used by the bytes branch.
    models_wanted = {
        m.lower() for m in (data.get("models") or ["clap", "mert"])
    }
    want_clap = "clap" in models_wanted
    want_mert = "mert" in models_wanted

    # Batch bytes (preferred — no URL download on the server)
    # Optimized: single CLAP forward pass for all N files, then loop MERT.
    if "audio_items" in data:
        tmp_paths: list = []
        decode_errors: dict = {}  # idx -> error dict

        # Decode + write tempfiles
        for i, b64 in enumerate(data["audio_items"]):
            try:
                audio = base64.b64decode(b64)
                if len(audio) < 1000:
                    decode_errors[i] = {
                        "error": "Audio too small",
                        "clap_512": None, "mert_768": None,
                    }
                    tmp_paths.append(None)
                    continue
                tmp = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False)
                tmp.write(audio)
                tmp.close()
                tmp_paths.append(Path(tmp.name))
            except Exception as e:
                decode_errors[i] = {
                    "error": f"Decode failed: {e}",
                    "clap_512": None, "mert_768": None,
                }
                tmp_paths.append(None)

        # Batch CLAP: all valid files in one forward pass — skipped if the
        # caller only wants MERT (saves the CLAP forward pass entirely).
        valid_paths = [p for p in tmp_paths if p is not None]
        clap_embeddings: dict = {}  # path_idx -> embedding
        if want_clap and valid_paths:
            try:
                embs = _models["clap"].get_audio_embedding_from_filelist(
                    [str(p) for p in valid_paths], use_tensor=False,
                )
                for orig_idx, emb in zip(
                    [i for i, p in enumerate(tmp_paths) if p is not None],
                    embs,
                ):
                    clap_embeddings[orig_idx] = [
                        round(float(v), 6) for v in emb
                    ]
            except Exception as e:
                logger.warning("Batch CLAP failed: %s", e)

        # Loop MERT per file + assemble results
        results = []
        for i, tmp_path in enumerate(tmp_paths):
            if i in decode_errors:
                results.append(decode_errors[i])
                continue
            result = {
                "error": None,
                "clap_512": clap_embeddings.get(i) if want_clap else None,
                "mert_768": None,
            }
            if want_clap and result["clap_512"] is None:
                result["error"] = "CLAP failed in batch"
            # MERT per-file — skipped entirely when the caller doesn't want it
            if want_mert:
                try:
                    import librosa
                    audio, sr = librosa.load(str(tmp_path), sr=None, mono=True)
                    if sr != 24000:
                        import torchaudio
                        at = torch.tensor(
                            audio, dtype=torch.float32,
                        ).unsqueeze(0)
                        resampler = torchaudio.transforms.Resample(sr, 24000)
                        at = resampler(at)
                        audio = at.squeeze(0).numpy()
                    inputs = _models["mert_processor"](
                        audio, sampling_rate=24000, return_tensors="pt",
                    )
                    mdev = _models["mert_device"]
                    mdtype = _models.get("mert_dtype", torch.float32)
                    inputs = {
                        k: v.to(mdev).to(mdtype)
                        if v.dtype.is_floating_point else v.to(mdev)
                        for k, v in inputs.items()
                    }
                    with torch.no_grad():
                        outputs = _models["mert"](
                            **inputs, output_hidden_states=True,
                        )
                    lh = outputs.hidden_states[-1]
                    emb = lh.mean(dim=1).squeeze(0).cpu().float().numpy()
                    result["mert_768"] = [round(float(v), 6) for v in emb]
                except Exception as e:
                    if not result["error"]:
                        result["error"] = f"MERT failed: {e}"
            results.append(result)

        # Cleanup tempfiles
        for p in tmp_paths:
            if p is not None:
                p.unlink(missing_ok=True)

        logger.info(
            "Batch-bytes: %d items processed (models=%s, clap-batched=%d)",
            len(results), sorted(models_wanted), len(valid_paths) if want_clap else 0,
        )
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
