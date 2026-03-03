# ============================================================================
# Multi-stage Dockerfile for LLMOps Pipeline + Agent Serving
# ============================================================================
# Stage 1: Build dependencies with uv (fast Python package manager)
# Stage 2: Slim production image
# ============================================================================

# --- Stage 1: Builder -------------------------------------------------------
FROM python:3.11-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (Docker cache optimization)
COPY pyproject.toml poetry.toml ./

# Export dependencies to requirements format and install them
# This step is cached as long as pyproject.toml doesn't change
RUN uv pip compile pyproject.toml -o /tmp/requirements.txt \
    && uv pip install --system --no-cache-dir -r /tmp/requirements.txt

# Copy source code
COPY src/ src/
COPY serving/ serving/
COPY confs/ confs/
COPY kfp_pipelines/ kfp_pipelines/

# Install the project itself (deps already installed, so --no-deps)
RUN uv pip install --system --no-cache-dir --no-deps -e .

# --- Stage 2: Production ----------------------------------------------------
FROM python:3.11-slim AS production

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --from=builder /app/src/ src/
COPY --from=builder /app/serving/ serving/
COPY --from=builder /app/confs/ confs/
COPY --from=builder /app/kfp_pipelines/ kfp_pipelines/

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Expose port
EXPOSE 8080

# Run the serving layer
CMD ["python", "-m", "uvicorn", "serving.server:app", "--host", "0.0.0.0", "--port", "8080"]
