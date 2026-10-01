import logging
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import require_superadmin
from ..db.session import get_db
from ..models.ground_truth_log import GroundTruthLog
from ..models.job import Job
from ..models.project import Project
from ..models.sheet import Sheet
from ..models.user import User
from ..schemas.admin import (
    AdminActivitySummary,
    AdminJobErrorSummary,
    AdminMonitoringResponse,
    AdminProjectSummary,
    AdminSheetSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])


@router.get("/monitoring", response_model=AdminMonitoringResponse)
async def get_monitoring_snapshot(
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> AdminMonitoringResponse:
    """Read-only global project, marking-activity, and failed-job snapshot."""
    logger.info("Admin monitoring snapshot accessed by user_id=%s", current_user.id)

    project_rows = (await db.execute(
        select(Project, User)
        .outerjoin(User, Project.owner_id == User.id)
        .order_by(Project.created_at.desc())
    )).all()
    project_ids = [project.id for project, _ in project_rows]
    sheets_by_project: dict[str, List[AdminSheetSummary]] = {project_id: [] for project_id in project_ids}
    if project_ids:
        sheets = (await db.scalars(
            select(Sheet).where(Sheet.project_id.in_(project_ids)).order_by(Sheet.created_at.asc())
        )).all()
        for sheet in sheets:
            sheets_by_project[sheet.project_id].append(AdminSheetSummary(
                id=sheet.id, filename=sheet.filename, sheet_number=sheet.sheet_number or "",
            ))

    projects = [AdminProjectSummary(
        id=project.id,
        bima_project_id=project.bima_project_id,
        name=project.name,
        owner_name=owner.full_name if owner else None,
        owner_email=owner.email if owner else None,
        sheets=sheets_by_project[project.id],
    ) for project, owner in project_rows]

    activity_rows = (await db.execute(
        select(GroundTruthLog, User, Project, Sheet)
        .join(User, User.id == GroundTruthLog.user_id)
        .join(Project, Project.id == GroundTruthLog.project_id)
        .outerjoin(Sheet, Sheet.id == GroundTruthLog.sheet_id)
        .order_by(GroundTruthLog.timestamp.desc())
        .limit(limit)
    )).all()
    activities = [AdminActivitySummary(
        id=log.id,
        user_id=user.id,
        user_name=user.full_name,
        user_email=user.email,
        project_id=project.id,
        project_name=project.name,
        sheet_id=sheet.id if sheet else None,
        sheet_name=(sheet.filename or sheet.sheet_number) if sheet else None,
        action_type=log.action_type,
        payload_json=log.payload_json or {},
        timestamp=log.timestamp,
    ) for log, user, project, sheet in activity_rows]

    error_rows = (await db.execute(
        select(Job, Project, Sheet)
        .join(Sheet, Sheet.id == Job.sheet_id)
        .join(Project, Project.id == Sheet.project_id)
        .where(or_(Job.error.is_not(None), Job.status.in_(["failed", "error"])))
        .order_by(Job.created_at.desc())
        .limit(limit)
    )).all()
    recent_errors = [AdminJobErrorSummary(
        id=job.id,
        project_id=project.id,
        project_name=project.name,
        sheet_id=sheet.id,
        sheet_name=sheet.filename,
        status=job.status,
        message=job.message or "",
        error=job.error or "",
        created_at=job.created_at,
    ) for job, project, sheet in error_rows]

    return AdminMonitoringResponse(
        projects=projects,
        activities=activities,
        recent_errors=recent_errors,
    )
