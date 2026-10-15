# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for P&ID Studio Backend & Worker
FROM python:3.13-slim AS runtime

# Runtime dependencies for OpenCV, PyMuPDF, and PyTorch
RUN --mount=type=cache,target=/var/cache/apt,id=backend-apt-cache,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,id=backend-apt-lib,sharing=locked \
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
RUN --mount=type=cache,target=/var/cache/apt,id=backend-apt-cache,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,id=backend-apt-lib,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv

# Torch wheel source for pip. Unset (default) installs the CPU-only wheels from
# the index configured in requirements.txt. GPU hosts pass a CUDA index so the
# +cuXXX wheels win, e.g. --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126
ARG TORCH_INDEX_URL

RUN --mount=type=cache,target=/root/.cache/pip,id=backend-pip-cache,sharing=locked \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install --prefer-binary \
        ${TORCH_INDEX_URL:+--extra-index-url ${TORCH_INDEX_URL}} \
        -r requirements.txt

# A GPU build must not silently fall back to CPU-only inference.
RUN if [ -n "$TORCH_INDEX_URL" ]; then \
        /opt/venv/bin/python -c "import torch,sys; print('torch', torch.__version__, '| cuda', torch.version.cuda); sys.exit(0 if torch.version.cuda else 'ERROR: TORCH_INDEX_URL was set but CPU-only torch got installed')"; \
    fi

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
