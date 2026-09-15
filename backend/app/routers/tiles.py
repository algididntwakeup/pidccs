import os
import cv2
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..services.project_service import ProjectService
from ..adapters.storage import LocalStorageAdapter
from ..adapters.pdf_renderer import load_drawing_image
from ..config import settings

router = APIRouter(prefix="/projects/{project_id}/sheets/{sheet_id}", tags=["tiles"])
storage = LocalStorageAdapter(settings.STORAGE_DIR)


@router.get("/raw")
async def get_raw_image(
    project_id: str,
    sheet_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Serve the rendered full-resolution drawing image."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    abs_path = storage.get_file_path(sheet.file_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File missing from storage")

    # If it's a PDF, render to PNG in memory or cache
    ext = os.path.splitext(abs_path)[1].lower()
    if ext == ".pdf":
        png_cache = storage.get_file_path(os.path.join("cache", f"{sheet_id}_rendered.png"))
        if not os.path.exists(png_cache):
            os.makedirs(os.path.dirname(png_cache), exist_ok=True)
            img = load_drawing_image(abs_path, dpi=sheet.dpi)
            if sheet.rot:
                from pidcorr.pipeline import rotate_bgr
                img = rotate_bgr(img, sheet.rot)
            _, buf = cv2.imencode(".png", img)
            with open(png_cache, "wb") as f:
                f.write(buf)
        return FileResponse(png_cache, media_type="image/png")

    return FileResponse(abs_path)


@router.get("/dzi")
async def get_dzi_manifest(
    project_id: str,
    sheet_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Serve DZI DeepZoom XML manifest for OpenSeadragon viewer."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    dzi_path = storage.get_file_path(os.path.join(os.path.dirname(sheet.file_path), "dzi", "image.dzi"))
    if not os.path.exists(dzi_path):
        # Fallback dynamic DZI XML
        w = sheet.width or 3300
        h = sheet.height or 2320
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Image xmlns="http://schemas.microsoft.com/deepzoom/2008"
  Format="png"
  Overlap="1"
  TileSize="256">
  <Size Width="{w}" Height="{h}"/>
</Image>"""
        return Response(content=xml, media_type="application/xml")

    with open(dzi_path, "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="application/xml")


@router.get("/tiles_files/{level}/{tile_coord}")
async def get_tile(
    project_id: str,
    sheet_id: str,
    level: int,
    tile_coord: str,
    db: AsyncSession = Depends(get_db),
):
    """Serve a single pyramid tile (e.g. 10/2_3.png)."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Sheet not found")

    tile_path = storage.get_file_path(
        os.path.join(os.path.dirname(sheet.file_path), "dzi", "tiles_files", str(level), tile_coord)
    )
    if not os.path.exists(tile_path):
        raise HTTPException(status_code=404, detail="Tile not found")

    return FileResponse(tile_path, media_type="image/png")
