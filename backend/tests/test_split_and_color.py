import os
import sys
import pytest
import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr.lines import PipeRun, split_poly_run
try:
    from app.services.export_service import ExportService
except ImportError:
    from backend.app.services.export_service import ExportService


def test_split_poly_run_geometry():
    """Verify polyline splitting creates 2 continuous PipeRuns preserving coordinates."""
    # Run from (0, 0) to (100, 0)
    runs = [{
        "points": [[0, 0], [100, 0]],
        "axis": "h",
        "x1": 0, "y1": 0, "x2": 100, "y2": 0,
        "color": "#2563EB",
    }]
    # Split at (40, 3) -> projects to (40, 0)
    new_runs, _, run_a, run_b, new_idx = split_poly_run(
        runs=runs,
        run_idx=0,
        split_x=40.0,
        split_y=3.0,
    )

    assert len(new_runs) == 2
    assert new_idx == 1
    # Check run_a
    assert run_a["points"] == [[0, 0], [40, 0]]
    assert run_a["color"] == "#2563EB"
    # Check run_b
    assert run_b["points"] == [[40, 0], [100, 0]]
    assert run_b["color"] == "#2563EB"
    # Verify exact continuity at split point
    assert run_a["points"][-1] == run_b["points"][0]


def test_split_multi_segment_polyline():
    """Verify splitting a 3-point bend polyline [(0,0), (50,0), (50,100)]."""
    runs = [{
        "points": [[0, 0], [50, 0], [50, 100]],
        "axis": "poly",
        "color": "#2563EB",
    }]
    # Split on vertical segment at (50, 60)
    new_runs, _, run_a, run_b, _ = split_poly_run(
        runs=runs,
        run_idx=0,
        split_x=52.0,
        split_y=60.0,
    )
    assert len(new_runs) == 2
    assert run_a["points"] == [[0, 0], [50, 0], [50, 60]]
    assert run_b["points"] == [[50, 60], [50, 100]]


def test_split_poly_run_reindex_piping_ids():
    """Verify piping ID attribution: closer half keeps ID, other indices shift."""
    runs = [
        {"points": [[0, 0], [100, 0]], "axis": "h", "color": "#2563EB"},
        {"points": [[200, 200], [300, 200]], "axis": "h", "color": "#2563EB"},
    ]
    pids = [
        # Attached to run 0, near (20, 0) -> should stay run_idx=0
        {"pid": "10-P-001", "run_idx": 0, "x1": 15, "y1": -5, "x2": 25, "y2": 5},
        # Attached to run 0, near (80, 0) -> should reassign to run_idx=1 (run_b)
        {"pid": "10-P-002", "run_idx": 0, "x1": 75, "y1": -5, "x2": 85, "y2": 5},
        # Attached to run 1 -> should shift to run_idx=2
        {"pid": "10-P-003", "run_idx": 1, "x1": 240, "y1": 195, "x2": 260, "y2": 205},
    ]

    new_runs, new_pids, _, _, _ = split_poly_run(
        runs=runs,
        run_idx=0,
        split_x=50.0,
        split_y=0.0,
        piping_ids=pids,
    )

    assert len(new_runs) == 3
    assert new_pids[0]["run_idx"] == 0  # 10-P-001 assigned to run_a
    assert new_pids[1]["run_idx"] == 1  # 10-P-002 assigned to run_b
    assert new_pids[2]["run_idx"] == 2  # 10-P-003 shifted from 1 to 2


