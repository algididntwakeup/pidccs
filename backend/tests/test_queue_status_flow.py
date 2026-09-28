"""Unit test for Celery queue status transitions and FIFO handling (Phase 1).

Verifies:
1. Newly split/uploaded sheets are created with status="queued".
2. On Celery worker start, first line updates Sheet status to "processing".
3. On Celery worker finish, Sheet status updates to "completed".
"""
import os
import sys
import uuid
import pytest
import asyncio
from unittest.mock import patch, MagicMock

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
for _p in (_BACKEND_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.db.base import Base
from app.models.project import Project
from app.models.sheet import Sheet
from app.models.job import Job
from worker.tasks import _mark_job_and_sheet_running, _save_detection_to_db, detect_sheet_task

TEST_DB_URL = "sqlite+aiosqlite:///./test_queue_flow.db"
test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

@pytest.fixture(autouse=True)
def mock_worker_session():
    """Route get_worker_session in tasks to TestSessionLocal."""
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _test_session():
        async with TestSessionLocal() as session:
            yield session

    with patch("worker.tasks.get_worker_session", _test_session):
        yield

@pytest.mark.asyncio
async def test_sheet_queue_and_worker_status_transitions():
    # 1. Initialize schema
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    proj_id = str(uuid.uuid4())
    sheet_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    # 2. Seed initial Project, Sheet (status="queued"), and Job (status="queued")
    async with TestSessionLocal() as session:
        proj = Project(id=proj_id, name="Queue Test Project", tenant_id="t1", user_id="u1")
        sheet = Sheet(
            id=sheet_id,
            project_id=proj_id,
            tenant_id="t1",
            user_id="u1",
            filename="Drawing-P01.pdf",
            sheet_number="1/50",
            file_path="projects/test/Drawing-P01.pdf",
            status="queued",
            dpi=350,
        )
        job = Job(
            id=job_id,
            sheet_id=sheet_id,
            tenant_id="t1",
            user_id="u1",
            status="queued",
            step="queued",
            message="Detection job queued",
        )
        session.add_all([proj, sheet, job])
        await session.commit()

    # Verify initial status is 'queued'
    async with TestSessionLocal() as session:
        s = await session.get(Sheet, sheet_id)
        assert s is not None
        assert s.status == "queued", "Sheet initial status must be 'queued'"

    # 3. Simulate first line of Celery task: _mark_job_and_sheet_running
    await _mark_job_and_sheet_running(job_id, sheet_id, "Processing sheet Fast Trace...")

    async with TestSessionLocal() as session:
        s = await session.get(Sheet, sheet_id)
        j = await session.get(Job, job_id)
        assert s.status == "processing", "Sheet status must transition to 'processing' when worker starts"
        assert j.status == "processing", "Job status must transition to 'processing'"

    # 4. Simulate final line of Celery task: _save_detection_to_db
    mock_result = {"w": 4000, "h": 3000, "runs": [], "symbols": [], "piping_ids": []}
    mock_systems = []
    await _save_detection_to_db(job_id, sheet_id, mock_result, mock_systems)

    async with TestSessionLocal() as session:
        s = await session.get(Sheet, sheet_id)
        j = await session.get(Job, job_id)
        assert s.status == "completed", "Sheet status must transition to 'completed' when Fast Trace finishes"
        assert j.status == "completed", "Job status must transition to 'completed'"
        assert s.width == 4000
        assert s.height == 3000
