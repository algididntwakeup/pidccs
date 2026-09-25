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


class UpdateRunLabelRequest(BaseModel):
    label: str = Field(..., description="Label or tag for the pipe run")


class BatchDeleteRunsRequest(BaseModel):
    run_idxs: List[int] = Field(..., description="List of run indices to delete")


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


@router.delete("/runs/{run_idx}")
async def delete_run(
    project_id: str,
    sheet_id: str,
    run_idx: int,
    db: AsyncSession = Depends(get_db),
):
    """Hapus satu segmen polyline pipa dari daftar runs dan re-index piping_ids."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json or "runs" not in sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(sheet.result_json)
    runs = list(res.get("runs", []))
    pids = list(res.get("piping_ids", []))

    if run_idx < 0 or run_idx >= len(runs):
        raise HTTPException(status_code=400, detail=f"Run index {run_idx} out of range")

    deleted_run = runs.pop(run_idx)

    # Re-index piping IDs
    new_pids = []
    for p in pids:
        p_rec = dict(p)
        r_idx = p_rec.get("run_idx", -1)
        if r_idx == run_idx:
            p_rec["run_idx"] = -1
            p_rec["state"] = "none"
        elif r_idx > run_idx:
            p_rec["run_idx"] = r_idx - 1

        extra = p_rec.get("extra_runs", [])
        if extra:
            new_extra = []
            for er in extra:
                if er == run_idx:
                    continue
                new_extra.append(er - 1 if er > run_idx else er)
            p_rec["extra_runs"] = new_extra

        new_pids.append(p_rec)

    # Re-assign standard sequential id: run-0, run-1, ...
    for idx, r in enumerate(runs):
        r["id"] = f"run-{idx}"

    res["runs"] = runs
    res["piping_ids"] = new_pids
    sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "deleted_run_idx": run_idx,
        "deleted_run": deleted_run,
        "total_runs": len(runs),
        "result": sheet.result_json,
    }


@router.post("/runs/batch-delete")
async def batch_delete_runs(
    project_id: str,
    sheet_id: str,
    payload: BatchDeleteRunsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Hapus beberapa segmen polyline pipa sekaligus secara massal."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json or "runs" not in sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(sheet.result_json)
    runs = list(res.get("runs", []))
    pids = list(res.get("piping_ids", []))
    n_runs = len(runs)

    indices_to_delete = sorted(set(ri for ri in payload.run_idxs if 0 <= ri < n_runs), reverse=True)
    if not indices_to_delete:
        return {"status": "success", "deleted_count": 0, "result": sheet.result_json}

    deleted_set = set(indices_to_delete)

    # Delete runs from highest index to lowest
    for ri in indices_to_delete:
        runs.pop(ri)

    # Map old run_idx to new run_idx
    old_to_new = {}
    new_counter = 0
    for old_i in range(n_runs):
        if old_i in deleted_set:
            old_to_new[old_i] = -1
        else:
            old_to_new[old_i] = new_counter
            new_counter += 1

    new_pids = []
    for p in pids:
        p_rec = dict(p)
        r_idx = p_rec.get("run_idx", -1)
        if r_idx in old_to_new:
            new_idx = old_to_new[r_idx]
            p_rec["run_idx"] = new_idx
            if new_idx == -1:
                p_rec["state"] = "none"

        extra = p_rec.get("extra_runs", [])
        if extra:
            new_extra = [old_to_new[er] for er in extra if er in old_to_new and old_to_new[er] != -1]
            p_rec["extra_runs"] = new_extra

        new_pids.append(p_rec)

    for idx, r in enumerate(runs):
        r["id"] = f"run-{idx}"

    res["runs"] = runs
    res["piping_ids"] = new_pids
    sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "deleted_count": len(indices_to_delete),
        "total_runs": len(runs),
        "result": sheet.result_json,
    }


class UpdateRunPointsRequest(BaseModel):
    points: List[List[float]] = Field(..., description="List of [x, y] coordinates for polyline vertices")


@router.patch("/runs/{run_idx}/points")
async def update_run_points(
    project_id: str,
    sheet_id: str,
    run_idx: int,
    payload: UpdateRunPointsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update koordinat titik-titik vertex untuk satu segmen polyline pipa (misal setelah drag control point)."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json or "runs" not in sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(sheet.result_json)
    runs = res.get("runs", [])
    if run_idx < 0 or run_idx >= len(runs):
        raise HTTPException(status_code=400, detail=f"Run index {run_idx} out of range")

    pts = payload.points
    if not pts or len(pts) < 2:
        raise HTTPException(status_code=400, detail="Run must have at least 2 points")

    runs[run_idx]["points"] = pts
    runs[run_idx]["x1"] = min(p[0] for p in pts)
    runs[run_idx]["y1"] = min(p[1] for p in pts)
    runs[run_idx]["x2"] = max(p[0] for p in pts)
    runs[run_idx]["y2"] = max(p[1] for p in pts)
    runs[run_idx]["manual"] = True

    # Recalculate axis orientation
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if max(ys) - min(ys) <= 4:
        runs[run_idx]["axis"] = "h"
    elif max(xs) - min(xs) <= 4:
        runs[run_idx]["axis"] = "v"
    else:
        runs[run_idx]["axis"] = "poly"

    res["runs"] = runs
    sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "run_idx": run_idx,
        "points": pts,
        "result": sheet.result_json,
    }


@router.patch("/runs/{run_idx}/label")
async def update_run_label(
    project_id: str,
    sheet_id: str,
    run_idx: int,
    payload: UpdateRunLabelRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update label / tag untuk satu segmen polyline pipa."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json or "runs" not in sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(sheet.result_json)
    runs = res.get("runs", [])
    if run_idx < 0 or run_idx >= len(runs):
        raise HTTPException(status_code=400, detail=f"Run index {run_idx} out of range")

    trimmed = (payload.label or "").strip()
    runs[run_idx]["label"] = trimmed
    runs[run_idx]["pid"] = trimmed
    runs[run_idx]["manual"] = True
    res["runs"] = runs

    pids = list(res.get("piping_ids", []))
    if not trimmed:
        for p in pids:
            if p.get("run_idx") == run_idx:
                p["run_idx"] = -1
                p["state"] = "none"
            extra = p.get("extra_runs", [])
            if run_idx in extra:
                p["extra_runs"] = [er for er in extra if er != run_idx]
    else:
        try:
            from pidcorr.piping_id import parse_tokens
            toks = parse_tokens(trimmed)
        except Exception:
            toks = {}

        target_run = runs[run_idx]
        found = False
        for p in pids:
            if p.get("run_idx") == run_idx:
                p["pid"] = trimmed
                p["manual"] = True
                p["state"] = "manual"
                for k, v in toks.items():
                    if v:
                        p[k] = v
                found = True
                break

        if not found:
            for p in pids:
                if (p.get("pid") or "").strip().lower() == trimmed.lower():
                    if p.get("run_idx", -1) < 0:
                        p["run_idx"] = run_idx
                        p["state"] = "attached"
                        p["manual"] = True
                        found = True
                        break
                    else:
                        extra = set(p.get("extra_runs", []))
                        extra.add(run_idx)
                        p["extra_runs"] = [er for er in extra if er != p.get("run_idx")]
                        p["manual"] = True
                        found = True
                        break

        if not found:
            new_pid = {
                "pid": trimmed,
                "x1": float(target_run.get("x1", 0)),
                "y1": float(target_run.get("y1", 0)),
                "x2": float(target_run.get("x2", 0)),
                "y2": float(target_run.get("y2", 0)),
                "unit": toks.get("unit", ""),
                "size": toks.get("size", ""),
                "fluid": toks.get("fluid", ""),
                "pclass": toks.get("pclass", ""),
                "seq": toks.get("seq", ""),
                "conf": 100,
                "run_idx": run_idx,
                "extra_runs": [],
                "state": "manual",
                "manual": True,
            }
            pids.append(new_pid)

    res["piping_ids"] = pids
    sheet.result_json = res

    try:
        updated_circuits = GroupingService.compute_circuits(res)
        sheet.systems_json = updated_circuits
    except Exception:
        pass

    await db.commit()

    return {
        "status": "success",
        "run_idx": run_idx,
        "label": trimmed,
        "result": sheet.result_json,
    }

