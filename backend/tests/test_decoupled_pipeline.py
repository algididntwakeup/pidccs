import os
import sys
import pytest
import numpy as np
from unittest.mock import MagicMock

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from pidcorr.orchestrator import PipelineOrchestrator
from pidcorr.lines import PipeRun
from app.db.base import Base
from app.models.project import Project
from app.models.sheet import Sheet
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db.session import get_db

TEST_DB_URL = "sqlite+aiosqlite:///./test_decoupled.db"
test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def override_get_db():
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


def test_orchestrator_lines_only_mode():
    """Verify that mode='lines_only' bypasses OCR and YOLO and only executes line tracing."""
    mock_detector = MagicMock()
    mock_extractor = MagicMock()
    mock_tracer = MagicMock()
    mock_classifier = MagicMock()

    # Setup tracer to return 2 runs
    r1 = PipeRun(points=[(100, 200), (500, 200)], axis="h", id="run-0", label="")
    r2 = PipeRun(points=[(500, 200), (500, 800)], axis="v", id="run-1", label="")

    mock_tracer.trace.return_value = [r1, r2]

    orchestrator = PipelineOrchestrator(
        detector=mock_detector,
        extractor=mock_extractor,
        tracer=mock_tracer,
        classifier=mock_classifier,
        layout_weights="",
    )

    img = np.zeros((1000, 1000, 3), dtype=np.uint8)
    progress_calls = []

    result = orchestrator.run(
        img_bgr=img,
        dpi=350,
        progress=lambda msg: progress_calls.append(msg),
        mode="lines_only",
    )

    # In lines_only mode, OCR and YOLO should NOT be called at all
    mock_extractor.extract.assert_not_called()
    mock_detector.detect.assert_not_called()

    # Tracer MUST be called
    mock_tracer.trace.assert_called_once()

    # Verify result structure
    assert len(result["runs"]) == 2
    assert result["runs"][0]["id"] == "run-0"
    assert result["runs"][1]["id"] == "run-1"
    assert result["symbols"] == []
    assert result["piping_ids"] == []
    assert result["conn_points"] == []
    assert result["furniture"] == []
    assert any("lines_only" in msg for msg in progress_calls)


def test_vector_fast_trace_does_not_load_raster(monkeypatch):
    from pidcorr.implementations import vector_tracer
    from pidcorr import orchestrator as orchestrator_module

    monkeypatch.setattr(vector_tracer, "tier_of_pdf", lambda _path: "A1")
    monkeypatch.setattr(
        vector_tracer, "extract_vector_runs",
        lambda *_args, **_kwargs: [PipeRun(points=[(10, 20), (100, 20)], axis="h")],
    )
    monkeypatch.setattr(orchestrator_module, "_pdf_pixel_size", lambda *_args: (1200, 800))
    load_image = MagicMock(side_effect=AssertionError("vector trace must not render a bitmap"))
    tracer = MagicMock()
    orchestrator = PipelineOrchestrator(
        detector=MagicMock(), extractor=MagicMock(), tracer=tracer,
        classifier=MagicMock(), layout_weights="",
    )

    result = orchestrator.run(
        img_bgr=None, image_path="drawing.pdf", mode="lines_only", image_loader=load_image,
    )

    load_image.assert_not_called()
    tracer.trace.assert_not_called()
    assert (result["w"], result["h"]) == (1200, 800)
    assert result["tracer"] == "vector:A1"


def test_pdf_pixel_size_matches_renderer(tmp_path):
    import pymupdf
    from app.adapters.pdf_renderer import load_drawing_image
    from pidcorr.orchestrator import _pdf_pixel_size

    path = tmp_path / "rotated.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=1191, height=842)
    page.set_rotation(270)
    pdf.save(path)
    pdf.close()

    image = load_drawing_image(str(path), dpi=72)
    assert _pdf_pixel_size(str(path), 72, 0) == (image.shape[1], image.shape[0])
    assert _pdf_pixel_size(str(path), 72, 90) == (image.shape[0], image.shape[1])


