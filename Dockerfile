# ==============================================================================
# Dockerfile — LLMOps Pipeline Agent (Cloud Run)
# ==============================================================================
# Two-stage build:
#   1. Builder — install Python dependencies via Poetry
#   2. Production — slim runtime with only what's needed
# ==============================================================================

# --------------- Stage 1: Builder -------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for building wheels (grpcio, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry (pinned version for reproducibility)
RUN pip install --no-cache-dir "poetry==1.8.4"

# Copy dependency manifests first (Docker layer caching)
COPY pyproject.toml poetry.toml README.md ./

# Install dependencies into system site-packages (no venv in Docker)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

# Copy source + install the project package itself
COPY src/ src/
RUN poetry install --only main --no-interaction --no-ansi

# --------------- Stage 2: Production ----------------------------------------
FROM python:3.11-slim AS production

# Runtime-only system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages \
                    /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code (serving layer + configs)
COPY serving/ serving/
COPY confs/ confs/
COPY kfp_pipelines/ kfp_pipelines/

# Ensure serving is on PYTHONPATH (not installed as a package)
ENV PYTHONPATH="/app"

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8080/health || exit 1

CMD ["python", "-m", "uvicorn", "serving.server:app", "--host", "0.0.0.0", "--port", "8080"]
