import asyncio
import json
import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..db.session import AsyncSessionLocal
from ..models.job import Job

router = APIRouter(tags=["websocket"])

async def _terminal_snapshot(job_id: str):
    """Event terminal untuk job yang sudah berakhir SEBELUM WebSocket subscribe.

    Worker mem-publish `completed`/`failed` satu kali saja. Job Fast Trace selesai dalam
    hitungan detik, jadi koneksi yang datang belakangan tidak akan pernah menerima event
    apa pun dan UI menunggu selamanya. Format payload harus sama dengan `publish_progress`
    di `worker/tasks.py` karena frontend membaca `data.step` / `data.pct` / `data.message`.
    """
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
    if job is None or job.status not in ("completed", "failed"):
        return None
    return {
        "type": "progress",
        "job_id": job_id,
        "step": job.status,
        "current": job.progress_pct,
        "total": 100,
        "pct": job.progress_pct,
        "message": job.message or "",
    }

@router.websocket("/ws/progress/{job_id}")
async def websocket_progress_endpoint(websocket: WebSocket, job_id: str):
    """Real-time WebSocket endpoint streaming detection pipeline progress events."""
    await websocket.accept()

    # Job yang sudah selesai: kirim satu event terminal lalu tutup. Job yang belum ada
    # (worker belum membuat barisnya) tetap subscribe seperti sebelumnya.
    snapshot = await _terminal_snapshot(job_id)
    if snapshot is not None:
        try:
            await websocket.send_text(json.dumps(snapshot))
            await websocket.close()
        except Exception:
            pass
        return

    redis_conn = None
    pubsub = None
    try:
        redis_conn = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis_conn.pubsub()
        await pubsub.subscribe(f"progress:{job_id}")

        while True:
            # Check for messages with a short timeout to handle connection alive checks
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                data = message.get("data")
                if isinstance(data, str):
                    await websocket.send_text(data)
                    payload = json.loads(data)
                    if payload.get("step") in ("completed", "failed"):
                        break
            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if pubsub:
            await pubsub.unsubscribe(f"progress:{job_id}")
            await pubsub.close()
        if redis_conn:
            await redis_conn.close()
        try:
            await websocket.close()
        except Exception:
            pass
