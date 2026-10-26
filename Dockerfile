# syntax=docker/dockerfile:1
# Multi-stage: build the frontend, then a slim Python runtime that serves API + UI.

FROM node:22-bookworm-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_LINK_MODE=copy \
    DOCINTEL_DATA_DIR=/data DOCINTEL_API_HOST=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/README.md backend/.python-version ./
RUN uv sync --frozen --no-dev --all-extras --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev --all-extras
COPY --from=web /app/frontend/dist /app/frontend/dist
RUN useradd --create-home --uid 10001 docintel && mkdir -p /data && chown docintel /data
USER docintel
VOLUME ["/data"]
EXPOSE 8077
# Configuration (model endpoint, credentials) comes from the environment at run time —
# never baked into the image.
CMD ["uv", "run", "--no-sync", "python", "-m", "docintel", "serve"]
