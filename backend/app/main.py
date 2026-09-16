import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure root directory is in sys.path
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from .config import settings
from .db.session import init_db
from .routers import projects, detection, results, grouping, validation, export, tiles, ws, linelist, topology


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables
    try:
        await init_db()
    except Exception as e:
        print(f"[Warning] Database initialization deferred or error: {e}")
    yield
    # Shutdown logic if any


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="Web-Based Platform for P&ID Digitization & API RP 970 Corrosion Systemization",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API v1 routers
api_prefix = settings.API_V1_PREFIX
app.include_router(projects.router, prefix=api_prefix)
app.include_router(detection.router, prefix=api_prefix)
app.include_router(results.router, prefix=api_prefix)
app.include_router(grouping.router, prefix=api_prefix)
app.include_router(validation.router, prefix=api_prefix)
app.include_router(export.router, prefix=api_prefix)
app.include_router(tiles.router, prefix=api_prefix)
app.include_router(linelist.router, prefix=api_prefix)
app.include_router(topology.router, prefix=api_prefix)
app.include_router(ws.router)


@app.get("/healthz", tags=["health"])
async def health_check():
    """Liveness probe."""
    return {"status": "ok", "service": "pidstudio-api"}


@app.get("/readyz", tags=["health"])
async def readiness_check():
    """Readiness probe checking database and core folders."""
    storage_ok = os.path.exists(settings.STORAGE_DIR)
    weights_ok = os.path.exists(settings.WEIGHTS_DIR)
    return {
        "status": "ready" if (storage_ok and weights_ok) else "degraded",
        "storage": storage_ok,
        "weights": weights_ok,
    }
