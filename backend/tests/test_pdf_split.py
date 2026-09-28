"""Multi-page PDF splitter (Fase 1) — satu PDF N halaman -> N Sheet.

Yang dikunci:
  1. PDF 3 halaman -> 3 Sheet, satu file PDF 1 halaman per sheet, terurut,
  2. halaman yang dihasilkan tetap VEKTOR dan mempertahankan `/Rotate` — ini yang
     membuat vector tracing dan Magic Wand tetap bekerja pada sheet hasil split,
     dan `width`/`height` yang disimpan sama persis dengan hasil render (ceil,
     bukan round: `round` membuat overlay kanvas melar 1 px),
  3. PDF 1 halaman & PNG tidak dipecah dan file aslinya dipakai apa adanya,
  4. setiap halaman punya thumbnail sendiri yang berbeda.
"""
import os
import sys
import uuid

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
for _p in (_BACKEND_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pymupdf
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.adapters.pdf_renderer import load_drawing_image
from app.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.project import Project
from _fixtures import fixture_path

TEST_DB_URL = "sqlite+aiosqlite:///./test_pdf_split.db"
test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession,
                                      autocommit=False, autoflush=False, expire_on_commit=False)

async def override_get_db():
    # WAJIB sama dengan `app.db.session.get_db`: commit setelah handler selesai.
    # Tanpa commit, respons pertama tetap benar (objek sudah di-flush di session yang
    # sama) tetapi request berikutnya membaca DB kosong — persis kegagalan yang bikin
    # endpoint list terlihat rusak padahal bukan.
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

@pytest.fixture(autouse=True)
def _split_db_override():
    """Pasang override `get_db` hanya selama test modul ini (lihat catatan di
    `test_wand_trace.py`: modul lain juga memasang override global saat import)."""
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    yield
    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous

_PAGES = [
    "BCD3-605-42-PID-1-006-01 Rev.10-CCD2.pdf",
    "BCD3-605-42-PID-1-014-01 Rev.6-CCD2.pdf",
    "BCD3-605-42-PID-1-005-01 Rev.4-CCD2.pdf",
]

def _bundle(parts: int) -> bytes:
    """PDF N halaman dari N P&ID nyata (rotasi/vektor beragam)."""
    srcs = [fixture_path("Contoh P&ID", n) for n in _PAGES[:parts]]
    if not all(os.path.exists(s) for s in srcs):
        pytest.skip("reference vector PDFs not available")
    out = pymupdf.open()
    for s in srcs:
        d = pymupdf.open(str(s))
        out.insert_pdf(d, from_page=0, to_page=0)
        d.close()
    buf = out.tobytes(garbage=4, deflate=True)
    out.close()
    return buf

def _multipart(data: bytes, filename: str, dpi: int = 350) -> tuple:
    b = "----split" + uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/pdf\r\n\r\n").encode()
    body += data + b"\r\n"
    body += f"--{b}\r\nContent-Disposition: form-data; name=\"dpi\"\r\n\r\n{dpi}\r\n--{b}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={b}"}

async def _new_project() -> str:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    pid = str(uuid.uuid4())
    async with TestSessionLocal() as session:
        session.add(Project(id=pid, name="Split Test", tenant_id="default_tenant",
                            user_id="default_user"))
        await session.commit()
    return pid

@pytest.mark.asyncio
async def test_three_page_pdf_becomes_three_sheets():
    pid = await _new_project()
    body, headers = _multipart(_bundle(3), "CCD2-BUNDLE-3P.pdf")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(f"/api/v1/projects/{pid}/sheets", content=body, headers=headers)
        assert r.status_code == 201, r.text
        sheets = r.json()
        assert len(sheets) == 3, sheets
        assert [s["sheet_number"] for s in sheets] == ["1/3", "2/3", "3/3"]
        assert len({s["file_path"] for s in sheets}) == 3, "tiap halaman harus file sendiri"
        assert all(s["file_path"].lower().endswith(".pdf") for s in sheets)
        assert all(s["filename"] == "CCD2-BUNDLE-3P.pdf" for s in sheets)
        assert all(s["project_id"] == pid for s in sheets)
        assert all(s["status"] == "queued" for s in sheets), "Semua sheet harus berstatus awal 'queued'"

        # Endpoint list (dipakai grid Folder View) urut halaman.
        r2 = await ac.get(f"/api/v1/projects/{pid}/sheets")
        assert r2.status_code == 200
        assert [s["sheet_number"] for s in r2.json()] == ["1/3", "2/3", "3/3"]

        # Setiap halaman punya thumbnail sendiri (isi berbeda = hash berbeda).
        hashes = []
        for s in sheets:
            rt = await ac.get(f"/api/v1/projects/{pid}/sheets/{s['id']}/thumbnail?size=240")
            assert rt.status_code == 200, rt.text
            hashes.append(hash(tuple(rt.content)))
        assert len(set(hashes)) == 3, "thumbnail tiap halaman harus berbeda"

def test_split_pages_keep_vector_and_rotate_and_exact_dims():
    """Inti keputusan 'PDF 1 halaman': vektor + /Rotate + dimensi persis."""
    pid = "dim-check"
    rel_dir = os.path.join("projects", pid, "sheets")
    abs_dir = os.path.join(settings.STORAGE_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    bundle = os.path.join(abs_dir, "bundle.pdf")
    with open(bundle, "wb") as f:
        f.write(_bundle(3))

    from app.services.project_service import ProjectService
    entries = ProjectService._split_pdf_pages(bundle, os.path.join(rel_dir, "bundle.pdf"))
    assert [label for _rel, label in entries] == ["1/3", "2/3", "3/3"]

    src = pymupdf.open(str(fixture_path("Contoh P&ID", _PAGES[0])))
    src_rot = src[0].rotation
    src.close()

    for i, (rel, _label) in enumerate(entries):
        page_path = os.path.join(settings.STORAGE_DIR, rel)
        assert os.path.exists(page_path)
        doc = pymupdf.open(page_path)
        try:
            assert len(doc) == 1, "file hasil split harus 1 halaman"
            # Vektor tidak boleh hilang (kalau hilang, Magic Wand mati di sheet ini).
            assert len(doc[0].get_cdrawings()) > 100, "geometri vektor hilang saat split"
            if i == 0:
                assert doc[0].rotation == src_rot, "/Rotate halaman tidak dipertahankan"
        finally:
            doc.close()

        # Dimensi tersimpan == dimensi render sebenarnya (ceil, bukan round).
        w, h = ProjectService._measure(page_path, 350)
        dims = ProjectService._page_dims(page_path, [rel], 350)
        assert dims[rel] == (w, h), (
            f"halaman {i + 1}: tersimpan {dims[rel]} != render {(w, h)} — overlay kanvas "
            f"akan melar"
        )
        img = load_drawing_image(page_path, dpi=350)
        assert (img.shape[1], img.shape[0]) == dims[rel]

@pytest.mark.asyncio
async def test_single_page_pdf_is_not_split(tmp_path):
    pid = await _new_project()
    one = _bundle(1)
    body, headers = _multipart(one, "SINGLE-PAGE.pdf")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(f"/api/v1/projects/{pid}/sheets", content=body, headers=headers)
        assert r.status_code == 201, r.text
        sheets = r.json()
        assert len(sheets) == 1, sheets
        assert sheets[0]["sheet_number"] == "", "PDF 1 halaman tidak diberi nomor halaman"
        # File asli dipakai apa adanya (tanpa duplikasi storage).
        assert not sheets[0]["file_path"].endswith("-p001.pdf")
        assert sheets[0]["width"] and sheets[0]["height"]
