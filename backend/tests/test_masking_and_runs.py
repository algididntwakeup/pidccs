import os
import sys
import numpy as np
import cv2
import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr.lines import (PipeRun, bridge_inline_valve_gaps, suppress_text_artifacts,
                           suppress_floating_stubs, suppress_low_ink_diagonals,
                           suppress_revision_clouds, suppress_diagonal_artifacts)
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer, _graph_segments, _morphological_skeleton


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


@pytest.mark.parametrize("coarse", ["valve", "instrument"])
def test_skeleton_tracer_leaves_detected_symbol_gap(coarse):
    image = np.full((350, 350, 3), 255, dtype=np.uint8)
    cv2.line(image, (20, 175), (330, 175), (0, 0, 0), 3)
    symbol = {"coarse": coarse, "x1": 165, "y1": 160, "x2": 185, "y2": 190}

    runs = SkeletonLineTracer(min_length_px=5).trace(
        image, dpi=350, detections=[symbol], furniture=[]
    )

    assert len(runs) >= 2
    assert all(not (min(p[0] for p in run.points) < 165 and max(p[0] for p in run.points) > 185) for run in runs)


def test_skeleton_graph_preserves_thin_t_branch():
    binary = np.zeros((100, 140), dtype=np.uint8)
    cv2.line(binary, (15, 60), (125, 60), 255, 1)
    cv2.line(binary, (70, 60), (70, 15), 255, 1)

    runs = _graph_segments(_morphological_skeleton(binary), min_length=10)

    assert any(min(p[0] for p in run.points) <= 20 and max(p[0] for p in run.points) >= 120 for run in runs)
    assert any(min(p[1] for p in run.points) <= 20 and max(p[1] for p in run.points) >= 55 for run in runs)


def test_skeleton_tracer_does_not_bridge_raster_valve_gap():
    image = np.full((180, 320, 3), 255, dtype=np.uint8)
    cv2.line(image, (20, 90), (145, 90), (0, 0, 0), 2)
    cv2.line(image, (175, 90), (300, 90), (0, 0, 0), 2)

    runs = SkeletonLineTracer(min_length_px=10).trace(image, dpi=350)

    assert any(max(p[0] for p in run.points) <= 150 for run in runs)
    assert any(min(p[0] for p in run.points) >= 170 for run in runs)
    assert all(not (min(p[0] for p in run.points) < 145 and max(p[0] for p in run.points) > 175) for run in runs)


def test_low_ink_diagonal_keeps_connected_elbow_only():
    image = np.full((350, 350, 3), 255, dtype=np.uint8)
    connected = PipeRun(points=[(20, 20), (100, 100)], axis="d")
    elbow = PipeRun(points=[(101, 100), (150, 100)], axis="h")
    isolated = PipeRun(points=[(200, 200), (300, 300)], axis="d")
    empty = PipeRun(points=[], axis="poly")

    kept = suppress_low_ink_diagonals([empty, connected, elbow, isolated], img_bgr=image)

    assert kept == [connected, elbow]


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
    assert garbage not in out, "isolated short diagonal must be dropped"
    assert near_symbol in out, "diagonal attached to a symbol must survive"
    assert short_horiz in out, "short horizontal pipe must survive"


def test_suppress_floating_stubs_noop_without_detections():
    g = PipeRun(points=[(10, 10), (30, 32)], axis="d")
    out = suppress_floating_stubs([g], detections=None, dpi=350)
    assert g in out, "must be a no-op when detections is empty/None"


# ---------------------------------------------------------------------------
# Phase 1 — 5 golden-ratio geometry rules (anti table/frame leak)
# ---------------------------------------------------------------------------
from pidcorr.lines import is_furniture_geometry, suppress_furniture_geometry


def test_is_furniture_geometry_five_rules():
    """Each of the 5 rules flags furniture; a normal interior pipe is not flagged."""
    W, H = 3300.0, 2340.0

    # 1. frame span > 80% of longest side
    assert is_furniture_geometry(100, 100, 3200, 100, 0.85 * W, W, H) is True
    # 2. title block at the BOTTOM (15.1% from base, origin top-left)
    assert is_furniture_geometry(1000, 0.90 * H, 1200, 0.90 * H, 200, W, H) is True
    # 3. top border (2.6%)
    assert is_furniture_geometry(1000, 10, 1200, 10, 200, W, H) is True
    # 4. left tick grid (2.4%)
    assert is_furniture_geometry(40, 1000, 40, 1200, 200, W, H) is True
    # 5. right tick grid (2.3%)
    assert is_furniture_geometry(0.99 * W, 1000, 0.99 * W, 1200, 200, W, H) is True
    # a normal interior pipe is NOT furniture
    assert is_furniture_geometry(1000, 1000, 1400, 1000, 400, W, H) is False


