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
    _merge_intervals,
    _to_runs,
    _drop_glyph_noise,
    _cluster_rows,
    _table_regions,
    _drop_inside_regions,
)
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer
from pidcorr.interfaces.perception import BaseLineTracer
from pidcorr.lines import PipeRun

_PDF = fixture_path("Contoh P&ID", "BCD3-605-42-PID-1-014-01 Rev.6-CCD2.pdf")
_PNG = fixture_path("Contoh P&ID", "BCD3-605-42-PID-1-014-01 Rev.6-CCD2.png")


# --------------------------------------------------------------------- unit ----
def test_vector_tracer_implements_interface():
    assert isinstance(VectorLineTracer(), BaseLineTracer)


def test_tier_classification():
    """PDF vektor -> A1/A2; PNG (tanpa geometri) -> raster."""
    assert tier_of_pdf(str(_PNG)) == "raster"
    assert tier_of_pdf(str(_PDF)) in ("A1", "A2")


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
def test_vector_trace_is_sub_second():
    """Target Phase 2: tracing vektor < 1 detik (tanpa binarisasi/skeletonisasi)."""
    tracer = VectorLineTracer()
    tracer.trace(pdf_path=str(_PDF), dpi=200)            # warmup
    t0 = time.perf_counter()
    runs = tracer.trace(pdf_path=str(_PDF), dpi=200)
    elapsed = time.perf_counter() - t0
    assert runs
    assert elapsed < 1.0, f"vector tracing took {elapsed:.3f}s (target < 1s)"


@pytest.mark.skipif(not _PDF.exists(), reason="reference vector PDF not available")
def test_vector_runs_are_straight_and_inside_canvas():
    """Garis vektor harus lurus (H/V eksak) dan berada di dalam kanvas."""
    runs = extract_vector_runs(str(_PDF), dpi=200)
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
