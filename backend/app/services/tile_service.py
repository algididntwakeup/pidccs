import os
import math
import cv2
import numpy as np
from typing import Dict, Any

from ..adapters.pdf_renderer import load_drawing_image
from ..adapters.storage import LocalStorageAdapter
from ..config import settings

storage = LocalStorageAdapter(settings.STORAGE_DIR)
TILE_SIZE = 256


class TileService:
    @staticmethod
    def get_dzi_metadata(width: int, height: int, tile_size: int = TILE_SIZE, overlap: int = 1) -> Dict[str, Any]:
        """Return DZI XML definition or JSON format for OpenSeadragon."""
        max_dim = max(width, height)
        max_level = math.ceil(math.log2(max_dim)) if max_dim > 0 else 0
        return {
            "Image": {
                "xmlns": "http://schemas.microsoft.com/deepzoom/2008",
                "Format": "png",
                "Overlap": overlap,
                "TileSize": tile_size,
                "Size": {
                    "Width": width,
                    "Height": height
                }
            },
            "max_level": max_level
        }

    @staticmethod
    def generate_dzi_pyramid(
        file_rel_path: str,
        output_dir_rel: str,
        dpi: int = 350,
        rot: int = 0,
        tile_size: int = TILE_SIZE,
        overlap: int = 1,
    ) -> Dict[str, Any]:
        """Generate pyramid tiles into storage folder.
        Directory structure:
          output_dir/
            info.dzi
            info_files/
              {level}/{x}_{y}.png
        """
        abs_src = storage.get_file_path(file_rel_path)
        img = load_drawing_image(abs_src, dpi=dpi)
        if rot != 0:
            from pidcorr.pipeline import rotate_bgr
            img = rotate_bgr(img, rot)

        h, w = img.shape[:2]
        abs_out = storage.get_file_path(output_dir_rel)
        files_dir = os.path.join(abs_out, "tiles_files")
        os.makedirs(files_dir, exist_ok=True)

        metadata = TileService.get_dzi_metadata(w, h, tile_size, overlap)
        max_level = metadata["max_level"]

        # Write pyramid levels from max_level down to 0
        current_img = img
        for level in range(max_level, -1, -1):
            level_dir = os.path.join(files_dir, str(level))
            os.makedirs(level_dir, exist_ok=True)

            cur_h, cur_w = current_img.shape[:2]
            cols = math.ceil(cur_w / tile_size)
            rows = math.ceil(cur_h / tile_size)

            for c in range(cols):
                for r in range(rows):
                    # Compute bounds with overlap
                    x0 = max(0, c * tile_size - (overlap if c > 0 else 0))
                    y0 = max(0, r * tile_size - (overlap if r > 0 else 0))
                    x1 = min(cur_w, (c + 1) * tile_size + (overlap if c < cols - 1 else 0))
                    y1 = min(cur_h, (r + 1) * tile_size + (overlap if r < rows - 1 else 0))

                    tile = current_img[y0:y1, x0:x1]
                    tile_path = os.path.join(level_dir, f"{c}_{r}.png")
                    # Safe encode
                    _, buf = cv2.imencode(".png", tile)
                    with open(tile_path, "wb") as f:
                        f.write(buf)

            # Downsample by 2 for next lower level
            if level > 0:
                next_w = max(1, cur_w // 2)
                next_h = max(1, cur_h // 2)
                current_img = cv2.resize(current_img, (next_w, next_h), interpolation=cv2.INTER_AREA)

        # Write DZI XML file
        xml_content = f"""<?xml version="1.0" encoding="utf-8"?>
<Image xmlns="http://schemas.microsoft.com/deepzoom/2008"
  Format="png"
  Overlap="{overlap}"
  TileSize="{tile_size}">
  <Size Width="{w}" Height="{h}"/>
</Image>"""
        with open(os.path.join(abs_out, "image.dzi"), "w", encoding="utf-8") as f:
            f.write(xml_content)

        return metadata
