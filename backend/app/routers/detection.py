from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from pydantic import BaseModel
from ..db.session import get_db
from ..schemas.job import JobResponse
from ..models.sheet import Sheet
from ..models.job import Job
from ..services.project_service import ProjectService
from ..services.detection_service import (
    dispatch_detection_job,
    dispatch_enrichment_job,
    queue_sheet_detection,
    queue_sheet_enrichment,
)

router = APIRouter(tags=["detection"])


class DetectRequest(BaseModel):
    dpi: Optional[int] = None
    rot: Optional[int] = None
    mode: Optional[str] = "lines_only"
    sheet_id: Optional[str] = None


@router.post(
    "/projects/{project_id}/sheets/{sheet_id}/detect",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED
)
async def trigger_detection(
    project_id: str,
    sheet_id: str,
    background_tasks: BackgroundTasks,
    dpi: Optional[int] = None,
    rot: Optional[int] = None,
    mode: Optional[str] = "lines_only",
    payload: Optional[DetectRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger asynchronous P&ID digitization pipeline on a sheet."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    target_mode = (payload.mode if payload and payload.mode else None) or mode or "lines_only"
    target_dpi = (payload.dpi if payload and payload.dpi is not None else None) or dpi or sheet.dpi or 350
    target_rot = (payload.rot if payload and payload.rot is not None else None) or (rot if rot is not None else sheet.rot or 0)

    job = queue_sheet_detection(sheet, mode=target_mode)
    await db.commit()

    dispatch_detection_job(
        job, sheet, mode=target_mode, dpi=target_dpi, rot=target_rot,
        background_tasks=background_tasks,
    )

    return JobResponse(
        job_id=job.id,
        sheet_id=sheet_id,
        status="queued",
        progress_pct=0,
        step="queued",
        message=job.message,
    )


@router.post(
    "/projects/{project_id}/detect",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED
)
async def trigger_project_detection(
    project_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[DetectRequest] = None,
    dpi: Optional[int] = None,
    rot: Optional[int] = None,
    mode: Optional[str] = "lines_only",
    db: AsyncSession = Depends(get_db),
):
    """Trigger detection for a project using its active or first sheet."""
    project = await ProjectService.get_project(db, project_id)
    if not project or not project.sheets:
        raise HTTPException(status_code=404, detail="Project or sheets not found")

    target_sheet_id = (payload.sheet_id if payload and payload.sheet_id else None) or project.sheets[0].id
    return await trigger_detection(
        project_id=project_id,
        sheet_id=target_sheet_id,
        background_tasks=background_tasks,
        dpi=dpi,
        rot=rot,
        mode=mode,
        payload=payload,
        db=db,
    )


@router.post(
    "/projects/{project_id}/sheets/{sheet_id}/enrich",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED
)
async def trigger_enrichment(
    project_id: str,
    sheet_id: str,
    background_tasks: BackgroundTasks,
    dpi: Optional[int] = None,
    rot: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger on-demand AI OCR & symbol enrichment on an already traced sheet."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    marked_runs = [
        run for run in (sheet.result_json or {}).get("runs", [])
        if isinstance(run, dict) and run.get("marked") is True
    ]
    if not marked_runs:
        raise HTTPException(
            status_code=400,
            detail="Mark at least one traced pipe before syncing with AI",
        )

    target_dpi = dpi or sheet.dpi or 350
    target_rot = rot if rot is not None else sheet.rot or 0

    job = queue_sheet_enrichment(sheet)
    await db.commit()

    dispatch_enrichment_job(
        job, sheet, dpi=target_dpi, rot=target_rot, background_tasks=background_tasks,
    )

    return JobResponse(
        job_id=job.id,
        sheet_id=sheet_id,
        status="queued",
        progress_pct=0,
        step="queued",
        message="Enrichment job queued",
    )


@router.post(
    "/projects/{project_id}/enrich",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED
)
async def trigger_project_enrichment(
    project_id: str,
    background_tasks: BackgroundTasks,
    sheet_id: Optional[str] = None,
    dpi: Optional[int] = None,
    rot: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger enrichment on the specified or first sheet of a project."""
    project = await ProjectService.get_project(db, project_id)
    if not project or not project.sheets:
        raise HTTPException(status_code=404, detail="Project or sheets not found")

    target_sheet_id = sheet_id or project.sheets[0].id
    return await trigger_enrichment(
        project_id=project_id,
        sheet_id=target_sheet_id,
        background_tasks=background_tasks,
        dpi=dpi,
        rot=rot,
        db=db,
    )



@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the live status and progress of a detection job."""
    query = select(Job).where(Job.id == job_id)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobResponse(
        job_id=job.id,
        sheet_id=job.sheet_id,
        status=job.status,
        progress_pct=job.progress_pct,
        step=job.step,
        message=job.message,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.get("/jobs", response_model=List[JobResponse])
async def list_jobs(
    sheet_id: Optional[str] = None,
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List detection jobs, optionally filtered by sheet (most recent first).

    Used by the frontend to RE-ATTACH to an in-flight detection job after a
    navigation/remount, so the loading/progress UI can be resumed without the
    user having kept the page open.
    """
    query = select(Job)
    if sheet_id:
        query = query.where(Job.sheet_id == sheet_id)
    elif project_id:
        query = query.join(Sheet, Sheet.id == Job.sheet_id).where(Sheet.project_id == project_id)
    query = query.order_by(Job.created_at.desc()).limit(50)

    result = await db.execute(query)
    jobs = result.scalars().all()
    return [
        JobResponse(
            job_id=j.id,
            sheet_id=j.sheet_id,
            status=j.status,
            progress_pct=j.progress_pct,
            step=j.step,
            message=j.message,
            error=j.error,
            created_at=j.created_at,
            completed_at=j.completed_at,
        )
        for j in jobs
    ]
