import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_user
from ..db.session import AsyncSessionLocal, get_db
from ..models.ground_truth_log import GroundTruthLog
from ..models.project import Project
from ..models.sheet import Sheet
from ..models.user import User
from ..models.project_assignment import ProjectAssignment
from ..schemas.tracking import GroundTruthLogAccepted, GroundTruthLogCreate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tracking", tags=["tracking"])


async def _persist_log(user_id: str, payload: GroundTruthLogCreate) -> None:
    try:
        async with AsyncSessionLocal() as db:
            db.add(GroundTruthLog(
                user_id=user_id,
                project_id=payload.project_id,
                sheet_id=payload.sheet_id,
                action_type=payload.action_type,
                payload_json=payload.payload_json,
            ))
            await db.commit()
    except Exception:
        logger.exception("Failed to persist ground-truth event")


@router.post("/log", response_model=GroundTruthLogAccepted, status_code=status.HTTP_202_ACCEPTED)
async def log_ground_truth(
    payload: GroundTruthLogCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroundTruthLogAccepted:
    assigned_project_ids = select(ProjectAssignment.project_id).where(
        ProjectAssignment.user_id == current_user.id
    )
    project_query = select(Project).where(Project.id == payload.project_id)
    if current_user.role.strip().upper() != "SUPERADMIN":
        project_query = project_query.where(
            or_(Project.owner_id == current_user.id, Project.id.in_(assigned_project_ids))
        )
    project = await db.scalar(project_query)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.sheet_id is not None:
        sheet = await db.scalar(select(Sheet).where(
            Sheet.id == payload.sheet_id,
            Sheet.project_id == project.id,
        ))
        if sheet is None:
            raise HTTPException(status_code=404, detail="Sheet not found in this project")

    background_tasks.add_task(_persist_log, current_user.id, payload)
    return GroundTruthLogAccepted()
