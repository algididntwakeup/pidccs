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


@router.delete("/{project_id}/sheets/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sheet(
    project_id: str,
    sheet_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete an individual P&ID sheet drawing from a project."""
    deleted = await ProjectService.delete_sheet(db=db, project_id=project_id, sheet_id=sheet_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Sheet not found in this project")
    return None


@router.post("/{project_id}/sheets/{sheet_id}/runs/{run_idx}/split")
@router.post("/{project_id}/runs/{run_idx}/split")
async def split_run_in_project(
    project_id: str,
    run_idx: int,
    payload: dict,
    sheet_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Pecah polyline run pada koordinat (x, y). Mendukung route langsung via project_id."""
    from pidcorr.lines import split_poly_run

    project = await ProjectService.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    target_sheet = None
    if sheet_id:
        target_sheet = await ProjectService.get_sheet(db, sheet_id)
    elif project.sheets:
        target_sheet = project.sheets[0]

    if not target_sheet:
        raise HTTPException(status_code=404, detail="No sheet found in project")

    if not target_sheet.result_json or "runs" not in target_sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(target_sheet.result_json)
    runs = res.get("runs", [])
    pids = res.get("piping_ids", [])
    x = float(payload.get("x", 0))
    y = float(payload.get("y", 0))

    try:
        new_runs, new_pids, run_a, run_b, new_idx = split_poly_run(
            runs=runs,
            run_idx=run_idx,
            split_x=x,
            split_y=y,
            piping_ids=pids,
        )
    except (IndexError, ValueError) as err:
        raise HTTPException(status_code=400, detail=str(err))

    res["runs"] = new_runs
    if new_pids is not None:
        res["piping_ids"] = new_pids

    target_sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "split_point": [x, y],
        "original_run_idx": run_idx,
        "new_run_idx": new_idx,
        "run_a": run_a,
        "run_b": run_b,
        "total_runs": len(new_runs),
        "result": target_sheet.result_json,
    }


@router.patch("/{project_id}/sheets/{sheet_id}/runs/{run_idx}/color")
async def update_sheet_run_color(
    project_id: str,
    sheet_id: str,
    run_idx: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update warna stroke untuk satu segmen polyline pipa."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json or "runs" not in sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(sheet.result_json)
    runs = res.get("runs", [])
    if run_idx < 0 or run_idx >= len(runs):
        raise HTTPException(status_code=400, detail=f"Run index {run_idx} out of range")

    color = payload.get("color", "#2563EB")
    runs[run_idx]["color"] = color
    res["runs"] = runs
    sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "run_idx": run_idx,
        "color": color,
        "result": sheet.result_json,
    }


@router.patch("/{project_id}/sheets/{sheet_id}/runs/batch-color")
async def batch_update_sheet_run_colors(
    project_id: str,
    sheet_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Batch update warna untuk beberapa segmen pipa (Shift+Click multi-select)."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json or "runs" not in sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(sheet.result_json)
    runs = res.get("runs", [])
    n_runs = len(runs)

    run_idxs = payload.get("run_idxs", [])
    color = payload.get("color", "#2563EB")
    updated_count = 0
    for ri in run_idxs:
        if 0 <= ri < n_runs:
            runs[ri]["color"] = color
            updated_count += 1

    res["runs"] = runs
    sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "updated_count": updated_count,
        "color": color,
        "result": sheet.result_json,
    }

