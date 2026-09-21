import uuid
import asyncio
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..db.session import get_db, AsyncSessionLocal
from ..schemas.job import JobResponse
from ..models.sheet import Sheet
from ..models.job import Job
from ..services.project_service import ProjectService
from ..services.detection_service import execute_sheet_detection
from ..services.grouping_service import GroupingService
from ..services.tile_service import TileService
from ..config import settings

router = APIRouter(tags=["detection"])


async def _run_detection_in_background(job_id: str, sheet_id: str, file_path: str, dpi: int, rot: int):
    """Fallback runner when Celery is not active or for lightweight dev testing."""
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        sheet = await session.get(Sheet, sheet_id)
        if not job or not sheet:
            return

        try:
            job.status = "processing"
            job.step = "starting"
            job.message = "Running detection pipeline..."
            sheet.status = "detecting"
            await session.commit()

            # Execute pipeline synchronously in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                execute_sheet_detection,
                file_path,
                dpi,
                rot,
            )

            # Compute circuits
            circuits = GroupingService.compute_circuits(result)

            sheet.result_json = result
            sheet.systems_json = circuits
            sheet.status = "detected"
            sheet.width = result.get("w")
            sheet.height = result.get("h")

            job.status = "completed"
            job.progress_pct = 100
            job.step = "completed"
            job.message = "Detection completed successfully"
            job.completed_at = datetime.utcnow()
            await session.commit()

        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.message = f"Error: {str(e)}"
            sheet.status = "error"
            await session.commit()


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
    db: AsyncSession = Depends(get_db),
):
    """Trigger asynchronous P&ID digitization pipeline on a sheet."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    target_dpi = dpi or sheet.dpi or 350
    target_rot = rot if rot is not None else sheet.rot or 0

    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        sheet_id=sheet_id,
        tenant_id=sheet.tenant_id,
        user_id=sheet.user_id,
        status="queued",
        progress_pct=0,
        step="queued",
        message="Job queued for processing",
    )
    db.add(job)
    sheet.status = "detecting"
    await db.commit()

    # Try dispatching to Celery task if configured, otherwise run via FastAPI BackgroundTasks
    celery_dispatched = False
    try:
        from worker.tasks import detect_sheet_task
        task = detect_sheet_task.delay(
            job_id=job_id,
            sheet_id=sheet_id,
            file_rel_path=sheet.file_path,
            dpi=target_dpi,
            rot=target_rot,
        )
        celery_dispatched = True
    except Exception:
        celery_dispatched = False

    if not celery_dispatched:
        background_tasks.add_task(
            _run_detection_in_background,
            job_id=job_id,
            sheet_id=sheet_id,
            file_path=sheet.file_path,
            dpi=target_dpi,
            rot=target_rot,
        )

    return JobResponse(
        job_id=job_id,
        sheet_id=sheet_id,
        status="queued",
        progress_pct=0,
        step="queued",
        message="Detection job queued",
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