def test_suppress_furniture_geometry_drops_border_keeps_pipe():
    W, H = 3300, 2340
    border = PipeRun(points=[(100, 1200), (100, 2000)], axis="v")      # left tick zone
    title = PipeRun(points=[(1500, 2200), (1700, 2200)], axis="h")     # short title-block row
    pipe = PipeRun(points=[(1000, 1200), (1600, 1200)], axis="h")      # interior
    out = suppress_furniture_geometry([border, title, pipe], page_wh=(W, H), dpi=350)
    assert border not in out, "left-edge furniture must be dropped"
    assert title not in out, "short title-block band run must be dropped"
    assert pipe in out, "interior pipe must survive"


def test_suppress_furniture_geometry_span_always_dropped():
    """Rule #1 (page frame) is unconditional — even a detection touch cannot save it."""
    W, H = 3300, 2340
    frame = PipeRun(points=[(50, 50), (3250, 50)], axis="h")           # ~97% span
    det = [{"coarse": "equipment", "x1": 0, "y1": 0, "x2": 3300, "y2": 100}]
    out = suppress_furniture_geometry([frame], page_wh=(W, H), detections=det, dpi=350)
    assert frame not in out, "page-spanning frame must always be dropped"


def test_suppress_furniture_geometry_guards_real_pipe():
    """Band rule must NOT eat a real pipe that crosses equipment or carries a header length."""
    W, H = 3300, 2340
    # long header sitting inside the bottom band (length > 10% of max side = 330px)
    header = PipeRun(points=[(300, 0.90 * H), (1200, 0.90 * H)], axis="h")
    # short stub crossing an equipment box in the bottom band
    stub = PipeRun(points=[(1200, 0.88 * H), (1400, 0.88 * H)], axis="h")
    det = [{"coarse": "equipment", "x1": 1300, "y1": 0.88 * H - 60,
            "x2": 1500, "y2": 0.88 * H + 60}]
    out = suppress_furniture_geometry([header, stub], page_wh=(W, H), detections=det, dpi=350)
    assert header in out, "long header must survive the band rule"
    assert stub in out, "pipe crossing equipment must survive the band rule"


def test_suppress_furniture_geometry_label_protects_pipe():
    """A piping-ID label next to a run protects it; a label inside furniture does not."""
    W, H = 3300, 2340
    pipe = PipeRun(points=[(1000, 0.90 * H), (1400, 0.90 * H)], axis="h")
    lab_outside = [(1000, 0.90 * H - 40, 1400, 0.90 * H - 8)]     # above the run, outside furniture
    lab_inside = [(1000, 0.90 * H - 40, 1400, 0.90 * H - 8)]      # same box but inside furniture
    furniture = [(900, 0.90 * H - 60, 1500, 0.95 * H)]
    out_ok = suppress_furniture_geometry([pipe], page_wh=(W, H), label_boxes=lab_outside, dpi=350)
    assert pipe in out_ok, "run carrying a real piping-ID label must survive"
    out_no = suppress_furniture_geometry(
        [pipe], page_wh=(W, H), furniture=furniture, label_boxes=lab_inside, dpi=350)
    assert pipe not in out_no, "label inside furniture (table cell) must not protect"


def test_suppress_furniture_geometry_drops_furniture_contained_run():
    """A run fully inside a detected furniture box (+pad) is a table grid line -> dropped."""
    W, H = 3300, 2340
    grid = PipeRun(points=[(2700, 1300), (2700, 1500)], axis="v")   # inside title block box
    furniture = [(2600, 1200, 3250, 2300)]
    out = suppress_furniture_geometry([grid], page_wh=(W, H), furniture=furniture, dpi=350)
    assert grid not in out, "table grid line inside furniture must be dropped"
