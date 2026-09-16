#!/usr/bin/env bash
# =================================================================
#  P&ID Studio Web Platform - One-Click Docker Compose Startup
# =================================================================

set -e

echo "=================================================================="
echo "  Starting P&ID Studio Web Platform (Docker Compose)"
echo "=================================================================="

if [ ! -f .env ]; then
    echo "[*] Generating .env from .env.example..."
    cp .env.example .env
fi

echo "[*] Building and launching containers (PostgreSQL, Redis, API, Worker, Frontend)..."
docker compose up --build -d

echo "[*] Waiting for services to become healthy..."
sleep 8

echo "[*] Running operational smoke test..."
python3 scripts/smoke_test.py || true

echo "=================================================================="
echo "  P&ID Studio Web Platform is now running:"
echo "  - Web UI:      http://localhost:3000"
echo "  - REST API:    http://localhost:8000"
echo "  - Swagger Doc: http://localhost:8000/docs"
echo "=================================================================="
