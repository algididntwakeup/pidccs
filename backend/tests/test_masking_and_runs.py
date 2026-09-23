import os
import sys
import pytest
import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr.lines import PipeRun, bridge_inline_valve_gaps, suppress_text_artifacts, suppress_floating_stubs
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


def test_suppress_text_artifacts_removes_glyphs_keeps_pipes():
    """Text glyph islands (compact, AR<4) get blacked out; elongated pipe stubs survive."""
    binary = np.zeros((300, 400), dtype=np.uint8)
    # A real long horizontal pipe (elongated, AR >> 4) -> must survive
    binary[100:103, 20:380] = 255
    # A real thin vertical stub (w=2, h=14 -> AR 7) -> must survive
    binary[150:164, 200:202] = 255
    # A text-glyph island "0": 12x16, AR<4 -> must be removed
    binary[200:216, 60:72] = 255
    # another glyph "3/4" fragment
    binary[200:214, 120:130] = 255

    out = suppress_text_artifacts(binary, dpi=200)
    assert out[100:103, 20:380].sum() > 0, "long pipe must be preserved"
    assert out[150:164, 200:202].sum() > 0, "thin vertical stub must be preserved"
    assert out[200:216, 60:72].sum() == 0, "glyph island must be blacked out"
    assert out[200:214, 120:130].sum() == 0, "size-fraction fragment must be blacked out"
    assert set(np.unique(out)).issubset({0, 255})


def test_suppress_text_artifacts_protect_boxes():
    """Islands inside protected symbol boxes are NOT removed."""
    binary = np.zeros((200, 200), dtype=np.uint8)
    binary[50:66, 50:62] = 255  # glyph-sized but inside a protected box
    out = suppress_text_artifacts(
        binary, dpi=200, protect_boxes=[(40, 40, 80, 80)]
    )
    assert out[50:66, 50:62].sum() > 0, "island inside protected box must survive"


def test_suppress_floating_stubs_removes_isolated_diagonal():
    """Short isolated diagonal strokes are dropped; symbols protect real branches."""
    garbage = PipeRun(points=[(10, 10), (30, 32)], axis="d")
    near_symbol = PipeRun(points=[(100, 100), (118, 118)], axis="d")
    short_horiz = PipeRun(points=[(200, 50), (220, 50)], axis="h")
    detections = [{"coarse": "valve", "x1": 95, "y1": 95, "x2": 130, "y2": 130}]

    out = suppress_floating_stubs(
        [garbage, near_symbol, short_horiz], detections=detections, dpi=350, max_len_px=40
    )
    kinds = [r.axis for r in out]
    assert garbage not in out, "isolated short diagonal must be dropped"
    assert near_symbol in out, "diagonal attached to a symbol must survive"
    assert short_horiz in out, "short horizontal pipe must survive"


def test_suppress_floating_stubs_noop_without_detections():
    g = PipeRun(points=[(10, 10), (30, 32)], axis="d")
    out = suppress_floating_stubs([g], detections=None, dpi=350)
    assert g in out, "must be a no-op when detections is empty/None"
