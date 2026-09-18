# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for P&ID Studio Backend & Worker
FROM python:3.13-slim AS runtime

# Runtime dependencies for OpenCV, PyMuPDF, and PyTorch
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

FROM runtime AS builder

# Build wheels in a disposable stage. Build tools and pip caches never enter
# the final API/worker image.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install --prefer-binary -r requirements.txt

FROM runtime AS base

COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# Copy source code and models
COPY . /app/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/.. \
    OMP_NUM_THREADS=1 \
    KMP_DUPLICATE_LIB_OK=TRUE

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
