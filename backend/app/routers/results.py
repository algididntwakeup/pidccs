from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..schemas.result import DigitizationResult
from ..services.project_service import ProjectService
from ..services.grouping_service import GroupingService

router = APIRouter(prefix="/projects/{project_id}/sheets/{sheet_id}/result", tags=["results"])


@router.get("", response_model=DigitizationResult)
async def get_sheet_result(
    project_id: str,
    sheet_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the full DigitizationResult JSON for this sheet."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json:
        # Fallback: check Redis for completed Celery task results
        recovered = False
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
                    recovered = True
                    break
        except Exception as rec_err:
            print(f"[ResultsRouter] Redis recovery fallback error: {rec_err}")

        if not recovered or not sheet.result_json:
            raise HTTPException(
                status_code=400,
                detail="Sheet has not been processed yet. Call /detect first."
            )

    return sheet.result_json


@router.patch("", response_model=DigitizationResult)
async def patch_sheet_result(
    project_id: str,
    sheet_id: str,
    updated_result: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Update digitization result with engineer manual edits (human-in-the-loop).

    Accepts modified symbols, piping_ids, runs, or conn_points.
    Automatically re-evaluates systemization & circuitization.
    """
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    # Merge / update result
    sheet.result_json = updated_result

    # Re-evaluate API RP 970 circuits with the user edits
    try:
        updated_circuits = GroupingService.compute_circuits(updated_result)
        sheet.systems_json = updated_circuits
    except Exception:
        pass

    await db.commit()
    return sheet.result_json
