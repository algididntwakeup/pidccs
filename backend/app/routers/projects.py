import os
import uuid
from typing import List, Optional
import cv2
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.session import get_db
from ..schemas.project import ProjectCreate, ProjectResponse, SheetResponse
from ..services.project_service import ProjectService
from ..services.detection_service import queue_sheet_detection, dispatch_detection_job

router = APIRouter(prefix="/projects", tags=["projects"])


class TraceRegionRequest(BaseModel):
    x1: float = Field(..., ge=0)
    y1: float = Field(..., ge=0)
    x2: float = Field(..., ge=0)
    y2: float = Field(..., ge=0)
    sheet_id: Optional[str] = None

class TraceClickRequest(BaseModel):
    """Satu klik pada garis pipa (koordinat ruang citra = ruang `runs`)."""
    x: float = Field(..., ge=0)
    y: float = Field(..., ge=0)
    radius: float = Field(15.0, gt=0, le=300)


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


@router.post("/{project_id}/sheets", response_model=List[SheetResponse], status_code=status.HTTP_201_CREATED)
async def upload_sheet(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    dpi: int = Form(350),
    sheet_number: str = Form(""),
    tenant_id: str = Form("default_tenant"),
    user_id: str = Form("default_user"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a P&ID drawing (PDF, PNG, JPG) to a project.

    PDF multi-halaman dipecah di sini: satu `Sheet` per halaman, masing-masing
    menunjuk file PDF 1 halaman sendiri. PDF 1 halaman / PNG / JPG tetap 1 Sheet.
    Response selalu berupa daftar agar pemanggil tidak perlu tahu jumlah halaman.

    Setiap sheet langsung di-Fast-Trace (`lines_only`) di background kecuali
    `AUTO_TRACE_ON_UPLOAD=false` atau Celery berjalan eager (`.delay()` inline akan
    memblokir respons upload sampai tracing selesai).
    """
    project = await ProjectService.get_project(db=db, project_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    sheets = await ProjectService.add_sheets(
        db=db,
        project_id=project_id,
        file=file,
        dpi=dpi,
        sheet_number=sheet_number,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    if settings.AUTO_TRACE_ON_UPLOAD and not settings.CELERY_TASK_ALWAYS_EAGER:
        jobs = [(queue_sheet_detection(s, mode="lines_only"), s) for s in sheets]
        # Baris Job harus sudah ter-commit sebelum task dikirim, kalau tidak worker
        # bisa memproses task untuk Job yang belum terlihat di DB.
        await db.commit()
        for job, sheet in jobs:
            dispatch_detection_job(
                job, sheet, mode="lines_only", dpi=sheet.dpi, rot=sheet.rot or 0,
                background_tasks=background_tasks,
            )

    return sheets

@router.get("/{project_id}/sheets", response_model=List[SheetResponse])
async def list_sheets(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List every sheet (page) in a project — dipakai grid thumbnail Folder View.

    Urut `created_at` (satu batch upload selalu menaik) dengan tiebreak numerik pada
    nomor halaman, supaya `1/12, 2/12, ... 10/12` tidak terurut leksikografis.
    """
    project = await ProjectService.get_project(db=db, project_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    def _order(sheet):
        head = (sheet.sheet_number or "").split("/")[0]
        return (sheet.created_at, int(head) if head.isdigit() else 0)

    return sorted(project.sheets, key=_order)


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
    try:
        from pidcorr.lines import stitch_region_runs
    except ImportError:
        def stitch_region_runs(existing_runs, new_runs, bounds_roi, snap_radius=18.0):
            return existing_runs, new_runs, []

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
    binary_crop = full_binary[py1:py2, px1:px2].copy()
    bgr_crop = img[py1:py2, px1:px2]

    # --- Bagian 3: Proteksi Simbol Valve & Instrumen (Anti-Nabrak) ------------------
    # Ambil deteksi simbol eksisting pada sheet yang beririsan dengan area crop ROI.
    # Lakukan blackout (masking hitam) pada bodi simbol valve & instrument pada binary_crop
    # sebelum skeletisasi dijalankan agar pipa berhenti rapi di port valve, bukan menembus bodinya.
    result = dict(sheet.result_json or {})
    existing_symbols = result.get("symbols", [])
    local_dets = []
    for s in existing_symbols:
        sx1 = float(s.get("x1", 0))
        sy1 = float(s.get("y1", 0))
        sx2 = float(s.get("x2", 0))
        sy2 = float(s.get("y2", 0))
        if sx2 > px1 and sx1 < px2 and sy2 > py1 and sy1 < py2:
            lx1 = max(0, int(round(sx1 - px1)))
            ly1 = max(0, int(round(sy1 - py1)))
            lx2 = min(px2 - px1, int(round(sx2 - px1)))
            ly2 = min(py2 - py1, int(round(sy2 - py1)))
            coarse = s.get("coarse", "")
            if coarse in ("valve", "instrument"):
                if lx2 > lx1 and ly2 > ly1:
                    binary_crop[ly1:ly2, lx1:lx2] = 0
            loc_s = dict(s)
            loc_s["x1"] = lx1
            loc_s["y1"] = ly1
            loc_s["x2"] = lx2
            loc_s["y2"] = ly2
            local_dets.append(loc_s)

    # 3. Trace on the padded crop using the pre-computed binary (roi=True adds a small
    #    MORPH_CLOSE to bridge OCR-masking holes) and adaptive min length.
    tracer = SkeletonLineTracer(min_length_px=6, suppress_floating_stubs=False)
    local_runs = tracer.trace(
        bgr_crop, dpi=sheet.dpi or 350, detections=local_dets, furniture=[],
        binary_img=binary_crop, roi=True,
    )

    new_runs = []
    for run in local_runs:
        record = dict(run) if isinstance(run, dict) else {
            "points": run.points,
            "axis": run.axis,
            "underline": getattr(run, "underline", False),
            "equipment_outline": getattr(run, "equipment_outline", False),
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
            "color": "#2563EB", "manual": True, "source": "rescan",
            "label": record.get("label", ""),
            "pid": record.get("pid", ""),
        })

    # --- Bagian 2: Localized OCR & Auto-Labeling di Box Trace ------------------------
    import math
    from ..services.detection_service import get_orchestrator
    from pidcorr.implementations.regex_parser import RegexPipingIDParser
    from pidcorr.piping_id import parse_tokens, ocr_region

    ocr_tags = []
    roi_bgr = img[y1:y2, x1:x2]
    if roi_bgr.size > 0 and roi_bgr.shape[0] >= 10 and roi_bgr.shape[1] >= 10:
        try:
            orch = get_orchestrator()
            extractor = orch.extractor
            parser = RegexPipingIDParser()
            pids, tokens = extractor.extract(roi_bgr)
            for p in (pids or []):
                tag = p.pid if hasattr(p, "pid") else p.get("pid", "")
                if tag:
                    tx1 = float(p.x1 if hasattr(p, "x1") else p.get("x1", 0)) + x1
                    ty1 = float(p.y1 if hasattr(p, "y1") else p.get("y1", 0)) + y1
                    tx2 = float(p.x2 if hasattr(p, "x2") else p.get("x2", 0)) + x1
                    ty2 = float(p.y2 if hasattr(p, "y2") else p.get("y2", 0)) + y1
                    parsed = parser.parse(tag) or parse_tokens(tag)
                    ocr_tags.append({"tag": tag, "x1": tx1, "y1": ty1, "x2": tx2, "y2": ty2, "parsed": parsed})

            if not ocr_tags and tokens:
                for tok in tokens:
                    raw_text = tok.get("text") or tok.get("t") or ""
                    parsed = parser.parse(raw_text)
                    if parsed and any(parsed.values()):
                        tx1 = float(tok.get("x1", 0)) + x1
                        ty1 = float(tok.get("y1", 0)) + y1
                        tx2 = float(tok.get("x2", 0)) + x1
                        ty2 = float(tok.get("y2", 0)) + y1
                        ocr_tags.append({"tag": raw_text, "x1": tx1, "y1": ty1, "x2": tx2, "y2": ty2, "parsed": parsed})

            if not ocr_tags:
                reg_text, matched = ocr_region(img, x1, y1, x2, y2)
                if matched and reg_text:
                    parsed = parser.parse(reg_text) or parse_tokens(reg_text)
                    ocr_tags.append({"tag": reg_text, "x1": float(x1), "y1": float(y1), "x2": float(x2), "y2": float(y2), "parsed": parsed})
        except Exception:
            pass

    # Pasangkan tag OCR ke new_runs yang lokasinya paling dekat dengan kotak teks
    if ocr_tags and new_runs:
        def _dist_to_run(pt, r):
            pts = r.get("points", [])
            if len(pts) < 2:
                return float("inf")
            px, py = pt
            min_d = float("inf")
            for i in range(len(pts) - 1):
                x0, y0 = pts[i]
                x1_s, y1_s = pts[i + 1]
                dx, dy = x1_s - x0, y1_s - y0
                l2 = dx * dx + dy * dy
                if l2 == 0:
                    d = math.hypot(px - x0, py - y0)
                else:
                    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / l2))
                    proj_x = x0 + t * dx
                    proj_y = y0 + t * dy
                    d = math.hypot(px - proj_x, py - proj_y)
                if d < min_d:
                    min_d = d
            return min_d

        for ot in ocr_tags:
            tcx = (ot["x1"] + ot["x2"]) / 2.0
            tcy = (ot["y1"] + ot["y2"]) / 2.0
            best_r = min(new_runs, key=lambda r: _dist_to_run((tcx, tcy), r))
            best_r["label"] = ot["tag"]
            best_r["pid"] = ot["tag"]
            if ot["parsed"].get("fluid"):
                best_r["fluid"] = ot["parsed"]["fluid"]

    current_runs = list(result.get("runs", []))

    # --- Smart Box-Trace stitching ---------------------------------------------------
    # Absorb the freshly traced region path into existing runs instead of always
    # appending a duplicate: extend a touched pipe (1-to-1 / Scenario A), bridge two
    # severed pipes (1-to-2 / Scenario B, dropping the merged target), or keep the path
    # as an independent new run when it touches nothing (Scenario C). The old rigid
    # "Replace vs Append" choice is gone — the backend decides automatically.
    current_runs, new_runs, consumed_ids = stitch_region_runs(
        current_runs, new_runs, bounds_roi=(x1, y1, x2, y2), snap_radius=18.0,
    )
    stitched_runs_count = len(consumed_ids)

    all_runs = current_runs + new_runs

    # Sinkronisasi tag hasil OCR ke daftar piping_ids pada sheet agar muncul di sidebar
    current_pids = list(result.get("piping_ids", []))
    for ot in ocr_tags:
        tag = ot["tag"]
        assigned_idx = -1
        for idx, r in enumerate(all_runs):
            if r.get("label") == tag or r.get("pid") == tag:
                assigned_idx = idx
                break

        existing_pid = next((p for p in current_pids if p.get("pid") == tag), None)
        if not existing_pid:
            new_pid = {
                "pid": tag,
                "x1": ot["x1"],
                "y1": ot["y1"],
                "x2": ot["x2"],
                "y2": ot["y2"],
                "conf": 1,
                "run_idx": assigned_idx,
                "extra_runs": [],
                "state": "attached" if assigned_idx >= 0 else "none",
                "manual": False,
                "unit": ot["parsed"].get("unit", ""),
                "size": ot["parsed"].get("size", ""),
                "fluid": ot["parsed"].get("fluid", ""),
                "pclass": ot["parsed"].get("pclass", ""),
                "seq": ot["parsed"].get("seq", ""),
            }
            current_pids.append(new_pid)
        else:
            if existing_pid.get("run_idx", -1) == -1 and assigned_idx >= 0:
                existing_pid["run_idx"] = assigned_idx
                existing_pid["state"] = "attached"

    result["runs"] = all_runs
    result["piping_ids"] = current_pids
    sheet.result_json = result
    await db.commit()
    return {
        "status": "success",
        "new_runs": new_runs,
        "stitched_runs_count": stitched_runs_count,
        "stitched": stitched_runs_count,
        "total_runs": len(result["runs"]),
        "result": result,
    }

