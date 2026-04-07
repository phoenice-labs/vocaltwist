# VocalTwist — Multi-stage production Dockerfile
#
# Security hardening applied:
#   - Non-root user (vocaltwist)
#   - Minimal base image (python:3.11-slim)
#   - No secrets baked into the image — all config via environment variables
#   - read-only filesystem friendly (logs should be mounted as a volume)

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies only in this stage (not in the final image)
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc \
      libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: slim runtime ─────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="VocalTwist" \
      org.opencontainers.image.description="Plug-and-play voice middleware" \
      org.opencontainers.image.version="1.0.0"

WORKDIR /app

# Security: create a dedicated non-root user
RUN groupadd -r vocaltwist && useradd -r -g vocaltwist -d /app -s /sbin/nologin vocaltwist

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY backend/    ./backend/
COPY frontend/   ./frontend/
COPY VocalTwistTest/ ./VocalTwistTest/
COPY .env.example   ./.env.example

# Fix ownership
RUN chown -R vocaltwist:vocaltwist /app

USER vocaltwist

EXPOSE 8000

# Disable Python output buffering so logs are visible immediately
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Health check — fast, no shell dependency
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import httpx; r=httpx.get('http://localhost:8000/api/health',timeout=5); r.raise_for_status()" \
  || exit 1

CMD ["uvicorn", "VocalTwistTest.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
