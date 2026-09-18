import os
import sys
import pytest
import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr.lines import PipeRun, snap_endpoints_to_equipment
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer


def test_pipe_run_color_attribute():
    """Verify PipeRun default color is #2563EB and supports dict access."""
    run = PipeRun(points=[(10, 20), (100, 20)], axis="h")
    assert run.color == "#2563EB"
    assert run["color"] == "#2563EB"

    custom = PipeRun(points=[(0, 0), (50, 50)], axis="d", color="#DC2626")
    assert custom.color == "#DC2626"
    assert custom["color"] == "#DC2626"


def test_snap_endpoints_to_equipment_horizontal():
    """Verify horizontal pipe approaching vessel wall snaps exactly to perimeter."""
    # Vessel from x=500 to x=800, y=200 to y=600
    detections = [{
        "coarse": "equipment",
        "cls": "vessel",
        "x1": 500, "y1": 200, "x2": 800, "y2": 600
    }]
    # Horizontal line approaching left vessel wall (ends at x=475, y=350)
    run = PipeRun(points=[(100, 350), (475, 350)], axis="h")
    snapped = snap_endpoints_to_equipment([run], detections, snap_radius_pt=14, dpi=350)

    assert len(snapped) == 1
    # At 350 DPI, 14 pt is ~68px. 25px gap must snap to x=500!
    assert snapped[0].points[-1][0] == 500
    assert snapped[0].points[-1][1] == 350
    assert snapped[0].points[0] == (100, 350)


def test_snap_endpoints_to_equipment_vertical():
    """Verify vertical pipe approaching vessel top nozzle snaps exactly to perimeter."""
    detections = [{
        "coarse": "equipment",
        "cls": "tank",
        "x1": 200, "y1": 300, "x2": 600, "y2": 700
    }]
    # Vertical line coming from top down to y=275, x=400
    run = PipeRun(points=[(400, 50), (400, 275)], axis="v")
    snapped = snap_endpoints_to_equipment([run], detections, snap_radius_pt=14, dpi=350)

    assert len(snapped) == 1
    assert snapped[0].points[-1][0] == 400
    assert snapped[0].points[-1][1] == 300


def test_snap_endpoints_far_from_equipment_unchanged():
    """Verify runs far from equipment are untouched."""
    detections = [{
        "coarse": "equipment",
        "cls": "tank",
        "x1": 500, "y1": 500, "x2": 900, "y2": 900
    }]
    # Line far away
    run = PipeRun(points=[(10, 10), (100, 10)], axis="h")
    snapped = snap_endpoints_to_equipment([run], detections, snap_radius_pt=14, dpi=350)

    assert snapped[0].points[0] == (10, 10)
    assert snapped[0].points[1] == (100, 10)


def test_skeleton_tracer_min_length_and_color():
    """Verify SkeletonLineTracer default min_length is relaxed to <= 25px."""
    tracer = SkeletonLineTracer()
    assert tracer.min_length_px <= 25

    # Synthesize clean test image with a small branch (30px) and a vessel
    import cv2
    img = np.full((400, 600, 3), 255, dtype=np.uint8)
    # Draw horizontal pipe with 3px thickness (standard P&ID line)
    cv2.line(img, (50, 100), (300, 100), (0, 0, 0), 3)
    # Draw short branch (30px long)
    cv2.line(img, (150, 100), (150, 130), (0, 0, 0), 3)

    detections = [{
        "coarse": "equipment",
        "cls": "vessel",
        "x1": 310, "y1": 50, "x2": 500, "y2": 300
    }]

    runs = tracer.trace(img, dpi=350, detections=detections)
    assert len(runs) >= 1
    # Check that color is #2563EB
    for r in runs:
        assert r.color == "#2563EB"


def test_snap_on_fixture_pdf():
    """Verify tracer and snap execution on representative fixture PDF."""
    try:
        from app.adapters.pdf_renderer import load_drawing_image
    except ImportError:
        from backend.app.adapters.pdf_renderer import load_drawing_image
    pdf_path = os.path.join(
        _ROOT, "backend", "tests", "fixtures", "manual-tracing",
        "CC_AKT-PR-PID-11-1002_3_P&ID Inlet Separation & Produced Water System Inlet Separator.pdf"
    )
    if not os.path.exists(pdf_path):
        pytest.skip("Fixture PDF not found")

    img_bgr = load_drawing_image(pdf_path, dpi=150)
    tracer = SkeletonLineTracer(min_length_px=20)
    # Simulated equipment bounding box for separator
    detections = [{
        "coarse": "equipment",
        "cls": "separator",
        "x1": int(img_bgr.shape[1] * 0.35),
        "y1": int(img_bgr.shape[0] * 0.25),
        "x2": int(img_bgr.shape[1] * 0.65),
        "y2": int(img_bgr.shape[0] * 0.70),
    }]
    runs = tracer.trace(img_bgr, dpi=150, detections=detections)
    assert len(runs) > 20
    for r in runs:
        assert r.color == "#2563EB"

