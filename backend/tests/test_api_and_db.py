import os
import sys
import pytest
import asyncio

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.db.base import Base
from app.models.project import Project
from app.models.sheet import Sheet
from app.schemas.result import DigitizationResult
from app.services.grouping_service import GroupingService
from app.services.export_service import ExportService
from PIL import Image

TEST_DB_URL = "sqlite+aiosqlite:///./test_pidstudio.db"
test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)



@pytest.mark.asyncio
async def test_db_project_sheet_lifecycle():
    # 1. Clean & create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # 2. Insert Project
    async with TestSessionLocal() as session:
        proj = Project(
            id="test-proj-001",
            name="Test Plant Unit 605",
            description="Testing project creation",
            tenant_id="default_tenant",
            user_id="default_user",
        )
        session.add(proj)
        await session.commit()

        # 3. Add Sheet to Project
        sheet = Sheet(
            id="test-sheet-001",
            project_id=proj.id,
            filename="BCD3-605-test.png",
            file_path="projects/test-proj-001/sheets/test-sheet-001.png",
            status="uploaded",
            tenant_id="default_tenant",
            user_id="default_user",
            dpi=350,
        )
        session.add(sheet)
        await session.commit()

    # 4. Query and verify
    async with TestSessionLocal() as session:
        queried = await session.get(Project, "test-proj-001")
        assert queried is not None
        assert queried.name == "Test Plant Unit 605"
        assert len(queried.sheets) == 1
        assert queried.sheets[0].id == "test-sheet-001"

    print("Project & Sheet database test PASSED successfully!")


def test_grouping_and_validation():
    # Test grouping service with sample result payload
    mock_result = {
        "image_path": "test.png",
        "dpi": 350,
        "rot": 0,
        "w": 3300,
        "h": 2320,
        "symbols": [
            {"coarse": "equipment", "cls": "equipment_box", "conf": 0.99, "x1": 100, "y1": 100, "x2": 400, "y2": 400}
        ],
        "runs": [
            {"points": [[100, 500], [600, 500]], "axis": "h", "x1": 100, "y1": 500, "x2": 600, "y2": 500, "underline": False}
        ],
        "piping_ids": [
            {"pid": "605-6\"-GF-CCB-001", "x1": 200, "y1": 480, "x2": 350, "y2": 500, "unit": "605", "size": "6\"", "fluid": "GF", "pclass": "CCB", "seq": "001", "conf": 5, "run_idx": 0, "state": "attached", "extra_runs": [], "manual": False}
        ],
        "conn_points": [],
        "furniture": []
    }

    # Verify Pydantic schema validation
    validated = DigitizationResult(**mock_result)
    assert validated.w == 3300
    assert len(validated.piping_ids) == 1

    # Verify API RP 970 grouping
    circuits = GroupingService.compute_circuits(mock_result)
    assert len(circuits) == 1
    assert circuits[0]["fluid"] == "GF"

    # Verify Validation checks
    report = GroupingService.validate(mock_result)
    assert "score" in report
    assert report["n_piping_ids"] == 1

    print("Grouping and Validation logic test PASSED successfully!")


def test_exports(tmp_path):
    drawing_path = tmp_path / "drawing.png"
    Image.new("RGB", (1000, 1000), "white").save(drawing_path)
    mock_result = {
        "image_path": str(drawing_path),
        "dpi": 350,
        "rot": 0,
        "w": 3300,
        "h": 2320,
        "symbols": [
            {"coarse": "equipment", "cls": "equipment_box", "conf": 0.99, "x1": 100, "y1": 100, "x2": 400, "y2": 400}
        ],
        "runs": [
            {"points": [[100, 500], [600, 500]], "axis": "h", "x1": 100, "y1": 500, "x2": 600, "y2": 500, "underline": False}
        ],
        "piping_ids": [
            {"pid": "605-6\"-GF-CCB-001", "x1": 200, "y1": 480, "x2": 350, "y2": 500, "unit": "605", "size": "6\"", "fluid": "GF", "pclass": "CCB", "seq": "001", "conf": 5, "run_idx": 0, "state": "attached", "extra_runs": [], "manual": False}
        ],
        "conn_points": [],
        "furniture": []
    }

    # Test Excel Line Register export
    xlsx_path = ExportService.export(mock_result, "BCD3-605-test", "xlsx")
    assert os.path.exists(xlsx_path)
    assert os.path.getsize(xlsx_path) > 1000

    # Test Word Asset Register export
    docx_path = ExportService.export(mock_result, "BCD3-605-test", "docx")
    assert os.path.exists(docx_path)
    assert os.path.getsize(docx_path) > 1000

    # Test Vector PDF export (Acrobat-editable polyline annotations)
    pdf_path = ExportService.export(mock_result, "BCD3-605-test", "pdf", mode="system")
    assert os.path.exists(pdf_path)
    assert os.path.getsize(pdf_path) > 1000

    print("Deliverable exports (XLSX, DOCX, Vector PDF) PASSED successfully!")


if __name__ == "__main__":
    asyncio.run(test_db_project_sheet_lifecycle())
    test_grouping_and_validation()
    test_exports()