@router.post("/{project_id}/sheets/{sheet_id}/trace-click")
async def trace_click(
    project_id: str,
    sheet_id: str,
    payload: TraceClickRequest,
    db: AsyncSession = Depends(get_db),
):
    """Magic Wand (HITL): ambil SATU garis vektor CAD terdekat dari titik klik.

    Tanpa OCR, tanpa YOLO, tanpa routing — murni geometri: indeks vektor PDF
    (di-prewarm saat sheet dibuka) -> `nearest_run_at` -> dedupe -> append run.
    """
    from ..adapters.storage import LocalStorageAdapter
    from ..config import settings
    from pidcorr.lines import get_vector_index, nearest_run_at, find_duplicate_run

    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found in this project")

    result = dict(sheet.result_json or {})
    if not result:
        raise HTTPException(
            status_code=400,
            detail="Sheet belum punya hasil tracing — jalankan Detect P&ID dulu",
        )

    if not str(sheet.filename or sheet.file_path).lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Magic Wand hanya bekerja pada sheet PDF vektor. "
                   "Untuk PNG/JPG gunakan Box Trace atau Manual Pen.",
        )

    image_path = LocalStorageAdapter(settings.STORAGE_DIR).get_file_path(sheet.file_path)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="File gambar sheet tidak ditemukan di storage")

    dpi = float(result.get("dpi") or sheet.dpi or 350)
    rot = int(result.get("rot") or 0)
    index = get_vector_index(image_path, dpi=dpi, rot=rot)
    runs = list(result.get("runs", []))

    cand, dist = nearest_run_at(index, float(payload.x), float(payload.y),
                               radius=float(payload.radius))
    if cand is None:
        return {
            "status": "success", "added": False, "reason": "none",
            "distance": None, "total_runs": len(runs), "result": result,
        }

    dup = find_duplicate_run(runs, cand)
    if dup is not None:
        return {
            "status": "success", "added": False, "reason": "duplicate",
            "run_idx": dup, "distance": round(dist, 2),
            "total_runs": len(runs), "result": result,
        }

    pts = [[int(p[0]), int(p[1])] for p in cand.points]
    new_run = {
        "id": f"wand-{uuid.uuid4().hex[:12]}",
        "points": pts,
        "axis": cand.axis if cand.axis in ("h", "v", "d", "poly") else "poly",
        "x1": min(p[0] for p in pts), "y1": min(p[1] for p in pts),
        "x2": max(p[0] for p in pts), "y2": max(p[1] for p in pts),
        "color": "#2563EB", "manual": True, "label": "",
    }
    runs.append(new_run)
    result["runs"] = runs
    sheet.result_json = result
    await db.commit()
    return {
        "status": "success", "added": True, "reason": None,
        "run_idx": len(runs) - 1, "run": new_run, "distance": round(dist, 2),
        "total_runs": len(runs), "result": result,
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
