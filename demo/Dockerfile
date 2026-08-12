# ─────────────────────────────────────────────────────────────────────────────
# SnapAdmin — Production Dockerfile
#
# Multi-stage build:
#   builder  — installs Python dependencies into a venv
#   runtime  — minimal image with only the venv and app code
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Dependency builder ────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install system build tools needed for psycopg2 and Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create an isolated virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install Python dependencies
COPY demo/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime image ─────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Runtime system dependencies only (libpq for psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the pre-built venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy project source
COPY --chown=appuser:appuser . .

# Create required directories and fix permissions
RUN mkdir -p .staticfiles .media && \
    chown -R appuser:appuser .staticfiles .media

USER appuser

# Expose Gunicorn port
EXPOSE 8000

# ── Health check ───────────────────────────────────────────────────────────
# SnapAdmin serves an unauthenticated probe at /api/health/ that checks the
# database and returns 503 when it is unreachable — so `curl -f` fails and the
# runtime restarts or replaces the container. Do NOT probe /admin/ instead: it
# answers 302 (redirect to login) while the database is down, i.e. it reports a
# broken instance as healthy.
#
# An anonymous caller gets only {"status": "healthy"} — never the per-service
# breakdown — so this endpoint is safe to expose to a load balancer.
#
# start-period covers migrate + collectstatic + seed on first boot; raise it if
# your own start-up work takes longer. This image bundles curl for exactly this.
#
# Two things to know before reusing this image for a non-web process:
#   * The route lives behind SNAPADMIN_REST_API_ENABLED (default True). With the
#     REST API switched off there is no /api/health/ and the probe fails on a
#     perfectly healthy container — probe something you do serve instead.
#   * A HEALTHCHECK is inherited by every container started from the image. The
#     Celery worker and Beat serve no HTTP, so docker-compose.yml overrides the
#     worker's probe with `celery inspect ping` and disables Beat's outright.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health/ || exit 1

# Default command (overridden in docker-compose for each service)
CMD ["gunicorn", "demo.core.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
