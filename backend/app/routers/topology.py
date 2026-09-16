from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..schemas.topology import ProjectTopologyResponse, ProjectCircuit
from ..services.project_service import ProjectService
from ..services.topology_service import MultiPageGraphService

router = APIRouter(prefix="/projects/{project_id}", tags=["topology"])


@router.get("/topology", response_model=ProjectTopologyResponse)
async def get_project_topology(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve full multi-page topology graph linking drawing sheets across OPC connections."""
    project = await ProjectService.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    return await MultiPageGraphService.build_project_topology(db, project_id)


@router.get("/circuits", response_model=List[ProjectCircuit])
async def get_project_circuits(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve project-wide unified API RP 970 corrosion circuits spanning multiple drawings."""
    project = await ProjectService.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    topology = await MultiPageGraphService.build_project_topology(db, project_id)
    return topology.circuits
