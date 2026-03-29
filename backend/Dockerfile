FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY alembic.ini ./
COPY alembic/ alembic/

# Install dependencies
RUN uv sync --frozen --no-dev

# Expose port
EXPOSE 8000

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
