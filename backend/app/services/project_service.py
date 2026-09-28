import os
import math
import uuid
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile

from ..config import settings
from ..models.project import Project
from ..models.sheet import Sheet
from ..adapters.storage import LocalStorageAdapter
from ..adapters.pdf_renderer import load_drawing_image

storage = LocalStorageAdapter(settings.STORAGE_DIR)


class ProjectService:
    @staticmethod
    async def create_project(
        db: AsyncSession,
        name: str,
        description: str = "",
        tenant_id: str = "default_tenant",
        user_id: str = "default_user",
    ) -> Project:
        project = Project(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        db.add(project)
        await db.flush()
        await db.refresh(project)
        return project

    @staticmethod
    async def list_projects(
        db: AsyncSession,
        tenant_id: str = "default_tenant",
    ) -> List[Project]:
        query = select(Project).where(Project.tenant_id == tenant_id).order_by(Project.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_project(db: AsyncSession, project_id: str) -> Optional[Project]:
        query = select(Project).where(Project.id == project_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def delete_project(db: AsyncSession, project_id: str) -> bool:
        project = await ProjectService.get_project(db, project_id)
        if not project:
            return False
        # Remove project files from storage
        storage.delete(os.path.join("projects", project_id))
        await db.delete(project)
        await db.commit()
        return True

    @staticmethod
    async def delete_sheet(db: AsyncSession, project_id: str, sheet_id: str) -> bool:
        query = select(Sheet).where(Sheet.id == sheet_id, Sheet.project_id == project_id)
        result = await db.execute(query)
        sheet = result.scalar_one_or_none()
        if not sheet:
            return False
        if sheet.file_path:
            storage.delete(sheet.file_path)
            base_no_ext = os.path.splitext(sheet.file_path)[0]
            storage.delete(f"{base_no_ext}_files")
            storage.delete(f"{base_no_ext}.dzi")
        await db.delete(sheet)
        await db.commit()
        return True

    @staticmethod
    def _split_pdf_pages(abs_path: str, rel_path: str) -> List[tuple]:
        """PDF multi-halaman -> satu file PDF per halaman.

        Tiap halaman diekstrak sebagai PDF **1 halaman** (bukan render PNG) supaya
        geometri vektor dan `/Rotate` halaman tetap utuh — dengan begitu vector
        tracing dan Magic Wand tetap bekerja pada sheet hasil split, dan ukuran file
        tetap kecil. Sumber raster (scan) juga aman: isinya dibawa apa adanya.

        Return `[(rel_path, sheet_number)]`. Bukan PDF atau PDF 1 halaman ->
        `[(rel_path, "")]` dan file asli dipakai apa adanya (tidak menggandakan
        storage untuk kasus upload biasa).
        """
        if os.path.splitext(abs_path)[1].lower() != ".pdf":
            return [(rel_path, "")]
        try:
            import pymupdf
        except ImportError:
            return [(rel_path, "")]

        doc = None
        try:
            doc = pymupdf.open(abs_path)
            total = len(doc)
            if total <= 1:
                return [(rel_path, "")]

            base = os.path.splitext(rel_path)[0]
            out = []
            for i in range(total):
                page_rel = f"{base}-p{i + 1:03d}.pdf"
                page_abs = storage.get_file_path(page_rel)
                os.makedirs(os.path.dirname(page_abs), exist_ok=True)
                single = pymupdf.open()
                try:
                    single.insert_pdf(doc, from_page=i, to_page=i)
                    single.save(page_abs, garbage=4, deflate=True)
                finally:
                    single.close()
                out.append((page_rel, f"{i + 1}/{total}"))
            return out
        except Exception:
            # Split gagal (PDF rusak/terenkripsi) -> tetap unggah sebagai satu sheet
            # supaya user tidak kehilangan file, dan biarkan pipeline yang mengeluh.
            return [(rel_path, "")]
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    @staticmethod
    def _page_dims(abs_path: str, rel_paths: List[str], dpi: int) -> dict:
        """{rel_path: (w, h)} dalam pixel pada `dpi` target.

        PDF: dihitung dari rect halaman — terverifikasi sama persis dengan hasil
        render (5790x4094 / 4094x5790 pada 3 halaman uji) tetapi instan, sedangkan
        render 2,08 s/halaman tidak masuk akal untuk PDF 50 halaman. `ceil`, BUKAN
        `round`: pypdfium2 membulatkan ke atas, dan `round` bikin meleset 1 px
        (4093 vs 4094) sehingga overlay kanvas melar 1 px. Raster: dirender.
        """
        dims = {}
        if os.path.splitext(abs_path)[1].lower() != ".pdf":
            dims[rel_paths[0]] = ProjectService._measure(abs_path, dpi)
            return dims
        try:
            import pymupdf
            doc = pymupdf.open(abs_path)
            scale = dpi / 72.0
            for i, page_rel in enumerate(rel_paths):
                rect = doc[i].rect
                dims[page_rel] = (math.ceil(rect.width * scale),
                                  math.ceil(rect.height * scale))
            doc.close()
        except Exception:
            pass
        return dims

    @staticmethod
    async def add_sheets(
        db: AsyncSession,
        project_id: str,
        file: UploadFile,
        dpi: int = 350,
        sheet_number: str = "",
        tenant_id: str = "default_tenant",
        user_id: str = "default_user",
    ) -> List[Sheet]:
        """Simpan upload dan buat SATU `Sheet` per halaman PDF.

        PDF 1 halaman / PNG / JPG -> tetap 1 Sheet (perilaku lama, file asli dipakai).
        PDF N halaman -> N Sheet, masing-masing menunjuk file PDF 1 halaman sendiri.
        """
        batch_id = str(uuid.uuid4())
        filename = file.filename or "drawing"
        ext = os.path.splitext(filename)[1].lower() or ".png"
        rel_path = os.path.join("projects", project_id, "sheets", f"{batch_id}{ext}")

        content = await file.read()
        storage.save_file(rel_path, content)
        abs_path = storage.get_file_path(rel_path)

        # Dimensi citra per halaman. PDF: dari rect halaman (instan). Raster: render.
        page_files = ProjectService._split_pdf_pages(abs_path, rel_path)
        dims = ProjectService._page_dims(abs_path, [p for p, _ in page_files], dpi)

        sheets: List[Sheet] = []
        for page_rel, label in page_files:
            width, height = dims.get(page_rel, (0, 0))
            sheet = Sheet(
                id=str(uuid.uuid4()),
                project_id=project_id,
                tenant_id=tenant_id,
                user_id=user_id,
                filename=filename,
                sheet_number=sheet_number or label,
                file_path=page_rel,
                status="uploaded",
                dpi=dpi,
                width=width,
                height=height,
            )
            db.add(sheet)
            sheets.append(sheet)

        await db.flush()
        for sheet in sheets:
            await db.refresh(sheet)
        return sheets

    @staticmethod
    def _measure(abs_path: str, dpi: int) -> tuple:
        """(w, h) citra hasil render — jalur raster (PNG/JPG) dan PDF 1 halaman."""
        try:
            img = load_drawing_image(abs_path, dpi=dpi)
            h, w = img.shape[:2]
            return int(w), int(h)
        except Exception:
            return 0, 0

    @staticmethod
    async def get_sheet(db: AsyncSession, sheet_id: str) -> Optional[Sheet]:
        query = select(Sheet).where(Sheet.id == sheet_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()
