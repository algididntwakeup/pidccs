from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..schemas.system import CorrosionSystem
from ..services.project_service import ProjectService
from ..services.grouping_service import GroupingService

router = APIRouter(prefix="/projects/{project_id}/sheets/{sheet_id}", tags=["grouping"])


@router.get("/systems", response_model=List[CorrosionSystem])
async def get_corrosion_systems(
    project_id: str,
    sheet_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the API RP 970 Corrosion Systems & Circuits computed for this sheet."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if sheet.systems_json:
        return sheet.systems_json

    if not sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet has no detection results")

    circuits = GroupingService.compute_circuits(sheet.result_json)
    sheet.systems_json = circuits
    await db.commit()
    return circuits


@router.post("/recolor")
async def recolor_fluid_endpoint(
    project_id: str,
    sheet_id: str,
    fluid: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    """Dynamically reassign a distinct high-contrast color for a process fluid."""
    old_color, new_color = GroupingService.recolor(fluid)
    if not new_color:
        raise HTTPException(status_code=400, detail=f"Fluid '{fluid}' not registered")

    # Refresh sheet systems
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if sheet and sheet.result_json:
        sheet.systems_json = GroupingService.compute_circuits(sheet.result_json)
        await db.commit()

    return {
        "fluid": fluid.upper(),
        "old_color": old_color,
        "new_color": new_color
    }
