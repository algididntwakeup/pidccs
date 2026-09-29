"""Magic Wand vector geometry and cache/prewarm utilities.

Tiga hal yang dikunci di sini:
  1. pemilihan garis: `nearest_run_at` menghormati radius dan memenangkan pipa
     terpanjang saat jarak seri; `find_duplicate_run` mengenali pipa yang sudah
     ter-trace dari kedua ujungnya,
  2. cache: `prewarm_vector_index` mengisi cache yang sama dengan yang dibaca
     `get_vector_index` (klik pertama tidak menunggu parse pdfplumber),
  3. Fast Trace already provides candidate runs for opt-in marking; no trace-click
     endpoint is involved.
"""
import os
import shutil
import sys
import time

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
for _p in (_BACKEND_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.config import settings
from pidcorr import lines as wand
from pidcorr.lines import PipeRun, find_duplicate_run, nearest_run_at
from _fixtures import fixture_path

_PDF_NAME = "BCD3-605-42-PID-1-006-01 Rev.10-CCD2.pdf"
_PDF_SRC = str(fixture_path("Contoh P&ID", _PDF_NAME))
_DPI = 350

def _stored_pdf() -> str:
    """Salin fixture PDF ke STORAGE_DIR (data/storage) dan kembalikan path absolutnya."""
    if not os.path.exists(_PDF_SRC):
        pytest.skip("reference vector PDF not available")
    os.makedirs(settings.STORAGE_DIR, exist_ok=True)
    dst = os.path.join(settings.STORAGE_DIR, "wand-test", _PDF_NAME)
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(_PDF_SRC, dst)
    return dst

def _warm_index(pdf_path: str) -> list:
    """Indeks vektor (tanpa furniture) — dibangun sinkron, dipakai lintas test."""
    return wand.get_vector_index(pdf_path, dpi=_DPI, rot=0)

# ------------------------------------------------------------------ geometri ----
def test_nearest_run_at_picks_closest_and_respects_radius():
    index = [
        PipeRun(points=[(0, 0), (100, 0)], axis="h"),
        PipeRun(points=[(0, 60), (100, 60)], axis="h"),
    ]
    run, dist = nearest_run_at(index, 50, 3, radius=15)
    assert run is index[0]
    assert abs(dist - 3.0) < 1e-6

    run, dist = nearest_run_at(index, 50, 3, radius=2)
    assert run is None
    assert dist == float("inf")

def test_nearest_run_at_prefers_longer_run_on_tie():
    """Jarak seri (<= 0.5 px) -> pipa panjang menang atas stub pendek."""
    stub = PipeRun(points=[(40, 0), (60, 0)], axis="h")
    pipe = PipeRun(points=[(0, 0), (500, 0)], axis="h")
    run, _ = nearest_run_at([stub, pipe], 50, 2, radius=15)
    assert run is pipe

def test_find_duplicate_run_detects_covered_polyline():
    runs = [{"points": [[0, 0], [100, 0]]}]
    assert find_duplicate_run(runs, PipeRun(points=[(2, 1), (98, 1)])) == 0
    # Ujung kedua menyimpang 40 px -> bukan duplikat.
    assert find_duplicate_run(runs, PipeRun(points=[(2, 1), (98, 40)])) is None
    # Kandidat tanpa geometri yang cukup tidak pernah dianggap duplikat.
    assert find_duplicate_run(runs, PipeRun(points=[(2, 1)])) is None

# --------------------------------------------------------------------- cache ----
def test_prewarm_then_get_returns_same_object():
    pdf = _stored_pdf()
    with wand._WAND_INDEX_LOCK:
        wand._WAND_INDEX_CACHE.clear()
    key = wand._wand_index_key(pdf, _DPI, 0, 0, None)

    t0 = time.time()
    wand.prewarm_vector_index(pdf, dpi=_DPI, rot=0)
    obj = None
    deadline = time.time() + 120
    while time.time() < deadline:
        with wand._WAND_INDEX_LOCK:
            obj = wand._WAND_INDEX_CACHE.get(key)
        if obj:
            break
        time.sleep(0.2)
    elapsed = time.time() - t0
    assert obj, "prewarm tidak pernah mengisi cache"
    assert len(obj) > 0
    assert wand.get_vector_index(pdf, dpi=_DPI, rot=0) is obj
    # Anggaran: prewarm harus selesai jauh sebelum user selesai mengarahkan kursor ke
    # pipa, kalau tidak klik pertama ikut menunggu parse. (PyMuPDF ~1 s; pdfplumber
    # butuh ~10 s pada lembar yang sama.)
    print(f"[wand] prewarm {elapsed:.2f} s, indeks {len(obj)} run")
    assert elapsed < 5.0, f"prewarm {elapsed:.2f} s terlalu lambat untuk jalur klik"
    assert wand._WAND_ENGINE == "pymupdf"

def test_wand_index_geometry_matches_detection_index():
    """Indeks cepat (PyMuPDF) harus mewakili geometri yang sama dengan indeks deteksi
    (pdfplumber): setiap ujung run hasil deteksi harus punya garis di indeks wand."""
    pdf = _stored_pdf()
    det = wand.build_vector_index(pdf, dpi=_DPI, rot=0, engine="pdfplumber")
    fast = wand.build_vector_index(pdf, dpi=_DPI, rot=0, engine="pymupdf")
    assert det and fast
    checked = 0
    for r in det[:25]:
        for pt in (r.points[0], r.points[-1]):
            run, dist = nearest_run_at(fast, pt[0], pt[1], radius=3.0)
            assert run is not None, f"titik {pt} dari indeks deteksi tidak ada di indeks wand"
            checked += 1
    assert checked >= 40
    print(f"[wand] geometri: deteksi {len(det)} run, wand {len(fast)} run, {checked} ujung cocok")
