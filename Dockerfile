# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for medrec-superpower MCP server.
# Stage 1 builds with uv; stage 2 runs on python:3.11-slim with only the venv.

ARG PYTHON_VERSION=3.11

# ---------------------------------------------------------------------- builder
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

# Install uv via the official installer
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copy only dependency manifests first for layer caching
COPY pyproject.toml uv.lock* ./

# Resolve and install runtime deps into a project venv
RUN uv sync --frozen --no-dev --no-install-project 2>/dev/null || \
    uv sync --no-dev --no-install-project

# Copy source and install the project itself
COPY medrec_superpower/ ./medrec_superpower/
COPY tests/fixtures/ ./tests/fixtures/

RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

# ----------------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    MEDREC_HOST=0.0.0.0 \
    MEDREC_PORT=8765 \
    MEDREC_LOG_LEVEL=INFO

# Non-root user for runtime
RUN groupadd --gid 1000 medrec && \
    useradd --uid 1000 --gid medrec --shell /bin/bash --create-home medrec

WORKDIR /app

# Copy venv and source from builder
COPY --from=builder --chown=medrec:medrec /build/.venv /app/.venv
COPY --from=builder --chown=medrec:medrec /build/medrec_superpower /app/medrec_superpower
COPY --from=builder --chown=medrec:medrec /build/tests/fixtures /app/tests/fixtures

USER medrec

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/healthz', timeout=2)" \
        || exit 1

CMD ["python", "-m", "medrec_superpower"]
