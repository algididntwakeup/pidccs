import os
from typing import List, Literal
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "P&ID Studio Web Platform"
    API_V1_PREFIX: str = "/api/v1"
    APP_ENV: Literal["local", "development", "test", "staging", "production"] = "production"
    DEBUG: bool = False
    LOCAL_DEV_AUTH_BYPASS: bool = False

    # Configure a stable shared secret in deployed environments. The random
    # fallback keeps local development from using a publicly known signing key.
    JWT_SECRET_KEY: str = Field(default_factory=lambda: os.urandom(48).hex())
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    # Empty by default: break-glass login stays disabled until IT provisions a
    # high-entropy value in the deployment environment.
    SUPERUSER_SECRET_KEY: str = ""
    BIMA_BASE_URL: str = ""
    BIMA_CLIENT_ID: str = ""
    # Live BIMA SSO is the default. Enable the mock identity callback explicitly
    # in local test/development environments so a deployed callback cannot accept
    # caller-supplied identities as authenticated users.
    BIMA_SSO_MOCK_MODE: bool = False
    # Flat BIMA project catalog served by GET /api/v1/bima/projects. BIMA assigns
    # no users per project, so a row only carries its number and name.
    BIMA_MOCK_PROJECTS_JSON: str = (
        '[{"bima_project_id":"BIMA-MOCK-001","project_number":"BCD3-605-42","name":"Unit 605 Amine Treating"},'
        '{"bima_project_id":"BIMA-MOCK-002","project_number":"BCD3-612-07","name":"Unit 612 Sour Water Stripper"},'
        '{"bima_project_id":"BIMA-MOCK-003","project_number":"BCD3-701-11","name":"Unit 701 Flare Header"}]'
    )

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
    CORS_ORIGINS: List[str] = []

    @model_validator(mode="after")
    def validate_local_dev_auth_bypass(self):
        if self.LOCAL_DEV_AUTH_BYPASS and (self.APP_ENV != "local" or not self.DEBUG):
            raise ValueError("LOCAL_DEV_AUTH_BYPASS requires APP_ENV=local and DEBUG=true")
        return self

    model_config = SettingsConfigDict(
        env_file=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env")),
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

# Ensure directories exist
os.makedirs(settings.STORAGE_DIR, exist_ok=True)
os.makedirs(settings.CACHE_DIR, exist_ok=True)
