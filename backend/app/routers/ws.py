import asyncio
import json
import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/progress/{job_id}")
async def websocket_progress_endpoint(websocket: WebSocket, job_id: str):
    """Real-time WebSocket endpoint streaming detection pipeline progress events."""
    await websocket.accept()

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
