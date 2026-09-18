import os
import sys
import pytest
import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr.lines import PipeRun, bridge_inline_valve_gaps
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer


def test_pipe_run_schema_fields():
    run = PipeRun(
        points=[(10, 10), (100, 10)],
        axis="h",
        color="#2563EB",
        id="run-42",
        label='605-6"-GR-BDA-029-H50',
        manual=True,
    )
    assert run.id == "run-42"
    assert run.label == '605-6"-GR-BDA-029-H50'
    assert run.manual is True
    assert run.color == "#2563EB"
    assert run["id"] == "run-42"
    assert run["label"] == '605-6"-GR-BDA-029-H50'
    assert run["manual"] is True
    assert run["color"] == "#2563EB"


def test_bridge_inline_valve_gaps():
    r1 = PipeRun(points=[(50, 100), (100, 100)], axis="h", color="#2563EB")
    r2 = PipeRun(points=[(140, 100), (200, 100)], axis="h", color="#2563EB")
    valves = [{"coarse": "valve", "x1": 100, "y1": 85, "x2": 140, "y2": 115}]

    bridged = bridge_inline_valve_gaps([r1, r2], detections=valves, max_gap_px=75)
    assert len(bridged) == 1
    pts = bridged[0].points if hasattr(bridged[0], "points") else bridged[0]["points"]
    assert pts[0] == (50, 100)
    assert pts[-1] == (200, 100)


def test_skeleton_tracer_dilated_text_and_equipment_masking():
    tracer = SkeletonLineTracer(min_length_px=15)
    img = np.ones((500, 500, 3), dtype=np.uint8) * 255
    img[249:252, 50:450] = 0
    img[349:351, 100:180] = 0
    img[100:102, 320:400] = 0

    tokens = [{"x1": 100, "y1": 340, "x2": 180, "y2": 352, "text": "BY INSTR."}]
    detections = [{"coarse": "equipment", "x1": 300, "y1": 50, "x2": 420, "y2": 200, "cls": "vessel"}]

    runs = tracer.trace(img_bgr=img, dpi=350, detections=detections, tokens=tokens)

    pipe_found = any(abs(r.points[0][1] - 250) <= 3 and abs(r.points[-1][1] - 250) <= 3 for r in runs)
    assert pipe_found, "Main pipe should be traced"

    text_underline_found = any(abs(r.points[0][1] - 350) <= 3 for r in runs)
    assert not text_underline_found, "Text underline should be blacked out and not traced"

    baffle_found = any(
        any(300 <= p[0] <= 420 and 50 <= p[1] <= 200 for p in r.points)
        for r in runs
    )
    assert not baffle_found, "Internal vessel baffles must be suppressed"
