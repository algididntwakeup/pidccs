import os
import uuid
from typing import List, Optional
import cv2
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..schemas.project import ProjectCreate, ProjectResponse, SheetResponse
from ..services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


class TraceRegionRequest(BaseModel):
    x1: float = Field(..., ge=0)
    y1: float = Field(..., ge=0)
    x2: float = Field(..., ge=0)
    y2: float = Field(..., ge=0)
    sheet_id: Optional[str] = None
    replace_existing: bool = False


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


@router.post("/{project_id}/trace-region")
async def trace_region(
    project_id: str,
    payload: TraceRegionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Re-trace a selected image region with relaxed short-line detection."""
    from ..adapters.storage import LocalStorageAdapter
    from ..adapters.pdf_renderer import load_drawing_image
    from ..config import settings
    from ..services.export_service import ExportService
    from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer

    project = await ProjectService.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    sheet = await ProjectService.get_sheet(db, payload.sheet_id) if payload.sheet_id else (project.sheets[0] if project.sheets else None)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found in this project")

    storage_adapter = LocalStorageAdapter(settings.STORAGE_DIR)
    image_path = storage_adapter.get_file_path(sheet.file_path)
    # Reuse the disk-cached full-resolution base render so repeated ROI re-scans
    # do not re-rasterize the entire drawing from PDF on every request.
    if os.path.exists(image_path):
        img = ExportService._cached_base_image({
            "image_path": image_path,
            "dpi": sheet.dpi or 350,
            "rot": getattr(sheet, "rot", 0),
        })
    else:
        img = load_drawing_image(image_path, dpi=sheet.dpi or 350)
    height, width = img.shape[:2]
    x1, y1 = max(0, min(int(payload.x1), width - 1)), max(0, min(int(payload.y1), height - 1))
    x2, y2 = max(x1 + 1, min(int(payload.x2), width)), max(y1 + 1, min(int(payload.y2), height))

    # --- Box Trace / ROI re-scan stabilization ---------------------------------------
    # 1. Binarize the FULL image ONCE (same adaptive params as the page-level tracer).
    #    We must NOT re-threshold the crop: a sub-image dominated by white background
    #    starves adaptive thresholding of local statistics, dropping thin pipe strokes.
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    full_binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 6
    )

    # 2. Expand the selection by 20px on every side (anti-clipping). Lines touching the box
    #    edge would otherwise be severed by skeletonization; the padded crop keeps them whole.
    pad = 20
    px1, py1 = max(0, x1 - pad), max(0, y1 - pad)
    px2, py2 = min(width, x2 + pad), min(height, y2 + pad)
    binary_crop = full_binary[py1:py2, px1:px2]
    bgr_crop = img[py1:py2, px1:px2]

    # 3. Trace on the padded crop using the pre-computed binary (roi=True adds a small
    #    MORPH_CLOSE to bridge OCR-masking holes) and adaptive min length.
    tracer = SkeletonLineTracer(min_length_px=6, suppress_floating_stubs=False)
    local_runs = tracer.trace(
        bgr_crop, dpi=sheet.dpi or 350, detections=[], furniture=[],
        binary_img=binary_crop, roi=True,
    )

    new_runs = []
    for run in local_runs:
        record = dict(run) if isinstance(run, dict) else {
            "points": run.points,
            "axis": run.axis,
            "underline": getattr(run, "underline", False),
        }
        # Offset local (padded-crop) coordinates back to global image space.
        points = [[int(p[0]) + px1, int(p[1]) + py1] for p in record.get("points", [])]
        if len(points) < 2:
            continue
        new_runs.append({
            **record,
            "id": f"rescan-run-{uuid.uuid4().hex[:12]}",
            "points": points,
            "x1": min(p[0] for p in points), "y1": min(p[1] for p in points),
            "x2": max(p[0] for p in points), "y2": max(p[1] for p in points),
            "color": "#2563EB", "manual": False, "source": "rescan",
        })

    result = dict(sheet.result_json or {})
    current_runs = list(result.get("runs", []))

    if payload.replace_existing:
        # Option C: Replace existing runs that fall predominantly within the ROI box
        retained_runs = []
        for r in current_runs:
            rx1 = r.get("x1", 0)
            ry1 = r.get("y1", 0)
            rx2 = r.get("x2", 0)
            ry2 = r.get("y2", 0)
            # Check if run center falls within ROI
            rcx = (rx1 + rx2) / 2
            rcy = (ry1 + ry2) / 2
            if x1 <= rcx <= x2 and y1 <= rcy <= y2:
                continue
            retained_runs.append(r)
        current_runs = retained_runs

    result["runs"] = current_runs + new_runs
    sheet.result_json = result
    await db.commit()
    return {
        "status": "success",
        "new_runs": new_runs,
        "total_runs": len(result["runs"]),
        "result": result,
    }


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


@router.patch("/{project_id}/sheets/{sheet_id}/runs/{run_idx}/points")
async def update_sheet_run_points(
    project_id: str,
    sheet_id: str,
    run_idx: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update titik koordinat vertex untuk satu segmen polyline pipa."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json or "runs" not in sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet result has no traced runs")

    res = dict(sheet.result_json)
    runs = res.get("runs", [])
    if run_idx < 0 or run_idx >= len(runs):
        raise HTTPException(status_code=400, detail=f"Run index {run_idx} out of range")

    pts = payload.get("points", [])
    if not pts or len(pts) < 2:
        raise HTTPException(status_code=400, detail="Run must have at least 2 points")

    runs[run_idx]["points"] = pts
    runs[run_idx]["x1"] = min(p[0] for p in pts)
    runs[run_idx]["y1"] = min(p[1] for p in pts)
    runs[run_idx]["x2"] = max(p[0] for p in pts)
    runs[run_idx]["y2"] = max(p[1] for p in pts)
    runs[run_idx]["manual"] = True

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


@router.delete("/{project_id}/sheets/{sheet_id}/runs/{run_idx}")
async def delete_sheet_run(
    project_id: str,
    sheet_id: str,
    run_idx: int,
    db: AsyncSession = Depends(get_db),
):
    """Hapus satu segmen polyline pipa dari runs dan re-index piping_ids."""
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


@router.post("/{project_id}/sheets/{sheet_id}/runs/batch-delete")
async def batch_delete_sheet_runs(
    project_id: str,
    sheet_id: str,
    payload: dict,
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

    indices_to_delete = sorted(set(ri for ri in payload.get("run_idxs", []) if 0 <= ri < n_runs), reverse=True)
    if not indices_to_delete:
        return {"status": "success", "deleted_count": 0, "result": sheet.result_json}

    deleted_set = set(indices_to_delete)

    for ri in indices_to_delete:
        runs.pop(ri)

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


@router.patch("/{project_id}/sheets/{sheet_id}/runs/{run_idx}/label")
async def update_sheet_run_label(
    project_id: str,
    sheet_id: str,
    run_idx: int,
    payload: dict,
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

    new_label = payload.get("label", "")
    runs[run_idx]["label"] = new_label
    runs[run_idx]["manual"] = True
    res["runs"] = runs
    sheet.result_json = res
    await db.commit()

    return {
        "status": "success",
        "run_idx": run_idx,
        "label": new_label,
        "result": sheet.result_json,
    }
