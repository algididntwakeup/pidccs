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


def test_suppress_text_artifacts_tighter_thresholds():
    """New defaults (side<=45px, area<=600px, AR<3.5 @350dpi) drop more glyph garbage
    while still preserving thin, elongated pipe stubs."""
    binary = np.zeros((400, 400), dtype=np.uint8)
    # Long horizontal pipe -> kept
    binary[50:53, 20:380] = 255
    # Thin vertical stub (w=3, h=40 -> AR 13) -> kept (elongated)
    binary[120:160, 100:103] = 255
    # A wider compact glyph "8" 30x30 (area 900 minus hole ~ 700) at 350dpi...
    binary[250:280, 60:90] = 255
    # small fraction glyph 20x24 (AR 1.2, area 480) -> removed
    binary[250:274, 150:170] = 255

    out = suppress_text_artifacts(binary, dpi=350)
    assert out[50:53, 20:380].sum() > 0, "long pipe must survive"
    assert out[120:160, 100:103].sum() > 0, "thin elongated stub must survive"
    assert out[250:274, 150:170].sum() == 0, "compact glyph must be removed"


def test_skeleton_tracer_accepts_precomputed_binary():
    """trace(binary_img=...) must bypass adaptive thresholding and trace the supplied ink."""
    img = np.full((300, 500, 3), 255, dtype=np.uint8)
    # Draw a black pipe on a WHITE background (so adaptive threshold would be the only
    # source of ink). Provide the pre-computed binary explicitly.
    img[150:153, 40:460] = 0
    binary = np.zeros((300, 500), dtype=np.uint8)
    binary[150:153, 40:460] = 255  # BINARY_INV: ink = 255

    tracer = SkeletonLineTracer(min_length_px=6)
    runs = tracer.trace(img, dpi=350, detections=[], binary_img=binary, roi=True)
    assert runs, "pre-computed binary path must still produce a pipe run"
    found = any(abs(r.points[0][1] - 151) <= 4 for r in runs)
    assert found, "the horizontal pipe at y=151 should be traced"


def test_roi_mode_bridges_ocr_masking_holes():
    """In ROI mode a small MORPH_CLOSE reconnects a pipe broken by a 2px gap."""
    img = np.full((200, 400, 3), 255, dtype=np.uint8)
    img[100:103, 40:200] = 0
    img[100:103, 202:360] = 0  # 2px gap (as if OCR masking punched through)
    binary = np.zeros((200, 400), dtype=np.uint8)
    binary[100:103, 40:200] = 255
    binary[100:103, 202:360] = 255

    tracer = SkeletonLineTracer(min_length_px=4)
    runs = tracer.trace(img, dpi=350, detections=[], binary_img=binary, roi=True)
    # The bridged result should contain a run that spans most of the width.
    wide = [r for r in runs if (max(p[0] for p in r.points) - min(p[0] for p in r.points)) > 200]
    assert wide, "MORPH_CLOSE in ROI mode should bridge the small gap"


# ---------------------------------------------------------------------------
# Bagian 1: T-junction snap, valve gap bridging, border-frame guard band
# ---------------------------------------------------------------------------

from pidcorr.lines import (
    snap_t_junctions,
    suppress_drawing_margins,
    stitch_region_runs,
)

def test_snap_t_junctions_pulls_dead_end_onto_perpendicular_body():
    """A short branch ending 10px short of a perpendicular header is snapped onto it."""
    header = PipeRun(points=[(0, 100), (300, 100)], axis="h")
    branch = PipeRun(points=[(150, 90), (150, 40)], axis="v")  # dead-end at y=90, 10px above header
    out = snap_t_junctions([header, branch], near_px=16)
    b = next(r for r in out if r.axis == "v")
    # The lower endpoint (nearest the header) must now touch y=100.
    assert min(p[1] for p in b.points) == 100 or max(p[1] for p in b.points) == 100, \
        "branch endpoint should be projected onto the header body"