@pytest.mark.asyncio
async def test_api_split_and_color_endpoints():
    """Test full API router flow for split line and recoloring."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    try:
        from app.db.base import Base
        from app.models.project import Project
        from app.models.sheet import Sheet
        from app.routers.results import (
            split_run, update_run_color, batch_update_run_colors, update_run_points,
            SplitRunRequest, UpdateRunColorRequest, BatchUpdateRunColorsRequest, UpdateRunPointsRequest
        )
    except ImportError:
        from backend.app.db.base import Base
        from backend.app.models.project import Project
        from backend.app.models.sheet import Sheet
        from backend.app.routers.results import (
            split_run, update_run_color, batch_update_run_colors, update_run_points,
            SplitRunRequest, UpdateRunColorRequest, BatchUpdateRunColorsRequest, UpdateRunPointsRequest
        )
    import uuid

    test_engine = create_async_engine("sqlite+aiosqlite:///./test_split.db", connect_args={"check_same_thread": False})
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    TestSession = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async with TestSession() as db:
        proj_id = str(uuid.uuid4())
        sheet_id = str(uuid.uuid4())

        project = Project(id=proj_id, name="Test Split Project")
        db.add(project)

        sheet = Sheet(
            id=sheet_id,
            project_id=proj_id,
            filename="test_sheet.png",
            file_path="test_sheet.png",
            status="detected",
            result_json={
                "w": 1000,
                "h": 1000,
                "runs": [
                    {"points": [[0, 100], [200, 100]], "axis": "h", "color": "#2563EB", "x1": 0, "y1": 100, "x2": 200, "y2": 100},
                    {"points": [[0, 200], [200, 200]], "axis": "h", "color": "#2563EB", "x1": 0, "y1": 200, "x2": 200, "y2": 200},
                ],
                "piping_ids": [
                    {"pid": "LINE-01", "run_idx": 0, "x1": 30, "y1": 90, "x2": 50, "y2": 110},
                    {"pid": "LINE-02", "run_idx": 1, "x1": 30, "y1": 190, "x2": 50, "y2": 210},
                ]
            }
        )
        db.add(sheet)
        await db.commit()

        # 1. Test Split Endpoint
        split_res = await split_run(
            project_id=proj_id,
            sheet_id=sheet_id,
            run_idx=0,
            payload=SplitRunRequest(x=100.0, y=100.0),
            db=db,
        )
        assert split_res["status"] == "success"
        assert split_res["total_runs"] == 3
        assert len(split_res["result"]["runs"]) == 3

        # 2. Test Single Color Update Endpoint
        color_res = await update_run_color(
            project_id=proj_id,
            sheet_id=sheet_id,
            run_idx=0,
            payload=UpdateRunColorRequest(color="#DC2626"),
            db=db,
        )
        assert color_res["status"] == "success"
        assert color_res["color"] == "#DC2626"
        assert color_res["result"]["runs"][0]["color"] == "#DC2626"

        # 3. Test Batch Color Update Endpoint (Multi-select)
        batch_res = await batch_update_run_colors(
            project_id=proj_id,
            sheet_id=sheet_id,
            payload=BatchUpdateRunColorsRequest(run_idxs=[0, 1, 2], color="#16A34A"),
            db=db,
        )
        assert batch_res["status"] == "success"
        assert batch_res["updated_count"] == 3
        assert batch_res["result"]["runs"][0]["color"] == "#16A34A"
        assert batch_res["result"]["runs"][1]["color"] == "#16A34A"
        assert batch_res["result"]["runs"][2]["color"] == "#16A34A"

        # 4. Test Update Run Points Endpoint (Drag vertex to straighten)
        pts_res = await update_run_points(
            project_id=proj_id,
            sheet_id=sheet_id,
            run_idx=0,
            payload=UpdateRunPointsRequest(points=[[10.0, 10.0], [200.0, 10.0]]),
            db=db,
        )
        assert pts_res["status"] == "success"
        assert pts_res["points"] == [[10.0, 10.0], [200.0, 10.0]]
        assert pts_res["result"]["runs"][0]["points"] == [[10.0, 10.0], [200.0, 10.0]]
        assert pts_res["result"]["runs"][0]["axis"] == "h"

    # Cleanup sqlite test db
    await test_engine.dispose()
    if os.path.exists("./test_split.db"):
        os.remove("./test_split.db")


def test_export_engineer_mode():
    """Verify PyMuPDF export correctly reads polyline state and custom run colors."""
    import cv2
    img = np.full((600, 800, 3), 255, dtype=np.uint8)
    tmp_img_path = os.path.join(_ROOT, "backend", "tests", "fixtures", "tmp_test_drawing.png")
    os.makedirs(os.path.dirname(tmp_img_path), exist_ok=True)
    cv2.imwrite(tmp_img_path, img)

    result = {
        "image_path": tmp_img_path,
        "dpi": 350,
        "rot": 0,
        "w": 800,
        "h": 600,
        "runs": [
            {"points": [[50, 100], [300, 100]], "axis": "h", "color": "#DC2626", "x1": 50, "y1": 100, "x2": 300, "y2": 100},
            {"points": [[300, 100], [550, 100]], "axis": "h", "color": "#16A34A", "x1": 300, "y1": 100, "x2": 550, "y2": 100},
            {"points": [[100, 300], [500, 300]], "axis": "h", "color": "#2563EB", "x1": 100, "y1": 300, "x2": 500, "y2": 300},
        ],
        "piping_ids": [
            {"pid": "10-P-101", "run_idx": 0, "x1": 100, "y1": 90, "x2": 150, "y2": 110},
        ]
    }

    # 1. Test PDF Export
    pdf_out = ExportService.export(result, "test_drawing", "pdf", mode="engineer")
    assert os.path.exists(pdf_out)
    assert os.path.getsize(pdf_out) > 500

    # 2. Test PNG Export
    png_out = ExportService.export(result, "test_drawing", "png", mode="engineer")
    assert os.path.exists(png_out)
    assert os.path.getsize(png_out) > 500

    # Clean up temp test image
    if os.path.exists(tmp_img_path):
        os.remove(tmp_img_path)
