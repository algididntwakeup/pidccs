from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..schemas.validation import ValidationReport
from ..services.project_service import ProjectService
from ..services.grouping_service import GroupingService

router = APIRouter(prefix="/projects/{project_id}/sheets/{sheet_id}/validate", tags=["validation"])


@router.get("", response_model=ValidationReport)
async def validate_sheet(
    project_id: str,
    sheet_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Run automated consistency and confidence checks on the digitization and grouping."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet has not been processed yet")

    report = GroupingService.validate(sheet.result_json)
    return report
