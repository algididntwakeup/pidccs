import os
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "P&ID Studio Web Platform"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    # Multi-tenancy forward-compatibility (Phase A/B hardcoded defaults)
    DEFAULT_TENANT_ID: str = "default_tenant"
    DEFAULT_USER_ID: str = "default_user"

    # Database: PostgreSQL (with asyncpg driver) or fallback to sqlite for testing
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/pidstudio",
        description="Async database connection string"
    )
    DATABASE_SYNC_URL: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/pidstudio",
        description="Sync database connection string (for Alembic or sync scripts)"
    )

    # Redis & Task Queue
    REDIS_URL: str = Field(default="redis://localhost:6379/0", description="Redis broker and cache URL")
    CELERY_TASK_ALWAYS_EAGER: bool = Field(
        default=False,
        description="Set True to run Celery tasks synchronously (useful for test/dev without worker)"
    )

    # Storage Paths
    BASE_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    STORAGE_DIR: str = Field(
        default=os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), "data", "storage")
    )
    WEIGHTS_DIR: str = Field(
        default=os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), "runs")
    )
    CACHE_DIR: str = Field(
        default=os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), ".pidcache")
    )

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

# Ensure directories exist
os.makedirs(settings.STORAGE_DIR, exist_ok=True)
os.makedirs(settings.CACHE_DIR, exist_ok=True)
