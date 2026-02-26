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
COPY requirements.txt .
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

# Default command (overridden in docker-compose for each service)
CMD ["gunicorn", "sandbox.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
