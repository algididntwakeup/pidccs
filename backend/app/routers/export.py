import os
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..services.project_service import ProjectService
from ..services.export_service import ExportService

router = APIRouter(prefix="/projects/{project_id}/sheets/{sheet_id}/export", tags=["export"])

MIME_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "png": "image/png",
}


@router.get("")
async def export_deliverable(
    project_id: str,
    sheet_id: str,
    format: Literal["xlsx", "docx", "pdf", "png"] = Query("xlsx"),
    mode: Literal["system", "circuit", "engineer"] = Query("engineer"),
    db: AsyncSession = Depends(get_db),
):
    """Generate and download P&ID digitization deliverables."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if not sheet.result_json:
        raise HTTPException(status_code=400, detail="Sheet has no processed result to export")

    try:
        exported_path = ExportService.export(
            result=sheet.result_json,
            drawing_name=sheet.filename,
            export_format=format,
            mode=mode,
        )

        if not os.path.exists(exported_path):
            raise HTTPException(status_code=500, detail="Failed to create export file")

        filename = os.path.basename(exported_path)
        media_type = MIME_TYPES.get(format, "application/octet-stream")

        return FileResponse(
            path=exported_path,
            filename=filename,
            media_type=media_type,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export generation failed: {str(e)}")
