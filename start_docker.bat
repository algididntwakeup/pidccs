@echo off
REM =================================================================
REM  P&ID Studio Web Platform - One-Click Docker Compose Startup
REM =================================================================

echo ==================================================================
echo   Starting P^&ID Studio Web Platform (Docker Compose)
echo ==================================================================

if not exist .env (
    echo [*] Generating .env from .env.example...
    copy .env.example .env >nul
)

echo [*] Building and launching containers (PostgreSQL, Redis, API, Worker, Frontend)...
docker compose up --build -d

echo.
echo [*] Waiting for services to become healthy...
timeout /t 8 /nobreak >nul

echo.
echo [*] Running operational smoke test...
py -3.13 scripts\smoke_test.py

echo.
echo ==================================================================
echo   P^&ID Studio Web Platform is now running:
echo   - Web UI:      http://localhost:3000
echo   - REST API:    http://localhost:8000
echo   - Swagger Doc: http://localhost:8000/docs
echo ==================================================================
pause
