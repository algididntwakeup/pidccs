import os
import sys
import json
import redis
import asyncio
from datetime import datetime

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from worker.celery_app import celery_app
from app.config import settings
from app.services.detection_service import execute_sheet_detection, execute_sheet_enrichment
from app.services.grouping_service import GroupingService
from app.services.tile_service import TileService


def publish_progress(redis_client, job_id: str, step: str, current: int, total: int, message: str):
    """Publish real-time progress update to Redis pubsub."""
    if not redis_client:
        return
    try:
        pct = int(round(100.0 * current / total)) if total > 0 else 0
        event = {
            "type": "progress",
            "job_id": job_id,
            "step": step,
            "current": current,
            "total": total,
            "pct": pct,
            "message": message,
        }
        redis_client.publish(f"progress:{job_id}", json.dumps(event))
    except Exception:
        pass


from contextlib import asynccontextmanager
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


@asynccontextmanager
async def get_worker_session():
    """Scoped async DB session using NullPool for Celery worker tasks.

    NullPool guarantees connections are closed cleanly per session and never
    recycled across different event loops, preventing asyncpg 'Future attached
    to a different loop' / 'Event loop is closed' errors.
    """
    worker_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    worker_session_maker = async_sessionmaker(
        bind=worker_engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        async with worker_session_maker() as session:
            yield session
    finally:
        await worker_engine.dispose()


async def _save_detection_to_db(job_id: str, sheet_id: str, result: dict, systems: list):
    """Persist completed detection and API RP 970 circuits to database."""
    from app.models.sheet import Sheet
    from app.models.job import Job

    async with get_worker_session() as session:
        sheet = await session.get(Sheet, sheet_id)
        if sheet:
            sheet.result_json = result
            sheet.systems_json = systems
            sheet.status = "detected"
            sheet.width = result.get("w")
            sheet.height = result.get("h")
        job = await session.get(Job, job_id)
        if job:
            job.status = "completed"
            job.progress_pct = 100
            job.step = "completed"
            job.message = "Digitasi & Sistemisasi selesai."
            job.completed_at = datetime.utcnow()
        await session.commit()


async def _save_enrichment_to_db(job_id: str, sheet_id: str, enrichment_result: dict):
    """Persist completed enrichment results to database without overwriting original run geometries."""
    from app.models.sheet import Sheet
    from app.models.job import Job
    from app.services.grouping_service import GroupingService

    async with get_worker_session() as session:
        sheet = await session.get(Sheet, sheet_id)
        if sheet:
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

            try:
                sheet.systems_json = GroupingService.compute_circuits(res)
            except Exception:
                pass

            sheet.result_json = res
            sheet.status = "detected"
        job = await session.get(Job, job_id)
        if job:
            job.status = "completed"
            job.progress_pct = 100
            job.step = "completed"
            job.message = "Pindai simbol dan teks (Enrich) selesai."
            job.completed_at = datetime.utcnow()
        await session.commit()


async def _save_error_to_db(job_id: str, sheet_id: str, err_msg: str):
    """Persist job failure to database."""
    from app.models.sheet import Sheet
    from app.models.job import Job

    try:
        async with get_worker_session() as session:
            sheet = await session.get(Sheet, sheet_id)
            if sheet:
                sheet.status = "error"
            job = await session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error = err_msg
                job.message = f"Error: {err_msg}"
                job.completed_at = datetime.utcnow()
            await session.commit()
    except Exception as e:
        print(f"[Worker] Failed to write error to DB: {e}")


@celery_app.task(bind=True, name="detect_sheet_task")
def detect_sheet_task(self, job_id: str, sheet_id: str, file_rel_path: str, dpi: int = 350, rot: int = 0, mode: str = "full"):
    """Celery background task for P&ID digitization & systemization."""
    r_client = None
    try:
        r_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception:
        r_client = None

    def _progress_cb(step: str, current: int, total: int, msg: str):
        publish_progress(r_client, job_id, step, current, total, msg)

    # Single event loop for all async DB operations in this task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # 1. Execute CV / ML detection pipeline
        publish_progress(r_client, job_id, "starting", 0, 100, f"Starting P&ID pipeline (mode: {mode})...")
        result = execute_sheet_detection(
            file_rel_path=file_rel_path,
            dpi=dpi,
            rot=rot,
            progress_callback=_progress_cb,
            mode=mode,
        )

        # 2. API RP 970 Systems & Circuits (Temporarily disabled in Phase B.5 pivot)
        systems = []

        # 3. Generate DZI pyramid tiles in background
        publish_progress(r_client, job_id, "tiling", 96, 100, "Membuat DeepZoom image pyramid...")
        try:
            dzi_dir = os.path.join(os.path.dirname(file_rel_path), "dzi")
            TileService.generate_dzi_pyramid(
                file_rel_path=file_rel_path,
                output_dir_rel=dzi_dir,
                dpi=dpi,
                rot=rot,
            )
        except Exception:
            pass

        # 4. Save results to PostgreSQL database
        publish_progress(r_client, job_id, "saving", 98, 100, "Menyimpan hasil ke database...")
        try:
            loop.run_until_complete(_save_detection_to_db(job_id, sheet_id, result, systems))
        except Exception as db_err:
            print(f"[Worker] DB commit error: {db_err}")

        publish_progress(r_client, job_id, "completed", 100, 100, "Digitasi selesai.")

        return {
            "status": "completed",
            "job_id": job_id,
            "sheet_id": sheet_id,
            "result": result,
            "systems": systems,
        }

    except Exception as exc:
        err_msg = str(exc)
        try:
            loop.run_until_complete(_save_error_to_db(job_id, sheet_id, err_msg))
        except Exception:
            pass
        publish_progress(r_client, job_id, "failed", 0, 100, f"Error: {err_msg}")
        raise exc
    finally:
        loop.close()


@celery_app.task(bind=True, name="enrich_sheet_task")
def enrich_sheet_task(self, job_id: str, sheet_id: str, file_rel_path: str, dpi: int = 350, rot: int = 0):
    """Celery background task for sheet enrichment (AI OCR & YOLO symbols)."""
    r_client = None
    try:
        r_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception:
        r_client = None

    def _progress_cb(step: str, current: int, total: int, msg: str):
        publish_progress(r_client, job_id, step, current, total, msg)

    # Create a single, dedicated event loop for ALL async DB operations in this task.
    # This avoids the "Future attached to a different loop" error caused by multiple
    # asyncio.run() calls each creating (and closing) a new loop.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        publish_progress(r_client, job_id, "starting", 0, 100, "Starting sheet enrichment (OCR & YOLO)...")

        async def _get_existing_data():
            from app.models.sheet import Sheet
            async with get_worker_session() as session:
                sheet = await session.get(Sheet, sheet_id)
                if sheet and sheet.result_json:
                    return (
                        sheet.result_json.get("runs", []),
                        sheet.result_json.get("piping_ids", []),
                        sheet.result_json.get("symbols", []),
                    )
                return [], [], []

        existing_runs, existing_pids, existing_symbols = loop.run_until_complete(_get_existing_data())

        enrichment_result = execute_sheet_enrichment(
            file_rel_path=file_rel_path,
            existing_runs=existing_runs,
            existing_pids=existing_pids,
            existing_symbols=existing_symbols,
            dpi=dpi,
            rot=rot,
            progress_callback=_progress_cb,
        )

        publish_progress(r_client, job_id, "saving", 98, 100, "Menyimpan hasil enrichment ke database...")
        try:
            loop.run_until_complete(_save_enrichment_to_db(job_id, sheet_id, enrichment_result))
        except Exception as db_err:
            print(f"[Worker] DB commit error in enrichment: {db_err}")

        publish_progress(r_client, job_id, "completed", 100, 100, "Pindai simbol dan teks (Enrich) selesai.")

        return {
            "status": "completed",
            "job_id": job_id,
            "sheet_id": sheet_id,
            "enrichment": enrichment_result,
        }

    except Exception as exc:
        err_msg = str(exc)
        try:
            loop.run_until_complete(_save_error_to_db(job_id, sheet_id, err_msg))
        except Exception:
            pass
        publish_progress(r_client, job_id, "failed", 0, 100, f"Error: {err_msg}")
        raise exc
    finally:
        loop.close()

