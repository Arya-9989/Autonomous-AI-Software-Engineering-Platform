# syntax=docker/dockerfile:1.7

# --- 1. Build the React UI ---------------------------------------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Resolve Python dependencies from the lockfile -----------------------------------------
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# --- 3. Sandbox runner: the only image with a Docker client (see docs/adr/0001) -------------------
# Built with `--target sandbox-runner`. It runs no API and serves no UI; it starts sandbox
# containers from autoswe-sandbox on request of the API, with limits it alone decides.
FROM python:3.12-slim AS sandbox-runner
ENV PATH=/opt/venv/bin:$PATH     PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1
COPY --from=docker:29.8.0-cli@sha256:eccaacfeed644c7de222ff047483568cb988dde95476fbaaf10ea2d04921bb66     /usr/local/bin/docker /usr/local/bin/docker
RUN groupadd --system --gid 10001 autoswe     && useradd --system --uid 10001 --gid autoswe --home-dir /tmp --shell /usr/sbin/nologin autoswe
COPY --from=builder /opt/venv /opt/venv
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3   CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
CMD ["autoswe", "sandbox-runner"]

# --- 4. Minimal runtime (the default target) -----------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    STATIC_DIR=/app/static \
    DATABASE_URL=sqlite+aiosqlite:////data/autoswe.db
RUN groupadd --system --gid 10001 autoswe \
    && useradd --system --uid 10001 --gid autoswe --home-dir /app --shell /usr/sbin/nologin autoswe \
    && mkdir -p /data /app \
    && chown autoswe:autoswe /data
COPY --from=builder /opt/venv /opt/venv
COPY --from=frontend /build/frontend/dist /app/static
WORKDIR /app
USER 10001
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/healthz', timeout=2)"]
CMD ["autoswe", "serve"]
