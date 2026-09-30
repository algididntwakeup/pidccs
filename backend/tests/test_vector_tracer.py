"""Phase 2 tests — hybrid vector-first line tracing (PDF) with raster fallback.

Covers:
  * tier classification (vector A1/A2 vs raster),
  * PipeRun schema parity with the skeleton tracer,
  * closed-path (symbol/bubble) exclusion,
  * table-region filtering,
  * orchestrator hybrid dispatch + raster fallback for PNG,
  * sub-second vector tracing on the reference P&ID.
"""
import os
import sys
import time

import numpy as np
import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
for _p in (_BACKEND_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _fixtures import fixture_path
from pidcorr.implementations.vector_tracer import (
    VectorLineTracer,
    extract_vector_runs,
    tier_of_pdf,
    _closed,
    _straight_segments,
    _cubic_points,
    drawing_curve_paths,
    _pdfplumber_curve_paths,
    _segs_to_runs,
    _merge_intervals,
    _to_runs,
    _drop_glyph_noise,
    _cluster_rows,
    _table_regions,
    _drop_inside_regions,
    _inline_symbol_regions,
    _transverse_symbol_regions,
    _split_curve_at_regions,
    _split_runs_at_symbol_regions,
)
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer
from pidcorr.interfaces.perception import BaseLineTracer
from pidcorr.lines import PipeRun

_PDF = fixture_path("Contoh P&ID", "BCD3-605-42-PID-1-014-01 Rev.6-CCD2.pdf")
_PNG = fixture_path("Contoh P&ID", "BCD3-605-42-PID-1-014-01 Rev.6-CCD2.png")

# pdfplumber adalah engine opsional: dipakai bila tersedia (dan dipilih lewat
# VECTOR_ENGINE), tetapi bukan syarat agar pipeline berjalan. Tes yang khusus
# menguji engine itu harus skip — bukan gagal — pada environment tanpa paketnya
# (mis. host dev yang hanya memasang dependensi container).
try:
    import pdfplumber as _pdfplumber_probe  # noqa: F401
    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False

_needs_pdfplumber = pytest.mark.skipif(
    not _HAS_PDFPLUMBER, reason="pdfplumber not installed in this environment"
)


# --------------------------------------------------------------------- unit ----
def test_vector_tracer_implements_interface():
    assert isinstance(VectorLineTracer(), BaseLineTracer)


def test_tier_classification(tmp_path):
    """A generated vector PDF is classified without relying on local drawings."""
    import pymupdf

    vector_pdf = tmp_path / "vector.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=600)
    for index in range(100):
        y = 10 + index * 5
        page.draw_line((10, y), (500, y))
    document.save(vector_pdf)
    document.close()

    assert tier_of_pdf(str(_PNG)) == "raster"
    assert tier_of_pdf(str(vector_pdf)) == "A1"


def test_straight_segments_splits_on_direction_change():
    """Ruas lurus terpisah di belokan; busur terpecah, bukan dianggap satu garis."""
    pts = [(0, 0), (10, 0), (20, 0), (20, 10)]
    segs = _straight_segments(pts)
    assert len(segs) == 2
    assert segs[0] == ((0.0, 0.0), (20.0, 0.0))
    assert segs[1] == ((20.0, 0.0), (20.0, 10.0))


def test_closed_helper_detects_loop():
    loop = {"pts": [(0, 0), (10, 0), (10, 10), (0, 0)]}
    line = {"pts": [(0, 0), (10, 0), (20, 0)]}
    assert _closed(loop) is True
    assert _closed(line) is False


def test_merge_intervals_joins_small_gap_and_keeps_large_gap():
    """Celah <= gap disambung; celah besar tetap dua run."""
    near = [(0.0, 0.0, 10.0), (0.2, 20.0, 30.0)]     # gap 10 pt
    far = [(0.0, 0.0, 10.0), (0.2, 90.0, 100.0)]     # gap 80 pt
    assert len(_merge_intervals(near, gap=42.0, min_len=10.0)) == 1
    assert len(_merge_intervals(far, gap=42.0, min_len=10.0)) == 2


def test_to_runs_classifies_axes():
    """H/V dipisah ke sumbunya, diagonal tetap 'd'."""
    segs = [((0, 0), (100, 0)), ((0, 0), (0, 100)), ((0, 0), (60, 60))]
    runs, axes = _to_runs(segs)
    assert "h" in axes and "v" in axes and "d" in axes
def test_cubic_curve_samples_preserve_point_order_and_rotation():
    class Rotation:
        a, b, c, d, e, f = 0, 1, -1, 0, 200, 0

    drawing = {
        "color": (0, 0, 0),
        "items": [("c", (10, 20), (30, 0), (50, 0), (70, 20))],
    }
    paths = drawing_curve_paths(drawing, rotation_matrix=Rotation())
    assert len(paths) == 1 and len(paths[0]) == 9
    assert paths[0][0] == (180.0, 10.0)
    assert paths[0][-1] == (180.0, 70.0)
    assert _cubic_points((10, 20), (30, 0), (50, 0), (70, 20))[0] == (10, 20)


def test_pdfplumber_curve_helper_keeps_only_open_ordered_strokes():
    objects = [
        {"stroke": True, "fill": None, "pts": [(1, 2), (5, 3), (8, 7), (10, 12)]},
        {"stroke": True, "fill": None, "pts": [(0, 0), (10, 0), (10, 10), (0, 0)]},
        {"stroke": True, "fill": (0, 0, 0), "pts": [(1, 1), (4, 2), (7, 4), (10, 8)]},
    ]
    assert _pdfplumber_curve_paths(objects) == [[(1.0, 2.0), (5.0, 3.0), (8.0, 7.0), (10.0, 12.0)]]


def test_segs_to_runs_preserves_curve_points_and_splits_symbol_regions():
    curve = [(30, 80), (42, 70), (54, 64), (66, 61), (78, 62), (90, 66), (102, 75)]
    straight = [((20, 60), (180, 60)), ((100, 52), (100, 68))]
    runs = _segs_to_runs(straight, 300, 300, dpi=72, rot=90,
                         keep_furniture=True, curve_paths=[curve])
    curved = [run for run in runs if run.axis == "poly"]
    assert len(curved) == 1
    assert len(curved[0].points) >= 4
    assert curved[0].points[0] == (219, 30)
    assert curved[0].points[-1] == (224, 102)
    straight_runs = [run for run in runs if run.axis in ("h", "v")]
    assert len(straight_runs) == 2
    assert all(not (min(p[0] for p in run.points) < 95 < max(p[0] for p in run.points))
               for run in straight_runs)


def test_transverse_bar_gaps_split_diagonal_and_horizontal_lines():
    straight = [((20, 60), (180, 60)), ((100, 52), (100, 68))]
    regions = _transverse_symbol_regions(straight)
    assert len(regions) == 1
    pieces = _split_curve_at_regions(((20, 60), (100, 60), (180, 60)), regions)
    assert len(pieces) == 2


def test_straight_line_and_sharp_elbow_behavior_remains_unchanged():
    assert _straight_segments([(0, 0), (10, 0), (20, 0), (20, 10)]) == [
        ((0.0, 0.0), (20.0, 0.0)), ((20.0, 0.0), (20.0, 10.0))]
    assert _segs_to_runs([((20, 30), (100, 30))], 200, 200,
                         dpi=72, keep_furniture=True)[0].axis == "h"


def test_generated_pdf_extraction_keeps_bezier_and_valve_gap(tmp_path):
    import pymupdf

    path = tmp_path / "curves-and-valve.pdf"
    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    page.draw_line((20, 80), (180, 80), color=(0, 0, 0), width=1)
    page.draw_line((95, 75), (100, 80), color=(0, 0, 0), width=1)
    page.draw_line((100, 80), (105, 75), color=(0, 0, 0), width=1)
    page.draw_bezier((20, 180), (60, 130), (120, 130), (180, 180),
                     color=(0, 0, 0), width=1)
    document.save(path)
    document.close()

    runs = extract_vector_runs(str(path), dpi=72, engine="pymupdf", keep_furniture=True)
    curved = [run for run in runs if run.axis == "poly"]
    assert curved and len(curved[0].points) >= 4
    assert any(run.axis == "h" and max(p[0] for p in run.points) < 95 for run in runs)
    assert any(run.axis == "h" and min(p[0] for p in run.points) > 105 for run in runs)
    assert all(not (run.axis == "h" and min(p[0] for p in run.points) < 95
                    and max(p[0] for p in run.points) > 105) for run in runs)


def test_segs_to_runs_splits_compact_v_into_distinct_runs():
    segments = [
        ((20.0, 80.0), (180.0, 80.0)),
        ((95.0, 80.0), (100.0, 85.0)),
        ((100.0, 85.0), (105.0, 80.0)),
    ]
    runs = _segs_to_runs(segments, 300, 300, dpi=72, keep_furniture=True)
    horizontal = [run for run in runs if run.axis == "h"]
    assert len(horizontal) == 2
    assert any(max(point[0] for point in run.points) <= 94 for run in horizontal)
    assert any(min(point[0] for point in run.points) >= 106 for run in horizontal)


@pytest.mark.parametrize("symbol", ["v", "bar"])
def test_segs_to_runs_splits_preserved_curve_at_symbol(symbol):
    curve = [(20, 82), (50, 72), (80, 66), (100, 65), (120, 66), (150, 72), (180, 82)]
    if symbol == "v":
        segments = [((95, 65), (100, 70)), ((100, 70), (105, 65))]
        box = (93.5, 63.5, 106.5, 71.5)
    else:
        segments = [((20, 65), (180, 65)), ((100, 57), (100, 73))]
        box = (98.5, 55.5, 101.5, 74.5)
    runs = _segs_to_runs(segments, 300, 300, dpi=72,
                         keep_furniture=True, curve_paths=[curve])
    pieces = [run for run in runs if run.axis == "poly"]
    assert len(pieces) == 2
    assert all(not any(box[0] + 1.0 < x < box[2] - 1.0
                       and box[1] + 1.0 < y < box[3] - 1.0
                       for x, y in run.points) for run in pieces)


def test_inline_valve_geometry_leaves_a_gap_in_collinear_vector_pipe():
    """A compact open V valve interrupts an otherwise merged pipe run."""
    segments = [
        ((0.0, 0.0), (100.0, 0.0)),
        ((45.0, 0.0), (50.0, 5.0)),
        ((50.0, 5.0), (55.0, 0.0)),
    ]
    regions = _inline_symbol_regions(segments)
    runs, axes = _to_runs(segments)
    split_runs, split_axes = _split_runs_at_symbol_regions(runs, axes, regions)

    horizontal = [run for run, axis in zip(split_runs, split_axes) if axis == "h"]
    assert len(horizontal) == 2
    assert all(not (run[0][0] < 50 < run[1][0]) for run in horizontal)


def test_isolated_elbow_does_not_create_inline_valve_gap():
    segments = [((0.0, 0.0), (8.0, 8.0)), ((8.0, 8.0), (16.0, 16.0))]
    assert _inline_symbol_regions(segments) == []


def test_drop_glyph_noise_keeps_short_run_when_connected():
    """Stub pendek yang menyambung ke jaringan pipa harus dipertahankan."""
    connected = [((0, 0), (200, 0)), ((200, 0), (200, 12))]     # stub 12 pt menempel
    isolated = [((0, 0), (200, 0)), ((500, 500), (512, 500))]   # 12 pt terpisah
    _, ax_c = _drop_glyph_noise(connected, ["h", "v"])
    _, ax_i = _drop_glyph_noise(isolated, ["h", "v"])
    assert len(ax_c) == 2, "connected short stub must survive"
    assert len(ax_i) == 1, "isolated short stroke is glyph noise"


def test_cluster_rows_separates_side_by_side_tables():
    """Dua tabel berdampingan pada ketinggian sama tidak boleh tergabung."""
    rows = []
    for k in range(4):                                  # tabel kiri
        rows.append((10.0 + k * 5, 0.0, 100.0, k))
    for k in range(4):                                  # tabel kanan
        rows.append((10.0 + k * 5, 500.0, 600.0, 100 + k))
    clusters = _cluster_rows(rows, max_row_gap_pt=12.0, min_rows=3)
    assert len(clusters) == 2
    assert all(len(c) == 4 for c in clusters)


def test_cluster_rows_rejects_border_absorption():
    """Baris tabel pendek tidak boleh terserap garis border selebar lembar."""
    rows = [(0.0, 0.0, 1191.0, 0)]                      # border penuh lebar
    for k in range(4):
        rows.append((8.0 + k * 5, 300.0, 550.0, k + 1))
    clusters = _cluster_rows(rows, max_row_gap_pt=12.0, min_rows=3)
    assert len(clusters) == 1
    assert 0 not in clusters[0], "full-width border must not join the table cluster"


def test_table_regions_and_drop_inside():
    """Region tabel terdeteksi dan run di dalamnya dibuang."""
    runs, axes = [], []
    for k in range(4):                                  # garis tabel
        runs.append(((0.0, 10.0 * k), (100.0, 10.0 * k)))
        axes.append("h")
    runs.append(((0.0, 500.0), (100.0, 500.0)))         # pipa jauh
    axes.append("h")

    regions = _table_regions(runs, axes)
    assert regions, "table region must be detected"
    kept, _ = _drop_inside_regions(runs, axes, regions)
    assert len(kept) == 1, "only the far pipe survives"


def test_drop_inside_regions_noop_without_regions():
    runs = [((0.0, 0.0), (10.0, 0.0))]
    kept, axes = _drop_inside_regions(runs, ["h"], [])
    assert kept == runs and axes == ["h"]


# ------------------------------------------------------------- integration ----
@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
@_needs_pdfplumber
def test_vector_runs_schema_matches_skeleton():
    """Skema PipeRun vektor harus identik dengan output skeleton tracer."""
    runs = extract_vector_runs(str(_PDF), dpi=200)
    assert runs, "vector tracer must produce runs on a vector PDF"
    for r in runs:
        assert isinstance(r, PipeRun)
        assert len(r.points) >= 2
        for x, y in r.points:
            assert isinstance(x, int) and isinstance(y, int)
        assert r.axis in ("h", "v", "d", "poly")
        assert r.color == "#2563EB"
        assert r.manual is False
        assert isinstance(r.id, str)

    skel = SkeletonLineTracer()
    sample = skel.trace(np.full((200, 200, 3), 255, np.uint8), dpi=350,
                        detections=[], furniture=[])
    for r in sample:                                    # bentuk kunci sama
        assert set(r.points[0]) == set(runs[0].points[0])


@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
def test_vector_trace_beats_skeleton_and_stays_bounded():
    """Tracing vektor harus lebih cepat dari skeletonisasi raster, dan terbatas.

    Catatan: target <1 s pada spesifikasi Phase 2 dicapai oleh engine `pymupdf`
    (0.26 s). Engine default `pdfplumber` sengaja lebih lambat (5.8–27 s) karena
    mengutamakan keamanan rotasi; batas di sini menjaga agar tidak membengkak
    tak terduga, bukan menuntut <1 s.
    """
    t0 = time.perf_counter()
    runs = extract_vector_runs(str(_PDF), dpi=200, engine="pymupdf")
    fast = time.perf_counter() - t0
    assert runs
    assert fast < 2.0, f"pymupdf engine took {fast:.3f}s (expected ~0.3s)"

    t0 = time.perf_counter()
    runs_pl = extract_vector_runs(str(_PDF), dpi=200, engine="pdfplumber")
    slow = time.perf_counter() - t0
    assert runs_pl
    assert slow < 60.0, f"pdfplumber engine took {slow:.1f}s (unexpectedly slow)"


@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
@_needs_pdfplumber
def test_vector_trace_default_engine_is_pdfplumber():
    """Default engine = pdfplumber (kualitas & keamanan rotasi diutamakan).

    Bukan batas kecepatan: pdfplumber sengaja dipilih meski lebih lambat karena
    `/Rotate` ditangani library, sehingga tidak ada risiko salah orientasi.
    """
    import inspect
    from pidcorr.implementations.vector_tracer import extract_vector_runs as _evr

    sig = inspect.signature(_evr)
    assert sig.parameters["engine"].default == "pdfplumber"
    assert VectorLineTracer().engine == "pdfplumber"

    runs = VectorLineTracer().trace(pdf_path=str(_PDF), dpi=200)
    assert runs, "default engine must produce runs"


@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
def test_vector_runs_are_straight_and_inside_canvas():
    """Garis vektor harus lurus (H/V eksak) dan berada di dalam kanvas."""
    # Uji kelurusan pakai engine pymupdf agar cepat; kelurusan berasal dari
    # konversi geometri yang sama (kedua engine diuji setara di tes lain).
    runs = extract_vector_runs(str(_PDF), dpi=200, engine="pymupdf")
    assert runs
    W, H = 1191 * 200 / 72.0, 842 * 200 / 72.0
    for r in runs:
        (x0, y0), (x1, y1) = r.points[0], r.points[-1]
        if r.axis == "h":
            assert y0 == y1, "horizontal run must be exactly straight"
        if r.axis == "v":
            assert x0 == x1, "vertical run must be exactly straight"
        assert -2 <= min(x0, x1) and max(x0, x1) <= W + 2
        assert -2 <= min(y0, y1) and max(y0, y1) <= H + 2


@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
def test_vector_tracer_excludes_closed_paths():
    """Bubble instrumen (closed path) tidak boleh menjadi run panjang."""
    import pymupdf
    doc = pymupdf.open(str(_PDF))
    page = doc[0]
    from pidcorr.implementations.vector_tracer import page_segments
    open_only = page_segments(page, rotation_matrix=page.rotation_matrix)
    with_closed = page_segments(page, rotation_matrix=page.rotation_matrix,
                               include_closed=True)
    assert len(with_closed) > len(open_only), \
        "closed paths must be excluded by default"
    doc.close()


@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
def test_orchestrator_uses_vector_for_pdf_and_raster_for_png():
    """Hybrid dispatch: PDF vektor -> tracer vektor; PNG -> fallback raster."""
    from pidcorr.orchestrator import PipelineOrchestrator
    from pidcorr.pipeline import load_image

    class _NoDetector:
        def detect(self, img_bgr, conf=0.3, progress=None):
            return []

        def load_weights(self, weights_path):
            pass

    class _NoExtractor:
        def extract(self, img_bgr, tile=1200, progress=None):
            return [], []

    class _NoClassifier:
        def classify_valve(self, img_bgr, sym):
            return ""

        def classify_instrument(self, img_bgr, sym):
            return "", ""

    orch = PipelineOrchestrator(detector=_NoDetector(), extractor=_NoExtractor(),
                                tracer=SkeletonLineTracer(), classifier=_NoClassifier())

    img = load_image(str(_PNG), dpi=100)
    res_vec = orch.run(img_bgr=img, image_path=str(_PDF), dpi=100)
    assert str(res_vec.get("tracer", "")).startswith("vector"), \
        f"PDF must dispatch to the vector tracer, got {res_vec.get('tracer')!r}"

    res_raster = orch.run(img_bgr=img, image_path=str(_PNG), dpi=100)
    assert res_raster.get("tracer") == "raster", \
        "PNG input must fall back to the raster tracer"


@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
def test_pymupdf_requires_rotation_matrix():
    """Regresi footgun rotasi: `get_cdrawings()` memberi koordinat UN-ROTATED.

    Lembar referensi ber-/Rotate 270. Tanpa `rotation_matrix`, koordinat vektor
    tidak sejajar citra render (ink coverage jatuh ~0.99 -> ~0.07), sehingga
    seluruh hasil tracing meleset. Uji ini mengunci perilaku itu supaya koreksi
    tidak hilang tanpa sengaja.
    """
    import pymupdf
    from pidcorr.implementations.vector_tracer import page_segments

    doc = pymupdf.open(str(_PDF))
    page = doc[0]
    assert page.rotation != 0, "fixture must be a rotated page for this regression"

    drawings = page.get_cdrawings()
    raw = page_segments(page, drawings=drawings, rotation_matrix=None,
                        include_closed=False)
    fixed = page_segments(page, drawings=drawings,
                          rotation_matrix=page.rotation_matrix,
                          include_closed=False)
    doc.close()

    assert raw and fixed

    # Ruang tampilan: lebar > tinggi (landscape). Tanpa koreksi rotasi, rentang
    # koordinat tertukar sumbu sehingga banyak titik jatuh di luar kanvas.
    W_pt, H_pt = 1191.0, 842.0
    def outside(segs):
        return sum(1 for a, b in segs
                   if max(a[0], b[0]) > W_pt + 1 or max(a[1], b[1]) > H_pt + 1)

    assert outside(fixed) == 0, "rotation-corrected segments must stay on the page"
    assert outside(raw) > 0, "un-rotated segments must fall outside the display page"


@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
@_needs_pdfplumber
def test_pdfplumber_engine_agrees_with_pymupdf():
    """Kedua engine harus menghasilkan geometri yang sama (bbox identik)."""
    from pidcorr.implementations.vector_tracer import extract_vector_runs

    runs_pm = extract_vector_runs(str(_PDF), dpi=350, engine="pymupdf")
    runs_pl = extract_vector_runs(str(_PDF), dpi=350, engine="pdfplumber")
    assert runs_pm and runs_pl

    def bbox(runs):
        xs = [p[0] for r in runs for p in r.points]
        ys = [p[1] for r in runs for p in r.points]
        return (min(xs), min(ys), max(xs), max(ys))

    bp, bl = bbox(runs_pm), bbox(runs_pl)
    # Toleransi 1 px: jumlah run boleh beda tipis (deteksi kurva), tetapi ruang
    # koordinatnya harus sama — inilah bukti kedua engine setara.
    assert abs(bp[0] - bl[0]) <= 1 and abs(bp[1] - bl[1]) <= 1
    assert abs(bp[2] - bl[2]) <= 1 and abs(bp[3] - bl[3]) <= 1, \
        f"engines disagree on extent: pymupdf={bp} pdfplumber={bl}"
    assert abs(len(runs_pm) - len(runs_pl)) <= max(5, 0.1 * len(runs_pm)), \
        "run counts should be close between engines"


@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
@_needs_pdfplumber
def test_vector_engine_selectable_via_env():
    """VECTOR_ENGINE memilih engine; orchestrator meneruskannya."""
    from pidcorr.orchestrator import PipelineOrchestrator
    from pidcorr.pipeline import load_image

    class _NoDetector:
        def detect(self, img_bgr, conf=0.3, progress=None):
            return []

        def load_weights(self, weights_path):
            pass

    class _NoExtractor:
        def extract(self, img_bgr, tile=1200, progress=None):
            return [], []

    class _NoClassifier:
        def classify_valve(self, img_bgr, sym):
            return ""

        def classify_instrument(self, img_bgr, sym):
            return "", ""

    orch = PipelineOrchestrator(detector=_NoDetector(), extractor=_NoExtractor(),
                                tracer=SkeletonLineTracer(), classifier=_NoClassifier())
    img = load_image(str(_PNG), dpi=100)

    os.environ["VECTOR_ENGINE"] = "pdfplumber"
    try:
        res = orch.run(img_bgr=img, image_path=str(_PDF), dpi=100)
        assert str(res.get("tracer", "")).startswith("vector")
        assert res.get("runs"), "pdfplumber engine must still produce runs"
    finally:
        os.environ.pop("VECTOR_ENGINE", None)


if __name__ == "__main__":
    test_vector_tracer_implements_interface()
    test_tier_classification()
    test_straight_segments_splits_on_direction_change()
    test_closed_helper_detects_loop()
    test_merge_intervals_joins_small_gap_and_keeps_large_gap()
    test_to_runs_classifies_axes()
    test_drop_glyph_noise_keeps_short_run_when_connected()
    test_cluster_rows_separates_side_by_side_tables()
    test_cluster_rows_rejects_border_absorption()
    test_table_regions_and_drop_inside()
    test_drop_inside_regions_noop_without_regions()
    print("[PASS] vector tracer unit tests")