def test_orchestrator_run_enrichment_preserves_runs():
    """Verify that run_enrichment attaches symbols and piping IDs without altering run IDs or manual labels."""
    mock_detector = MagicMock()
    mock_extractor = MagicMock()
    mock_tracer = MagicMock()
    mock_classifier = MagicMock()

    # Mock OCR returning one piping ID near y=200
    mock_extractor.extract.return_value = (
        [
            {
                "pid": "605-4\"-HC-001",
                "x1": 150.0,
                "y1": 170.0,
                "x2": 300.0,
                "y2": 190.0,
                "fluid": "HC",
                "unit": "605",
                "size": "4\"",
                "pclass": "CS",
                "seq": "001",
                "conf": 90,
            }
        ],
        [
            {"t": "605-4\"-HC-001", "text": "605-4\"-HC-001", "x1": 150, "y1": 170, "x2": 300, "y2": 190, "ang": 0}
        ],
    )

    # Mock YOLO returning one valve symbol
    mock_detector.detect.return_value = [
        {
            "coarse": "valve",
            "cls": "gate_valve",
            "conf": 0.95,
            "x1": 480,
            "y1": 180,
            "x2": 520,
            "y2": 220,
        }
    ]

    orchestrator = PipelineOrchestrator(
        detector=mock_detector,
        extractor=mock_extractor,
        tracer=mock_tracer,
        classifier=mock_classifier,
        layout_weights="",
    )

    # Existing runs from prior lines_only or user edits
    existing_runs = [
        {
            "id": "run-0",
            "points": [[100, 200], [500, 200]],
            "axis": "h",
            "x1": 100,
            "y1": 200,
            "x2": 500,
            "y2": 200,
            "underline": False,
            "color": "#2563EB",
            "label": "",
            "manual": False,
        },
        {
            "id": "manual-run-12345",
            "points": [[500, 200], [500, 800]],
            "axis": "v",
            "x1": 500,
            "y1": 200,
            "x2": 500,
            "y2": 800,
            "underline": False,
            "color": "#10B981",
            "label": "USER-CUSTOM-TAG",
            "manual": True,
        },
    ]

    img = np.zeros((1000, 1000, 3), dtype=np.uint8)

    enrichment = orchestrator.run_enrichment(
        img_bgr=img,
        existing_runs=existing_runs,
        dpi=350,
    )

    # Verify detector and extractor were called
    mock_extractor.extract.assert_called_once()
    mock_detector.detect.assert_called_once()

    # Tracer should NOT be called in enrichment (no re-tracing)
    mock_tracer.trace.assert_not_called()

    # Verify run IDs are preserved exactly
    updated_runs = enrichment["runs"]
    assert len(updated_runs) == 2
    assert updated_runs[0]["id"] == "run-0"
    assert updated_runs[1]["id"] == "manual-run-12345"

    # Verify manual run with custom label is NOT overwritten
    assert updated_runs[1]["label"] == "USER-CUSTOM-TAG"
    assert updated_runs[1]["color"] == "#10B981"
    assert updated_runs[1]["manual"] is True

    # Verify symbols and piping_ids are populated
    assert len(enrichment["symbols"]) == 1
    assert enrichment["symbols"][0]["coarse"] == "valve"
    assert len(enrichment["piping_ids"]) == 1
    assert enrichment["piping_ids"][0]["pid"] == "605-4\"-HC-001"


@pytest.mark.asyncio
async def test_api_detect_mode_and_enrich_endpoints():
    """Verify REST API /detect with mode and /enrich endpoints."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        proj = Project(
            id="proj-enrich-test",
            name="Enrich Test Plant",
            tenant_id="default_tenant",
            user_id="default_user",
        )
        session.add(proj)

        sheet = Sheet(
            id="sheet-enrich-test",
            project_id=proj.id,
            filename="sheet.png",
            file_path="test_sheet.png",
            status="uploaded",
            tenant_id="default_tenant",
            user_id="default_user",
            dpi=350,
            result_json={
                "runs": [
                    {
                        "id": "run-0",
                        "points": [[50, 100], [400, 100]],
                        "axis": "h",
                        "x1": 50,
                        "y1": 100,
                        "x2": 400,
                        "y2": 100,
                        "label": "",
                    }
                ]
            },
        )
        session.add(sheet)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Test /detect with mode="lines_only"
        detect_resp = await client.post(
            "/api/v1/projects/proj-enrich-test/sheets/sheet-enrich-test/detect?mode=lines_only"
        )
        assert detect_resp.status_code == 202
        data = detect_resp.json()
        assert "job_id" in data
        assert data["sheet_id"] == "sheet-enrich-test"

        # 2. Test /enrich endpoint
        enrich_resp = await client.post(
            "/api/v1/projects/proj-enrich-test/sheets/sheet-enrich-test/enrich"
        )
        assert enrich_resp.status_code == 202
        enrich_data = enrich_resp.json()
        assert "job_id" in enrich_data
        assert enrich_data["sheet_id"] == "sheet-enrich-test"
