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

# Railway provides PORT env var; default to 8000 for local docker
ENV PORT=8000
EXPOSE ${PORT}

# Run migrations then start server
# Railway sets PORT dynamically; Vercel/local uses 8000
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn musicmind.app:app --host 0.0.0.0 --port ${PORT}"]
