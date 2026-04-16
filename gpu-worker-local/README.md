# Local GPU Worker (Mac)

A drop-in replacement for the Modal GPU worker that runs on your Mac.
Useful for a day of heavy enrichment without Modal costs, or for dev/debug.

The server listens on `http://localhost:8765` and speaks the exact same
HTTP interface as the Modal endpoint — so Railway services use it
transparently by flipping the `MUSICMIND_GPU_MODE` env var.

---

## 1 — Install and run the local server

> ⚠️ **Python 3.11 required.** `laion-clap==1.1.6` pins `numpy==1.23.5`,
> which can't build on Python 3.12+ because `distutils` was removed from
> the stdlib. Modal's image uses 3.11 for the same reason.

```bash
cd gpu-worker-local

# must explicitly pick Python 3.11 — uv defaults to your system Python
uv venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements.txt

# first run downloads ~2GB (CLAP + MERT checkpoints)
python server.py
```

If you don't have Python 3.11:

```bash
brew install python@3.11
```

First request may take 30–60s (cold model load). Subsequent requests:
5–10s per track on Apple Silicon (MPS), 15–30s on Intel Mac (CPU).

Verify it's alive:

```bash
curl http://localhost:8765/health
# → {"status":"ok","device":"mps","clap_loaded":true,"mert_loaded":true}
```

---

## 2 — Expose your Mac to Railway via a tunnel

Railway's cloud services can't reach `localhost:8765` on your Mac.
You need a public HTTPS URL pointing to your server.

### Option A — Cloudflare Tunnel (recommended, free, no account)

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8765
```

Copy the printed URL:
`https://<something>.trycloudflare.com`

### Option B — ngrok

```bash
brew install ngrok
# one-time: sign up at ngrok.com, grab your authtoken
ngrok config add-authtoken <YOUR_TOKEN>
ngrok http 8765
```

Copy the Forwarding URL: `https://<something>.ngrok-free.app`

---

## 3 — Switch Railway to use the local endpoint

On **both** the backend and worker Railway services, set these
environment variables:

```
MUSICMIND_GPU_MODE=LOCAL
MUSICMIND_LOCAL_GPU_ENDPOINT_URL=https://<your-tunnel-url>
```

Then redeploy (Railway auto-redeploys on env change, or trigger manually).

The backend's `Settings` class detects `gpu_mode=LOCAL` and swaps
`modal_endpoint_url` with `local_gpu_endpoint_url` during startup —
every existing caller (`gpu_client.py`, `orchestrator.py`, etc.) uses
the local endpoint automatically.

---

## 4 — Switch back to Modal

Either delete the `MUSICMIND_GPU_MODE` env var, or:

```
MUSICMIND_GPU_MODE=CLOUD
```

Then stop your local `python server.py` and `cloudflared` process.

---

## Local development (backend on your Mac, worker on Railway)

If you also run the backend locally for testing, add to your
`backend/.env`:

```
MUSICMIND_GPU_MODE=LOCAL
MUSICMIND_LOCAL_GPU_ENDPOINT_URL=http://localhost:8765
```

No tunnel needed — localhost-to-localhost works directly.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `MERT failed: Format not recognised` | Already handled — server uses `librosa.load()` which falls back to audioread for M4A/AAC |
| `No module named laion_clap` | `uv pip install -r requirements.txt` |
| First request hangs | Models still loading — check `/health` endpoint |
| `ImportError: nnAudio` | Harmless warning — MERT works without CQT features |
| Tunnel URL changes each restart | That's expected. Update the Railway env var each time you restart cloudflared, OR use a named tunnel (ngrok reserved domain / Cloudflare Tunnel with account) |
| Modal logs still show calls | Railway didn't pick up the env change — trigger a redeploy |

---

## How it works

**Config-side switch** (`backend/src/musicmind/config.py`):

```python
gpu_mode: str = "CLOUD"              # MUSICMIND_GPU_MODE
local_gpu_endpoint_url: str | None = None  # MUSICMIND_LOCAL_GPU_ENDPOINT_URL

def model_post_init(self, __context):
    ...
    if self.gpu_mode.upper() == "LOCAL" and self.local_gpu_endpoint_url:
        # Transparent swap — all existing code that reads
        # settings.modal_endpoint_url now gets the local URL.
        object.__setattr__(
            self, "modal_endpoint_url", self.local_gpu_endpoint_url,
        )
```

**Request flow** with `gpu_mode=LOCAL`:

```
Railway worker
    └─ enrich_batch_bytes_via_gpu(audio_items, settings.modal_endpoint_url)
        └─ POST https://xxx.trycloudflare.com/
            └─ Cloudflare edge → your Mac → FastAPI :8765
                └─ _process_audio_bytes → CLAP + MERT
                    └─ {"results": [{"clap_512": [...], "mert_768": [...]}, ...]}
```

The Modal endpoint shape (`/` accepts `audio_items`/`preview_urls`/
`preview_url`/`text`, returns `{results: [...]}` or a single dict) is
preserved exactly — no client-side changes needed.