def test_skeleton_tracer_preserves_gradual_arc_through_geometric_filters():
    image = np.full((350, 350, 3), 255, dtype=np.uint8)
    centerline = [(x, int(60 + 0.001 * (x - 30) ** 2)) for x in range(30, 271, 2)]
    cv2.polylines(image, [np.asarray(centerline, dtype=np.int32)], False, (0, 0, 0), 2)
    runs = SkeletonLineTracer(min_length_px=5, suppress_text_artifacts=False,
                              suppress_floating_stubs=False).trace(
                                  image, dpi=350, detections=[], furniture=[])
    arcs = [run for run in suppress_diagonal_artifacts(
        suppress_revision_clouds(runs), page_wh=(1200, 1200)) if len(run.points) >= 4]
    assert arcs
    for point in centerline:
        assert min(_distance_to_run(point, run.points) for run in arcs) <= 4.0


def _distance_to_run(point, points):
    px, py = point
    distances = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx, dy = x1 - x0, y1 - y0
        squared = dx * dx + dy * dy
        ratio = 0.0 if not squared else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / squared))
        distances.append(np.hypot(px - x0 - ratio * dx, py - y0 - ratio * dy))
    return min(distances)


def _trace_symbol_case(symbol):
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    cv2.line(image, (80, 500), (920, 500), (0, 0, 0), 2)
    if symbol == "v":
        cv2.line(image, (460, 500), (500, 460), (0, 0, 0), 2)
        cv2.line(image, (500, 460), (540, 500), (0, 0, 0), 2)
        symbol_box = (455, 455, 545, 505)
    elif symbol == "bar":
        cv2.line(image, (500, 460), (500, 540), (0, 0, 0), 2)
        symbol_box = (490, 455, 510, 545)
    else:
        cv2.rectangle(image, (490, 470), (510, 530), (0, 0, 0), -1)
        symbol_box = (490, 470, 510, 530)
    runs = SkeletonLineTracer(min_length_px=5, suppress_text_artifacts=False,
                              suppress_floating_stubs=False).trace(
                                  image, dpi=350, detections=[], furniture=[])
    return runs, symbol_box


@pytest.mark.parametrize("symbol", ["v", "bar", "thick"])
def test_raster_inline_symbols_leave_two_sides_and_no_symbol_interior(symbol):
    runs, (x1, y1, x2, y2) = _trace_symbol_case(symbol)
    assert any(max(p[0] for p in run.points) <= x1 for run in runs)
    assert any(min(p[0] for p in run.points) >= x2 for run in runs)
    assert all(not any(x1 <= x <= x2 and y1 <= y <= y2 for x, y in run.points)
               for run in runs)


@pytest.mark.parametrize("crossing", [False, True])
def test_raster_t_and_full_crossover_keep_through_and_branch(crossing):
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    cv2.line(image, (200, 500), (800, 500), (0, 0, 0), 2)
    cv2.line(image, (500, 250 if crossing else 350), (500, 750 if crossing else 500), (0, 0, 0), 2)
    runs = SkeletonLineTracer(min_length_px=5, suppress_text_artifacts=False,
                              suppress_floating_stubs=False).trace(
                                  image, dpi=72, detections=[], furniture=[])
    assert any(min(p[0] for p in run.points) <= 205 and max(p[0] for p in run.points) >= 795
               for run in runs)
    assert any(min(p[1] for p in run.points) <= (255 if crossing else 355)
               and max(p[1] for p in run.points) >= (745 if crossing else 495)
               for run in runs if run.axis == "v")
def test_smooth_open_curve_survives_both_geometric_post_filters():
    curve = PipeRun(
        points=[(100, 500), (200, 470), (300, 450), (400, 445),
                (500, 450), (600, 470), (700, 500)],
        axis="poly",
    )
    loop = PipeRun(points=[(100, 100), (200, 50), (250, 150), (200, 220), (100, 100)], axis="poly")
    long_diagonal = PipeRun(points=[(100, 200), (600, 700)], axis="d")
    assert curve in suppress_revision_clouds([curve, loop])
    assert loop not in suppress_revision_clouds([curve, loop])
    filtered = suppress_diagonal_artifacts([curve, long_diagonal], page_wh=(1200, 1200))
    assert curve in filtered
    assert long_diagonal not in filtered
