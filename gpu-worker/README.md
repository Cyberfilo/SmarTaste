# SmarTaste GPU Worker

Serverless GPU enrichment on Modal (A100, scale-to-zero).

## Setup
1. `pip install modal`
2. `modal token new` (creates Modal account + auth)
3. `cd gpu-worker && modal deploy handler.py`
4. Copy the endpoint URL from Modal dashboard
5. Set on Railway backend + worker:
   - `MUSICMIND_MODAL_ENDPOINT_URL=https://your-modal-endpoint.modal.run`

## Cost
- ~$0.018/track on A100 40GB
- $30/month free credits (covers ~1,200 tracks)
- Scale to zero when idle = $0

## Models
- LAION CLAP (512-dim) — text+audio shared space for semantic search
- MERT-v1-95M (768-dim) — music-native embeddings for structural similarity

## Test
```bash
modal run handler.py  # Runs locally with GPU
```
