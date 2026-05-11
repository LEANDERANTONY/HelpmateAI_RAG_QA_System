FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# LibreOffice (core + writer) provides the headless DOCX → PDF converter
# used at ingest to produce the viewable PDF rendition for the in-app
# document viewer. Without it, DOCX uploads still succeed but the viewer
# falls back to a download-only affordance. The full LibreOffice install
# is ~400MB; we install only the writer module to keep the image lean.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    fonts-dejavu-core \
    libreoffice-core \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

COPY . .

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
