from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from ..config import settings
from .base import Base

# Configure engine arguments
engine_kwargs = {"echo": False}
if "sqlite" in settings.DATABASE_URL:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Initialize database tables."""
    # Import all models so Base has metadata registered
    from ..models import project, sheet, job, user, ground_truth_log  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not add columns to existing databases. Keep old projects
        # without an owner inaccessible until explicitly assigned to an account.
        def add_legacy_owner_column(sync_conn):
            from sqlalchemy import inspect

            inspector = inspect(sync_conn)
            tables = set(inspector.get_table_names())
            if "projects" in tables:
                project_columns = {column["name"] for column in inspector.get_columns("projects")}
                if "owner_id" not in project_columns:
                    sync_conn.exec_driver_sql(
                        "ALTER TABLE projects ADD COLUMN owner_id VARCHAR(36) REFERENCES users(id) ON DELETE CASCADE"
                    )
                if "bima_project_id" not in project_columns:
                    sync_conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN bima_project_id VARCHAR(255)")
                if "project_number" not in project_columns:
                    sync_conn.exec_driver_sql(
                        "ALTER TABLE projects ADD COLUMN project_number VARCHAR(64) NOT NULL DEFAULT ''"
                    )
                sync_conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_projects_owner_id ON projects (owner_id)"
                )
                sync_conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_projects_bima_project_id ON projects (bima_project_id)"
                )

            # BIMA exposes a flat project catalog and assigns no users, so the
            # assignment table has no writer left. Local access is owner-only.
            if "project_assignments" in tables:
                sync_conn.exec_driver_sql("DROP TABLE IF EXISTS project_assignments")

            if "users" in tables:
                user_columns = {column["name"] for column in inspector.get_columns("users")}
                if "bima_user_id" not in user_columns:
                    sync_conn.exec_driver_sql("ALTER TABLE users ADD COLUMN bima_user_id VARCHAR(255)")
                if "hashed_password" in user_columns:
                    sync_conn.exec_driver_sql("ALTER TABLE users DROP COLUMN hashed_password")
                sync_conn.exec_driver_sql(
                    "UPDATE users SET is_active = FALSE WHERE bima_user_id IS NULL"
                )
                sync_conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_bima_user_id ON users (bima_user_id)"
                )

        await conn.run_sync(add_legacy_owner_column)
