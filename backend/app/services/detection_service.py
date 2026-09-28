import os
import sys
import uuid
import asyncio
from datetime import datetime
from typing import Callable, Optional, Dict, Any

# Ensure project root is in sys.path so pidcorr package can be imported
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import BackgroundTasks

from pidcorr import pipeline
from ..adapters.pdf_renderer import load_drawing_image
from ..adapters.storage import LocalStorageAdapter
from ..config import settings
from ..db.session import AsyncSessionLocal
from ..models.job import Job
from ..models.sheet import Sheet
from .grouping_service import GroupingService

storage = LocalStorageAdapter(settings.STORAGE_DIR)

# Process-level singleton: the orchestrator holds the heavy YOLO / OCR models in memory.
# Building it once per task (as before) reloaded weights from disk for every sheet, which
# is a major source of the "web app gets heavier with each P&ID opened" degradation.
_ORCHESTRATOR = None


def get_orchestrator():
    """Return the process-wide PipelineOrchestrator singleton (lazy, built once)."""
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        from pidcorr.factory import get_configured_orchestrator
        _ORCHESTRATOR = get_configured_orchestrator()
    return _ORCHESTRATOR


def _resolve_drawing_abs_path(file_rel_path: str) -> str:
    """Resolve file relative or absolute path to an existing absolute filesystem path."""
    abs_path = storage.get_file_path(file_rel_path)
    if not os.path.exists(abs_path):
        if os.path.isabs(file_rel_path) and os.path.exists(file_rel_path):
            abs_path = file_rel_path
        elif os.path.exists(os.path.join(_ROOT, file_rel_path)):
            abs_path = os.path.join(_ROOT, file_rel_path)
        else:
            raise FileNotFoundError(f"Drawing file not found at: {abs_path}")
    return abs_path


def _create_progress_reporter(progress_callback: Optional[Callable[[str, int, int, str], None]]):
    def _say(*args, **kwargs):
        if not progress_callback:
            return
        if len(args) == 2 and isinstance(args[0], (int, float)) and isinstance(args[1], (int, float)):
            current, total = int(args[0]), int(args[1])
            progress_callback("ocr_tiled", current, total, f"OCR tile {current}/{total}")
            return

        msg = str(args[0]) if args else ""
        step, current, total = "processing", 0, 100
        if msg.startswith("YOLO tile "):
            step = "symbol_detection"
            try:
                done, count = msg.rsplit(" ", 1)[-1].split("/")
                current = 65 + round(10 * int(done) / max(1, int(count)))
                total = 100
            except (ValueError, IndexError):
                current = 65
        elif "YOLO tiled detection: mulai" in msg:
            step, current = "symbol_detection", 65
        elif "Mendeteksi furniture" in msg:
            step, current = "symbol_detection", 78
        elif "Mengklasifikasikan subtype" in msg:
            step, current = "symbol_detection", 79
        elif "Subtype selesai" in msg:
            step, current = "symbol_detection", 79
        elif "YOLO tiled detection: selesai" in msg:
            step, current = "symbol_detection", 75
        elif "equipment besar" in msg:
            step, current = "symbol_detection", 77
        elif "kontur equipment" in msg:
            step, current = "symbol_detection", 76
        elif "Deteksi simbol selesai" in msg:
            step, current = "symbol_detection", 79
        elif "equipment" in msg or "YOLO" in msg:
            step, current = "symbol_detection", 65
        elif "tracing" in msg or "line" in msg:
            step, current = "line_tracing", 80
        elif "connection point" in msg:
            step, current = "spec_break", 90
        elif "selesai" in msg:
            step, current = "completed", 100
        progress_callback(step, current, total, msg)

    return _say


