import json

from fastapi import APIRouter, Depends, HTTPException

from ..config import settings
from ..core.dependencies import get_current_user
from ..schemas.bima import BimaProjectRecord

router = APIRouter(prefix="/bima", tags=["bima"], dependencies=[Depends(get_current_user)])

@router.get("/projects", response_model=list[BimaProjectRecord])
async def list_bima_projects() -> list[BimaProjectRecord]:
    """Flat BIMA project catalog (simulated external API).

    BIMA publishes a directory of projects, not per-user assignments. Every
    authenticated engineer sees the same rows and imports the ones they work on.
    """
    try:
        catalog = json.loads(settings.BIMA_MOCK_PROJECTS_JSON)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="BIMA project catalog is unreadable") from exc
    return [BimaProjectRecord.model_validate(item) for item in catalog]
