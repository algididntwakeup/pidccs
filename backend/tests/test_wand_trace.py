"""Magic Wand (HITL 1-click trace) — geometri, cache/prewarm, dan endpoint.

Tiga hal yang dikunci di sini:
  1. pemilihan garis: `nearest_run_at` menghormati radius dan memenangkan pipa
     terpanjang saat jarak seri; `find_duplicate_run` mengenali pipa yang sudah
     ter-trace dari kedua ujungnya,
  2. cache: `prewarm_vector_index` mengisi cache yang sama dengan yang dibaca
     `get_vector_index` (klik pertama tidak menunggu parse pdfplumber),
  3. endpoint `POST /trace-click`: menambah run sekali, menolak duplikat pada
     klik kedua di titik yang sama, menolak sheet non-PDF, dan tetap di bawah
     anggaran latensi 1 s saat indeks sudah hangat.
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

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from httpx import AsyncClient, ASGITransport

from app.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.project import Project
from app.models.sheet import Sheet
from pidcorr import lines as wand
from pidcorr.lines import PipeRun, find_duplicate_run, nearest_run_at
from _fixtures import fixture_path

TEST_DB_URL = "sqlite+aiosqlite:///./test_wand_trace.db"
test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

async def override_get_db():
    async with TestSessionLocal() as session:
        yield session

@pytest.fixture(autouse=True)
def _wand_db_override():
    """Pasang override `get_db` milik modul ini HANYA selama test modul ini berjalan.

    Tujuh modul test memasang `app.dependency_overrides[get_db]` global saat import,
    dan urutan impor pytest tidak dijamin — override yang dipasang saat import modul
    ini akan membajak test modul lain yang sudah dikoleksi (mereka membaca DB sqlite
    milik modul ini). Karena itu modul ini TIDAK memasang override di level modul:
    hanya fixture ini, yang memasang lalu mengembalikan override sebelumnya.
    """
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    yield
    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous

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

# ------------------------------------------------------------------ endpoint ----
async def _seed_sheet(pdf_path: str, filename: str = _PDF_NAME,
                      rel_path: str | None = None) -> tuple[str, str]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    rel = rel_path or os.path.relpath(pdf_path, settings.STORAGE_DIR).replace("\\", "/")
    async with TestSessionLocal() as session:
        session.add(Project(id="wand-proj", name="Wand Test", tenant_id="default_tenant",
                            user_id="default_user"))
        await session.commit()
        session.add(Sheet(
            id="wand-sheet", project_id="wand-proj", filename=filename, file_path=rel,
            status="detected", tenant_id="default_tenant", user_id="default_user",
            dpi=_DPI,
            result_json={"runs": [], "piping_ids": [], "symbols": [], "conn_points": [],
                         "furniture": [], "dpi": _DPI, "rot": 0, "w": 3309, "h": 2339,
                         "image_path": pdf_path},
        ))
        await session.commit()
    return "wand-proj", "wand-sheet"

@pytest.mark.asyncio
async def test_trace_click_endpoint_adds_then_dedupes():
    pdf = _stored_pdf()
    project_id, sheet_id = await _seed_sheet(pdf)
    index = _warm_index(pdf)
    assert index, "indeks vektor kosong"
    run = index[0]
    pts = run.points
    x = int((pts[0][0] + pts[-1][0]) / 2)
    y = int((pts[0][1] + pts[-1][1]) / 2)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        url = f"/api/v1/projects/{project_id}/sheets/{sheet_id}/trace-click"
        r1 = await ac.post(url, json={"x": x, "y": y, "radius": 15})
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1["added"] is True, d1
        assert d1["reason"] is None
        assert d1["run_idx"] == 0
        assert d1["distance"] is not None and d1["distance"] <= 15
        assert len(d1["run"]["points"]) >= 2
        assert d1["run"]["manual"] is True
        assert d1["total_runs"] == 1
        assert len(d1["result"]["runs"]) == 1

        r2 = await ac.post(url, json={"x": x, "y": y, "radius": 15})
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2["added"] is False, d2
        assert d2["reason"] == "duplicate"
        assert d2["run_idx"] == 0
        assert d2["total_runs"] == 1

        # Klik di area kosong (pojok jauh dari geometri mana pun) -> tidak ada kandidat.
        r3 = await ac.post(url, json={"x": 4, "y": 4, "radius": 2})
        assert r3.status_code == 200, r3.text
        d3 = r3.json()
        assert d3["added"] is False
        assert d3["reason"] == "none"
        assert d3["total_runs"] == 1

@pytest.mark.asyncio
async def test_trace_click_rejects_non_pdf_sheet():
    pdf = _stored_pdf()
    project_id, sheet_id = await _seed_sheet(pdf, filename="sheet.png")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            f"/api/v1/projects/{project_id}/sheets/{sheet_id}/trace-click",
            json={"x": 1200, "y": 900, "radius": 15},
        )
    assert r.status_code == 400, r.text
    assert "PDF vektor" in r.json()["detail"]

@pytest.mark.asyncio
async def test_trace_click_warm_latency_under_budget():
    pdf = _stored_pdf()
    project_id, sheet_id = await _seed_sheet(pdf)
    index = _warm_index(pdf)
    # Dua garis berbeda: klik pertama (tidak dihitung) memanaskan async engine DB +
    # koneksi sqlite; klik kedua yang diukur adalah pengalaman klik normal engineer.
    run_a, run_b = index[0], index[1]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        url = f"/api/v1/projects/{project_id}/sheets/{sheet_id}/trace-click"
        await ac.post(url, json={"x": int(run_a.points[0][0]), "y": int(run_a.points[0][1]),
                                 "radius": 15})
        t0 = time.perf_counter()
        r = await ac.post(url, json={"x": int(run_b.points[0][0]), "y": int(run_b.points[0][1]),
                                     "radius": 15})
        elapsed = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    assert r.json()["added"] is True, r.text
    print(f"[wand] trace-click hangat: {elapsed * 1000:.1f} ms")
    assert elapsed < 1.0, f"latensi klik hangat {elapsed:.3f} s melebihi anggaran 1 s"