def execute_sheet_detection(
    file_rel_path: str,
    dpi: int = 350,
    rot: int = 0,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
    mode: str = "full",
) -> Dict[str, Any]:
    """Execute the P&ID digitization pipeline on a sheet (mode='full' or 'lines_only').

    progress_callback signature: (step: str, current: int, total: int, message: str)
    """
    abs_path = _resolve_drawing_abs_path(file_rel_path)
    _say = _create_progress_reporter(progress_callback)

    def load_image():
        _say("Memuat citra P&ID...")
        image = load_drawing_image(abs_path, dpi=dpi)
        return pipeline.rotate_bgr(image, rot) if rot != 0 else image

    # Fast Trace can use the PDF's vector geometry and page metadata directly.
    # The loader is called only if vector extraction needs a raster fallback.
    img = None if mode == "lines_only" and abs_path.lower().endswith(".pdf") else load_image()

    # Reuse the process-wide singleton so YOLO/OCR weights are loaded only once.
    orchestrator = get_orchestrator()

    result = orchestrator.run(
        img_bgr=img,
        image_path=abs_path,
        dpi=dpi,
        rot=rot,
        progress=_say,
        mode=mode,
        image_loader=load_image,
    )
    return result


def execute_sheet_enrichment(
    file_rel_path: str,
    existing_runs: list,
    existing_pids: Optional[list] = None,
    existing_symbols: Optional[list] = None,
    dpi: int = 350,
    rot: int = 0,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    """Execute AI enrichment (OCR, YOLO symbol detection, association) on previously traced sheet runs.

    progress_callback signature: (step: str, current: int, total: int, message: str)
    """
    abs_path = _resolve_drawing_abs_path(file_rel_path)
    _say = _create_progress_reporter(progress_callback)

    _say("Memuat citra P&ID untuk enrichment...")
    img = load_drawing_image(abs_path, dpi=dpi)
    if rot != 0:
        img = pipeline.rotate_bgr(img, rot)

    orchestrator = get_orchestrator()

    enrichment_result = orchestrator.run_enrichment(
        img_bgr=img,
        existing_runs=existing_runs,
        existing_pids=existing_pids,
        existing_symbols=existing_symbols,
        image_path=abs_path,
        dpi=dpi,
        rot=rot,
        progress=_say,
    )
    return enrichment_result

# ---------------------------------------------------------------------------
# Job dispatch helpers (see queue_sheet_detection below).
# ---------------------------------------------------------------------------

def queue_sheet_detection(sheet: Sheet, *, mode: str = "lines_only") -> Job:
    """Create a 'queued' Job for `sheet` and mark it 'detecting'. No commit, no dispatch."""
    job = Job(
        id=str(uuid.uuid4()),
        sheet_id=sheet.id,
        tenant_id=sheet.tenant_id,
        user_id=sheet.user_id,
        status="queued",
        progress_pct=0,
        step="queued",
        message=f"Detection job queued (mode: {mode})",
    )
    # Appending through the relationship (instead of db.add) also fills
    # SheetResponse.latest_job_id, which the frontend needs to resume the job.
    sheet.jobs.append(job)
    sheet.status = "queued"
    return job

def queue_sheet_enrichment(sheet: Sheet) -> Job:
    """Create a 'queued' enrichment Job for `sheet` and mark it 'detecting'."""
    job = Job(
        id=str(uuid.uuid4()),
        sheet_id=sheet.id,
        tenant_id=sheet.tenant_id,
        user_id=sheet.user_id,
        status="queued",
        progress_pct=0,
        step="queued",
        message="Enrichment job queued for processing",
    )
    sheet.jobs.append(job)
    sheet.status = "detecting"
    return job

def dispatch_detection_job(
    job: Job,
    sheet: Sheet,
    *,
    mode: str,
    dpi: int,
    rot: int,
    background_tasks: BackgroundTasks,
) -> None:
    """Send the job to Celery; fall back to an in-process task if the broker is unavailable."""
    try:
        from worker.tasks import detect_sheet_task
        detect_sheet_task.delay(
            job_id=job.id,
            sheet_id=sheet.id,
            file_rel_path=sheet.file_path,
            dpi=dpi,
            rot=rot,
            mode=mode,
        )
        return
    except Exception:
        pass
    background_tasks.add_task(
        run_detection_in_background,
        job_id=job.id,
        sheet_id=sheet.id,
        file_path=sheet.file_path,
        dpi=dpi,
        rot=rot,
        mode=mode,
    )

def dispatch_enrichment_job(
    job: Job,
    sheet: Sheet,
    *,
    dpi: int,
    rot: int,
    background_tasks: BackgroundTasks,
) -> None:
    """Send the enrichment job to Celery; fall back to an in-process task if the broker is down."""
    try:
        from worker.tasks import enrich_sheet_task
        enrich_sheet_task.delay(
            job_id=job.id,
            sheet_id=sheet.id,
            file_rel_path=sheet.file_path,
            dpi=dpi,
            rot=rot,
        )
        return
    except Exception:
        pass
    background_tasks.add_task(
        run_enrichment_in_background,
        job_id=job.id,
        sheet_id=sheet.id,
        file_path=sheet.file_path,
        dpi=dpi,
        rot=rot,
    )

async def run_detection_in_background(job_id: str, sheet_id: str, file_path: str, dpi: int, rot: int, mode: str = "full"):
    """Fallback runner when Celery is not active or for lightweight dev testing."""
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        sheet = await session.get(Sheet, sheet_id)
        if not job or not sheet:
            return

        try:
            job.status = "processing"
            job.step = "starting"
            job.message = f"Running detection pipeline (mode: {mode})..."
            sheet.status = "processing"
            await session.commit()

            # Execute pipeline synchronously in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                execute_sheet_detection,
                file_path,
                dpi,
                rot,
                None,
                mode,
            )

            # Compute circuits
            circuits = GroupingService.compute_circuits(result)

            sheet.result_json = result
            sheet.systems_json = circuits
            sheet.status = "completed"
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

