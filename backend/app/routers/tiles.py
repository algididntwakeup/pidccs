import os
import hashlib
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


def _file_etag(abs_path: str) -> str:
    """Weak-ish ETag from file identity: path + mtime + size."""
    stat = os.stat(abs_path)
    return hashlib.sha256(
        f"{abs_path}|{stat.st_mtime_ns}|{stat.st_size}".encode()
    ).hexdigest()[:32]


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
        serve_path = png_cache
    else:
        serve_path = abs_path

    # Aggressive caching: drawings are immutable once uploaded. Without this header
    # the browser re-downloads a multi-megapixel PNG on every project open / tab
    # switch, which makes the app feel progressively heavier.
    etag = _file_etag(serve_path)
    headers = {"Cache-Control": "public, max-age=31536000, immutable", "ETag": f'"{etag}"'}
    return FileResponse(serve_path, media_type="image/png", headers=headers)


@router.get("/thumbnail")
async def get_thumbnail(
    project_id: str,
    sheet_id: str,
    size: int = 480,
    db: AsyncSession = Depends(get_db),
):
    """Serve a small cached thumbnail for project/sheet cards (avoids full-res downloads)."""
    sheet = await ProjectService.get_sheet(db, sheet_id)
    if not sheet or sheet.project_id != project_id:
        raise HTTPException(status_code=404, detail="Sheet not found")

    abs_path = storage.get_file_path(sheet.file_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File missing from storage")

    size = max(64, min(int(size), 1024))
    stat = os.stat(abs_path)
    key = hashlib.sha256(
        f"{abs_path}|{stat.st_mtime_ns}|{stat.st_size}|{sheet.dpi}|{sheet.rot}|{size}".encode()
    ).hexdigest()[:24]
    thumb_path = storage.get_file_path(os.path.join("cache", "thumbs", f"thumb-{sheet_id}-{size}-{key}.png"))

    if not os.path.exists(thumb_path):
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        img = load_drawing_image(abs_path, dpi=sheet.dpi)
        if sheet.rot:
            from pidcorr.pipeline import rotate_bgr
            img = rotate_bgr(img, sheet.rot)
        h, w = img.shape[:2]
        scale = min(size / max(w, 1), size / max(h, 1), 1.0)
        if scale < 1.0:
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        if ok:
            with open(thumb_path, "wb") as f:
                f.write(buf)

    if not os.path.exists(thumb_path):
        raise HTTPException(status_code=500, detail="Failed to generate thumbnail")

    etag = _file_etag(thumb_path)
    headers = {"Cache-Control": "public, max-age=31536000, immutable", "ETag": f'"{etag}"'}
    return FileResponse(thumb_path, media_type="image/png", headers=headers)


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
