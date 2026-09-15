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
from app.services.detection_service import execute_sheet_detection
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


@celery_app.task(bind=True, name="detect_sheet_task")
def detect_sheet_task(self, job_id: str, sheet_id: str, file_rel_path: str, dpi: int = 350, rot: int = 0):
    """Celery background task for P&ID digitization & systemization."""
    r_client = None
    try:
        r_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception:
        r_client = None

    def _progress_cb(step: str, current: int, total: int, msg: str):
        publish_progress(r_client, job_id, step, current, total, msg)

    try:
        # 1. Execute CV / ML detection pipeline
        publish_progress(r_client, job_id, "starting", 0, 100, "Starting P&ID pipeline...")
        result = execute_sheet_detection(
            file_rel_path=file_rel_path,
            dpi=dpi,
            rot=rot,
            progress_callback=_progress_cb,
        )

        # 2. Compute API RP 970 Systems & Circuits
        publish_progress(r_client, job_id, "grouping", 92, 100, "Pengelompokan Corrosion System & Circuit...")
        systems = GroupingService.compute_circuits(result)

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
        except Exception as e:
            # Tiling failure is non-fatal to detection results
            pass

        publish_progress(r_client, job_id, "completed", 100, 100, "Digitasi & Sistemisasi selesai.")

        return {
            "status": "completed",
            "job_id": job_id,
            "sheet_id": sheet_id,
            "result": result,
            "systems": systems,
        }

    except Exception as exc:
        err_msg = str(exc)
        publish_progress(r_client, job_id, "failed", 0, 100, f"Error: {err_msg}")
        raise exc
