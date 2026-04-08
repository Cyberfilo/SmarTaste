FROM python:3.14-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy backend source (build context = repo root)
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src/ src/
COPY backend/alembic.ini ./
COPY backend/alembic/ alembic/

# Install dependencies
RUN uv sync --frozen --no-dev

CMD ["uv", "run", "python", "-m", "musicmind.worker"]
