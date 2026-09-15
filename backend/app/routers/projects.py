from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..schemas.project import ProjectCreate, ProjectResponse, SheetResponse
from ..services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new P&ID project or plant unit workspace."""
    project = await ProjectService.create_project(
        db=db,
        name=payload.name,
        description=payload.description or "",
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
    )
    return project


@router.get("", response_model=List[ProjectResponse])
async def list_projects(
    tenant_id: str = "default_tenant",
    db: AsyncSession = Depends(get_db),
):
    """List all projects for the given tenant."""
    return await ProjectService.list_projects(db=db, tenant_id=tenant_id)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get project details including all uploaded P&ID drawing sheets."""
    project = await ProjectService.get_project(db=db, project_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete project and associated sheets."""
    deleted = await ProjectService.delete_project(db=db, project_id=project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return None


@router.post("/{project_id}/sheets", response_model=SheetResponse, status_code=status.HTTP_201_CREATED)
async def upload_sheet(
    project_id: str,
    file: UploadFile = File(...),
    dpi: int = Form(350),
    sheet_number: str = Form(""),
    tenant_id: str = Form("default_tenant"),
    user_id: str = Form("default_user"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a P&ID sheet drawing (PDF, PNG, JPG) to a project."""
    project = await ProjectService.get_project(db=db, project_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    sheet = await ProjectService.add_sheet(
        db=db,
        project_id=project_id,
        file=file,
        dpi=dpi,
        sheet_number=sheet_number,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    return sheet


@router.get("/{project_id}/sheets/{sheet_id}", response_model=SheetResponse)
async def get_sheet(
    project_id: str,
    sheet_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get sheet metadata."""
    sheet = await ProjectService.get_sheet(db=db, sheet_id=sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found in this project")
    return sheet
