@echo off
REM =================================================================
REM  P&ID Studio Web Platform - Native Local Startup (without Docker)
REM =================================================================

echo ==================================================================
echo   Starting P^&ID Studio Web Platform (Local Development Mode)
echo ==================================================================

if not exist .env (
    copy .env.example .env >nul
)

echo [*] Starting FastAPI Backend on http://localhost:8000 ...
start "PIDStudio-Backend" cmd /k "cd backend && py -3.13 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

echo [*] Starting Next.js Frontend on http://localhost:3000 ...
start "PIDStudio-Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo ==================================================================
echo   P^&ID Studio services started in separate terminal windows:
echo   - Web UI:      http://localhost:3000
echo   - REST API:    http://localhost:8000
echo   - Swagger Doc: http://localhost:8000/docs
echo ==================================================================
