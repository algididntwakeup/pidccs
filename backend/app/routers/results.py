from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..schemas.result import DigitizationResult
from ..services.project_service import ProjectService
from ..services.grouping_service import GroupingService
from pidcorr.lines import split_poly_run

router = APIRouter(prefix="/projects/{project_id}/sheets/{sheet_id}/result", tags=["results"])


class SplitRunRequest(BaseModel):
    x: float = Field(..., description="X coordinate of cut point")
    y: float = Field(..., description="Y coordinate of cut point")


class UpdateRunColorRequest(BaseModel):
    color: str = Field(..., description="Hex color code (e.g. #2563EB, #DC2626)")


class BatchUpdateRunColorsRequest(BaseModel):
    run_idxs: List[int] = Field(..., description="List of run indices to update")
    color: str = Field(..., description="Hex color code to apply")


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


@router.post("/runs/{run_idx}/split")
async def split_run(
    project_id: str,
    sheet_id: str,
    run_idx: int,
    payload: SplitRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """Pecah satu polyline run pada koordinat (x, y) menjadi dua PipeRun terpisah."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json or "runs" not in sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(sheet.result_json)
    runs = res.get("runs", [])
    pids = res.get("piping_ids", [])

    try:
        new_runs, new_pids, run_a, run_b, new_idx = split_poly_run(
            runs=runs,
            run_idx=run_idx,
            split_x=payload.x,
            split_y=payload.y,
            piping_ids=pids,
        )
    except IndexError as ie:
        raise HTTPException(status_code=400, detail=str(ie))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    res["runs"] = new_runs
    if new_pids is not None:
        res["piping_ids"] = new_pids

    sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "split_point": [payload.x, payload.y],
        "original_run_idx": run_idx,
        "new_run_idx": new_idx,
        "run_a": run_a,
        "run_b": run_b,
        "total_runs": len(new_runs),
        "result": sheet.result_json,
    }


@router.patch("/runs/{run_idx}/color")
async def update_run_color(
    project_id: str,
    sheet_id: str,
    run_idx: int,
    payload: UpdateRunColorRequest,
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

    runs[run_idx]["color"] = payload.color
    res["runs"] = runs
    sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "run_idx": run_idx,
        "color": payload.color,
        "result": sheet.result_json,
    }


@router.patch("/runs/batch-color")
async def batch_update_run_colors(
    project_id: str,
    sheet_id: str,
    payload: BatchUpdateRunColorsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Batch update warna untuk beberapa segmen pipa sekaligus (Shift+Click multi-select)."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json or "runs" not in sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(sheet.result_json)
    runs = res.get("runs", [])
    n_runs = len(runs)

    updated_count = 0
    for ri in payload.run_idxs:
        if 0 <= ri < n_runs:
            runs[ri]["color"] = payload.color
            updated_count += 1

    res["runs"] = runs
    sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "updated_count": updated_count,
        "color": payload.color,
        "result": sheet.result_json,
    }

