import os
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "P&ID Studio Web Platform"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    # Configure a stable shared secret in deployed environments. The random
    # fallback keeps local development from using a publicly known signing key.
    JWT_SECRET_KEY: str = Field(default_factory=lambda: os.urandom(48).hex())
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    # Empty by default: break-glass login stays disabled until IT provisions a
    # high-entropy value in the deployment environment.
    SUPERUSER_SECRET_KEY: str = ""
    BIMA_BASE_URL: str = "https://bima.reksolindo.co.id"
    BIMA_CLIENT_ID: str = ""
    # Live BIMA integration is the default. Enable mock behavior explicitly in
    # local test/development environments so a deployed callback cannot accept
    # caller-supplied identities as authenticated users.
    BIMA_PROJECTS_MOCK_MODE: bool = False
    BIMA_SSO_MOCK_MODE: bool = False
    BIMA_MOCK_PROJECTS_JSON: str = '[{"bima_project_id":"BIMA-MOCK-001","name":"Mock P&ID Beta Project","description":"Sample project from the mock BIMA API","assigned_bima_user_ids":["bima-demo-user"]}]'

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
    AUTO_TRACE_ON_UPLOAD: bool = Field(
        default=True,
        description="Jalankan Fast Trace (lines_only) otomatis untuk setiap sheet yang baru diunggah",
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
