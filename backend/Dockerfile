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

# Run migrations (tolerate failure so the server still starts) then start server
CMD ["sh", "-c", "uv run alembic upgrade head || echo 'WARNING: migrations failed, starting anyway'; uv run uvicorn musicmind.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
