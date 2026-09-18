import os
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
    async def add_sheet(
        db: AsyncSession,
        project_id: str,
        file: UploadFile,
        dpi: int = 350,
        sheet_number: str = "",
        tenant_id: str = "default_tenant",
        user_id: str = "default_user",
    ) -> Sheet:
        sheet_id = str(uuid.uuid4())
        ext = os.path.splitext(file.filename or "")[1].lower() or ".png"
        rel_path = os.path.join("projects", project_id, "sheets", f"{sheet_id}{ext}")

        # Save uploaded file
        content = await file.read()
        storage.save_file(rel_path, content)

        # Inspect dimensions from image/PDF
        abs_path = storage.get_file_path(rel_path)
        w, h = 0, 0
        try:
            img = load_drawing_image(abs_path, dpi=dpi)
            h, w = img.shape[:2]
        except Exception:
            pass

        sheet = Sheet(
            id=sheet_id,
            project_id=project_id,
            tenant_id=tenant_id,
            user_id=user_id,
            filename=file.filename or "drawing",
            sheet_number=sheet_number,
            file_path=rel_path,
            status="uploaded",
            dpi=dpi,
            width=w,
            height=h,
        )
        db.add(sheet)
        await db.flush()
        await db.refresh(sheet)
        return sheet

    @staticmethod
    async def get_sheet(db: AsyncSession, sheet_id: str) -> Optional[Sheet]:
        query = select(Sheet).where(Sheet.id == sheet_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()