def test_snap_t_junctions_ignores_collinear_continuation():
    """A collinear butt-joint gap must NOT be snapped (approach is not perpendicular)."""
    a = PipeRun(points=[(0, 100), (100, 100)], axis="h")
    b = PipeRun(points=[(110, 100), (200, 100)], axis="h")
    out = snap_t_junctions([a, b], near_px=16)
    pts_b = next(r for r in out if r.points[0][0] == 110).points
    assert (110, 100) in [(int(p[0]), int(p[1])) for p in pts_b], "collinear gap must be left alone"

def test_bridge_inline_valve_gaps_containment_without_angle():
    """Two facing pipe ends inside the SAME valve bbox are bridged regardless of angle."""
    r1 = PipeRun(points=[(50, 100), (100, 100)], axis="h")
    r2 = PipeRun(points=[(110, 88), (170, 88)], axis="h")  # offset vertically (diagonal-ish gap)
    valves = [{"coarse": "valve", "x1": 95, "y1": 80, "x2": 115, "y2": 130}]
    bridged = bridge_inline_valve_gaps([r1, r2], detections=valves)
    assert len(bridged) == 1, "endpoints inside the same valve bbox must merge"

def test_suppress_drawing_margins_guard_band():
    """A border segment fully inside the 15px perimeter band is dropped."""
    page = (2000, 1500)
    frame = PipeRun(points=[(5, 8), (1990, 8)], axis="h")       # 8px from top -> inside guard band
    real = PipeRun(points=[(500, 700), (900, 700)], axis="h")  # interior pipe -> kept
    out = suppress_drawing_margins([frame, real], page_wh=page)
    assert frame not in out, "border frame inside guard band must be suppressed"
    assert real in out, "interior pipe must survive"

def test_stitch_region_runs_one_to_one_extends_existing():
    """A new ROI path touching exactly one existing end extends it (no new run)."""
    existing = [{"id": "runA", "points": [[0, 100], [100, 100]]}]
    new = [{"id": "rescan-1", "points": [[100, 100], [200, 100]]}]
    upd, remaining, consumed = stitch_region_runs(new, existing, bbox=(95, 95, 205, 105))
    assert not remaining, "the new run should be absorbed"
    assert consumed == ["rescan-1"], "the new run id should be marked consumed"
    assert len(upd) == 1
    assert upd[0]["points"][-1] == [200, 100], "existing run should be extended"

def test_stitch_region_runs_one_to_two_bridges_and_drops():
    """A new path bridging two pipe ends merges them and drops the second run."""
    existing = [
        {"id": "runM", "points": [[0, 100], [100, 100]]},
        {"id": "runF", "points": [[200, 100], [300, 100]]},
    ]
    new = [{"id": "rescan-x", "points": [[100, 100], [200, 100]]}]
    upd, remaining, consumed = stitch_region_runs(new, existing, bbox=(95, 95, 205, 105))
    assert not remaining, "bridged run must be absorbed"
    assert len(upd) == 1, "the two existing runs must collapse into one"
    pts = upd[0]["points"]
    assert pts[0] == [0, 100] and pts[-1] == [300, 100], "merged polyline spans M..F"
    assert set(consumed) == {"rescan-x", "runF"}, "both the new and the merged-away F are consumed"

def test_stitch_region_runs_ambiguous_keeps_independent_but_snaps():
    """With 3+ touching ends (T-junction) the new run is kept but endpoints snapped."""
    existing = [
        {"id": "runA", "points": [[0, 100], [100, 100]]},
        {"id": "runB", "points": [[200, 100], [300, 100]]},
        {"id": "runC", "points": [[100, 0], [100, 100]]},
    ]
    new = [{"id": "rescan-amb", "points": [[105, 100], [195, 100]]}]
    upd, remaining, consumed = stitch_region_runs(new, existing, bbox=(95, 95, 205, 105))
    assert len(remaining) == 1, "ambiguous branch must remain independent"
    assert not consumed
    assert len(upd) == 3, "no existing run should be dropped"
