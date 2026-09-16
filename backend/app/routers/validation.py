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
        # Fallback: check Redis for completed Celery task results
        try:
            import redis, json
            from ..config import settings
            r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            for key in r.keys("celery-task-meta-*"):
                raw = r.get(key)
                if not raw:
                    continue
                meta = json.loads(raw)
                data = meta.get("result")
                if isinstance(data, dict) and data.get("sheet_id") == sheet_id and "result" in data:
                    sheet.result_json = data["result"]
                    sheet.systems_json = data.get("systems") or GroupingService.compute_circuits(data["result"])
                    sheet.status = "detected"
                    sheet.width = data["result"].get("w")
                    sheet.height = data["result"].get("h")
                    await db.commit()
                    break
        except Exception as rec_err:
            print(f"[ValidationRouter] Redis recovery fallback error: {rec_err}")

    if not sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet has not been processed yet")

    report = GroupingService.validate(sheet.result_json)
    return report
