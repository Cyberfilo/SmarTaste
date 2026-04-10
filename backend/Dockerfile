FROM python:3.13-slim

WORKDIR /app

# System deps: ffmpeg for Essentia's MonoLoader M4A/AAC support
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY alembic.ini ./
COPY alembic/ alembic/

# Install dependencies (including audio extras for Essentia + ONNX)
RUN uv sync --frozen --no-dev --extra audio || uv sync --frozen --no-dev

# Download Essentia EffNet ONNX model (~18MB) if not mounted externally
RUN mkdir -p /models && \
    python3 -c "import urllib.request; urllib.request.urlretrieve(\
    'https://essentia.upf.edu/models/feature-extractors/discogs-effnet/\
discogs-effnet-bsdynamic-1.onnx', '/models/discogs-effnet-bsdynamic-1.onnx')" \
    2>/dev/null || echo "Model download skipped (will use ReccoBeats fallback)"

# Non-root user for security
RUN useradd --create-home appuser
USER appuser

# Railway provides PORT env var; default to 8000 for local docker
ENV PORT=8000
EXPOSE ${PORT}

# Auto-generate secrets if not already set (first deploy convenience)
# These persist across restarts via Railway's env vars — set them there for production.
CMD ["sh", "-c", "\
  if [ -z \"$MUSICMIND_FERNET_KEY\" ]; then \
    export MUSICMIND_FERNET_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'); \
    echo \"AUTO-GENERATED MUSICMIND_FERNET_KEY (set in Railway env vars to persist)\"; \
  fi && \
  if [ -z \"$MUSICMIND_JWT_SECRET_KEY\" ]; then \
    export MUSICMIND_JWT_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))'); \
    echo \"AUTO-GENERATED MUSICMIND_JWT_SECRET_KEY (set in Railway env vars to persist)\"; \
  fi && \
  uv run alembic upgrade head || echo 'WARNING: migrations failed, starting anyway'; \
  uv run uvicorn musicmind.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
