# Worker image — runs musicmind.worker from the backend package.
# Build context must be the repo root so COPY backend/... paths resolve.
# Dependencies come from backend/pyproject.toml (uv sync --extra audio),
# NOT from worker/requirements.txt (deleted — was dead code).
#
# Python 3.12 required — essentia only has wheels for 3.9-3.12
FROM python:3.12-slim

WORKDIR /app

# System deps: ffmpeg for Essentia's MonoLoader M4A/AAC support
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy backend source (build context = repo root)
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src/ src/
COPY backend/alembic.ini ./
COPY backend/alembic/ alembic/

# Install dependencies (Essentia + ONNX required for audio enrichment)
RUN uv sync --frozen --no-dev --extra audio

# Download Essentia EffNet ONNX model (~18MB)
RUN mkdir -p /models && \
    python3 -c "import urllib.request; urllib.request.urlretrieve(\
    'https://essentia.upf.edu/models/feature-extractors/discogs-effnet/\
discogs-effnet-bsdynamic-1.onnx', '/models/discogs-effnet-bsdynamic-1.onnx')" \
    2>/dev/null || echo "WARNING: Model download failed"

# Non-root user
RUN useradd --create-home appuser && \
    chown -R appuser:appuser /app /models
USER appuser

CMD ["uv", "run", "python", "-m", "musicmind.worker"]