async def run_enrichment_in_background(job_id: str, sheet_id: str, file_path: str, dpi: int, rot: int):
    """Fallback runner for sheet enrichment when Celery is not active."""
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        sheet = await session.get(Sheet, sheet_id)
        if not job or not sheet:
            return

        try:
            job.status = "processing"
            job.step = "starting"
            job.message = "Running sheet enrichment (OCR & YOLO)..."
            sheet.status = "detecting"
            await session.commit()

            existing_runs = sheet.result_json.get("runs", []) if sheet.result_json else []
            existing_pids = sheet.result_json.get("piping_ids", []) if sheet.result_json else []
            existing_symbols = sheet.result_json.get("symbols", []) if sheet.result_json else []

            loop = asyncio.get_event_loop()
            enrichment_result = await loop.run_in_executor(
                None,
                execute_sheet_enrichment,
                file_path,
                existing_runs,
                existing_pids,
                existing_symbols,
                dpi,
                rot,
            )

            res = dict(sheet.result_json or {})
            res["symbols"] = enrichment_result.get("symbols", [])
            res["piping_ids"] = enrichment_result.get("piping_ids", [])
            res["runs"] = enrichment_result.get("runs", res.get("runs", []))
            if "furniture" in enrichment_result:
                res["furniture"] = enrichment_result["furniture"]
            if "conn_points" in enrichment_result:
                res["conn_points"] = enrichment_result["conn_points"]
            if "opcs" in enrichment_result:
                res["opcs"] = enrichment_result["opcs"]

            circuits = GroupingService.compute_circuits(res)
            sheet.result_json = res
            sheet.systems_json = circuits
            sheet.status = "completed"

            job.status = "completed"
            job.progress_pct = 100
            job.step = "completed"
            job.message = "Enrichment completed successfully"
            job.completed_at = datetime.utcnow()
            await session.commit()

        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.message = f"Error: {str(e)}"
            sheet.status = "error"
            await session.commit()
