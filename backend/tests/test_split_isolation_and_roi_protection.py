import os
import sys
import pytest
import numpy as np
import cv2

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr.lines import PipeRun, split_poly_run, stitch_region_runs
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer
from pidcorr.implementations.regex_parser import RegexPipingIDParser


def test_split_run_generates_distinct_unique_ids_dict():
    """Verify that splitting a dictionary-based run assigns distinct, unique IDs to run_a and run_b."""
    runs = [{
        "id": "run-42",
        "points": [[0, 100], [200, 100]],
        "axis": "h",
        "color": "#2563EB",
        "label": "605-6\"-GF-CCB-001",
    }]

    new_runs, _, run_a, run_b, new_idx = split_poly_run(
        runs=runs,
        run_idx=0,
        split_x=80.0,
        split_y=100.0,
    )

    assert len(new_runs) == 2
    assert run_a["id"] == "run-42-a"
    assert run_b["id"] == "run-42-b"
    assert run_a["id"] != run_b["id"]
    assert new_runs[0]["id"] == "run-42-a"
    assert new_runs[1]["id"] == "run-42-b"
    assert run_a["manual"] is True
    assert run_b["manual"] is True
    assert run_a["label"] == "605-6\"-GF-CCB-001"
    assert run_b["label"] == "605-6\"-GF-CCB-001"


def test_split_run_generates_distinct_unique_ids_object():
    """Verify that splitting a PipeRun object assigns distinct, unique IDs to run_a and run_b."""
    pipe = PipeRun(
        id="run-99",
        points=[(50, 0), (50, 300)],
        axis="v",
        color="#2563EB",
        label="14-P2-1802-A1A2-6\"",
    )

    new_runs, _, run_a, run_b, _ = split_poly_run(
        runs=[pipe],
        run_idx=0,
        split_x=50.0,
        split_y=150.0,
    )

    assert len(new_runs) == 2
    assert run_a.id == "run-99-a"
    assert run_b.id == "run-99-b"
    assert run_a.id != run_b.id
    assert run_a.manual is True
    assert run_b.manual is True


def test_stitch_region_runs_propagates_labels():
    """Verify stitch_region_runs propagates label, pid, fluid when merging a new labeled run."""
    existing = [{
        "id": "run-0",
        "points": [[0, 50], [100, 50]],
        "label": "",
        "pid": "",
        "fluid": "",
    }]
    # New run touching existing at (100, 50) and extending to (200, 50) with label
    new = [{
        "id": "rescan-1",
        "points": [[100, 50], [200, 50]],
        "label": "605-4\"-LO-APA-010",
        "pid": "605-4\"-LO-APA-010",
        "fluid": "LO",
    }]

    upd, remaining, consumed = stitch_region_runs(new, existing, bbox=(80, 40, 220, 60), snap_px=18)
    assert len(upd) == 1
    assert "rescan-1" in consumed
    assert upd[0]["label"] == "605-4\"-LO-APA-010"
    assert upd[0]["pid"] == "605-4\"-LO-APA-010"
    assert upd[0]["fluid"] == "LO"
    assert upd[0]["points"] == [[0, 50], [100, 50], [200, 50]]


def test_roi_tracer_does_not_bridge_inline_valves():
    """Verify that in ROI mode (roi=True), SkeletonLineTracer stops at valve boundaries and skips inline valve bridging."""
    # Synthetic image: 120x60
    # Pipe entering from x=10 to x=40 at y=30
    # Valve bbox from x=40 to x=70, y=20 to y=40 (blacked out in binary)
    # Pipe exiting from x=70 to x=100 at y=30
    img = np.zeros((60, 120), dtype=np.uint8)
    # Left pipe
    img[29:32, 10:41] = 255
    # Right pipe
    img[29:32, 70:101] = 255

    valves = [{"coarse": "valve", "x1": 40, "y1": 20, "x2": 70, "y2": 40}]

    tracer = SkeletonLineTracer(min_length_px=6, suppress_floating_stubs=False)
    runs = tracer.trace(
        img_bgr=cv2.cvtColor(img, cv2.COLOR_GRAY2BGR),
        dpi=350,
        detections=valves,
        binary_img=img,
        roi=True,
    )

    # In ROI mode, bridging across the valve must NOT happen.
    # The two pipes must remain separate runs, terminating at the valve port (x<=40 and x>=70).
    assert len(runs) >= 2
    for r in runs:
        pts = r.points if hasattr(r, "points") else r["points"]
        xs = [p[0] for p in pts]
        # No point should penetrate into the valve interior (x in 42..68)
        assert not any(42 < x < 68 for x in xs), f"Run penetrated valve interior: {pts}"


def test_regex_piping_id_parser_in_roi():
    """Verify RegexPipingIDParser parses PetroChina and Pertamina line tags."""
    parser = RegexPipingIDParser()

    res1 = parser.parse("650-2\"-GF-CCB-005")
    assert res1 is not None
    assert res1["fluid"] == "GF"
    assert res1["pclass"] == "CCB"
    assert res1["size"] == "2"

    res2 = parser.parse("14-P2-1802-A1A2-6\"")
    assert res2 is not None
    assert res2["fluid"] == "P2"
    assert res2["pclass"] == "A1A2"
    assert res2["size"] == "6"
