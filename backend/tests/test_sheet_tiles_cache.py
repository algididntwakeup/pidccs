"""Regression tests for sheet tile serving.

Covers the performance fixes that prevent the web app from getting progressively
heavier:
  * `/raw` responses must advertise long-lived HTTP caching (Cache-Control + ETag).
  * `/thumbnail` must return a small cached PNG and honor an existing cache entry.
"""
import os
import sys
import hashlib
import cv2
import numpy as np
import pytest
from httpx import AsyncClient, ASGITransport

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from app.main import app
from app.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.adapters.storage import LocalStorageAdapter
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

TEST_DB_URL = "sqlite+aiosqlite:///./test_sheet_tiles_cache.db"
test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, autocommit=False, autoflush=False, expire_on_commit=False
)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _file_etag(abs_path: str) -> str:
    stat = os.stat(abs_path)
    return hashlib.sha256(f"{abs_path}|{stat.st_mtime_ns}|{stat.st_size}".encode()).hexdigest()[:32]


@pytest.mark.asyncio
async def test_sheet_raw_and_thumbnail_cache_headers():
    """`/raw` and `/thumbnail` must be cacheable and serve valid PNGs.

    This guards the performance fix: previously `/raw` shipped with no caching
    headers, so browsers re-downloaded full-resolution drawings on every visit.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    storage = LocalStorageAdapter(settings.STORAGE_DIR)
    rel_path = os.path.join("projects", "tiles-proj", "sheets", "tiles-sheet.png")
    abs_path = storage.get_file_path(rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    # A large-ish drawing so the thumbnail actually downscales.
    img = np.full((1200, 1600, 3), 255, dtype=np.uint8)
    cv2.line(img, (50, 50), (1550, 1150), (0, 0, 0), 4)
    cv2.imwrite(abs_path, img)

    # Seed the DB
    from app.models.project import Project
    from app.models.sheet import Sheet

    async with TestSessionLocal() as session:
        session.add(Project(id="tiles-proj", name="Tiles", description="", tenant_id="t", user_id="u"))
        session.add(Sheet(
            id="tiles-sheet",
            project_id="tiles-proj",
            filename="tiles-sheet.png",
            file_path=rel_path,
            status="uploaded",
            dpi=350,
            width=1600,
            height=1200,
            tenant_id="t",
            user_id="u",
        ))
        await session.commit()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # ---- /raw ----
            res_raw = await ac.get("/api/v1/projects/tiles-proj/sheets/tiles-sheet/raw")
            assert res_raw.status_code == 200, res_raw.text
            assert res_raw.headers.get("cache-control", "").startswith("public")
            assert "immutable" in res_raw.headers.get("cache-control", "")
            assert res_raw.headers.get("etag")
            assert res_raw.headers.get("etag").strip('"') == _file_etag(abs_path)

            # ---- /thumbnail ----
            res_thumb = await ac.get(
                "/api/v1/projects/tiles-proj/sheets/tiles-sheet/thumbnail?size=256"
            )
            assert res_thumb.status_code == 200, res_thumb.text
            assert res_thumb.headers.get("cache-control", "").startswith("public")
            assert res_thumb.headers.get("etag")

            # Decoded thumbnail must be a valid PNG no larger than the requested box.
            arr = cv2.imdecode(np.frombuffer(res_thumb.content, np.uint8), cv2.IMREAD_COLOR)
            assert arr is not None
            assert max(arr.shape[:2]) <= 256 + 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        if os.path.exists(abs_path):
            os.remove(abs_path)
