# Stage 1 — build the Next.js static export
FROM node:20-slim AS ui-builder

WORKDIR /ui

# Cache-friendly: install deps first
COPY admin/ui/package.json admin/ui/package-lock.json* ./
RUN npm install --no-audit --no-fund

# Copy the rest of the UI source and build
COPY admin/ui/ ./
RUN npm run build

# Stage 2 — Python runtime (FastAPI proxy + cookie gate)
FROM python:3.14-slim

WORKDIR /app

COPY admin/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Python app + Jinja login template
COPY admin/app.py ./
COPY admin/templates ./templates

# Static React bundle produced by stage 1 (out/ from next build)
COPY --from=ui-builder /ui/out ./static

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
