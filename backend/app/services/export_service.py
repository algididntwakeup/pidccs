import os
import sys
import uuid
import cv2
from typing import Dict, Any, Literal

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr.export import export_register_xlsx, export_marked_pdf, render_marked_png
from pidcorr.asset_register import export_asset_register_docx
from ..adapters.pdf_renderer import load_drawing_image
from ..adapters.storage import LocalStorageAdapter
from ..config import settings

storage = LocalStorageAdapter(settings.STORAGE_DIR)


class ExportService:
    @staticmethod
    def export(
        result: Dict[str, Any],
        drawing_name: str,
        export_format: Literal["xlsx", "docx", "pdf", "png"],
        mode: Literal["system", "circuit", "engineer"] = "engineer",
    ) -> str:
        """Export digitization deliverables. Returns the generated file path."""
        export_id = str(uuid.uuid4())[:8]
        clean_name = os.path.splitext(os.path.basename(drawing_name))[0]
        rel_dir = os.path.join("exports", clean_name)

        if export_format == "xlsx":
            rel_file = os.path.join(rel_dir, f"{clean_name}_line_register_{export_id}.xlsx")
            abs_file = storage.get_file_path(rel_file)
            os.makedirs(os.path.dirname(abs_file), exist_ok=True)
            export_register_xlsx(result, abs_file, drawing_name=clean_name)
            return abs_file

        elif export_format == "docx":
            rel_file = os.path.join(rel_dir, f"{clean_name}_asset_register_{export_id}.docx")
            abs_file = storage.get_file_path(rel_file)
            os.makedirs(os.path.dirname(abs_file), exist_ok=True)

            img_bgr = None
            raw_path = result.get("image_path")
            if raw_path and os.path.exists(raw_path):
                try:
                    img_bgr = load_drawing_image(raw_path, dpi=result.get("dpi", 350))
                except Exception:
                    pass

            export_asset_register_docx(
                result=result,
                path=abs_file,
                drawing_name=clean_name,
                img_bgr=img_bgr,
                read_tags=False,  # tags already stored in result
            )
            return abs_file

        elif export_format == "pdf":
            rel_file = os.path.join(rel_dir, f"{clean_name}_marked_{mode}_{export_id}.pdf")
            abs_file = storage.get_file_path(rel_file)
            os.makedirs(os.path.dirname(abs_file), exist_ok=True)
            export_marked_pdf(result, abs_file, mode=mode)
            return abs_file

        elif export_format == "png":
            rel_file = os.path.join(rel_dir, f"{clean_name}_marked_{mode}_{export_id}.png")
            abs_file = storage.get_file_path(rel_file)
            os.makedirs(os.path.dirname(abs_file), exist_ok=True)

            raw_path = result.get("image_path")
            if not raw_path or not os.path.exists(raw_path):
                raise FileNotFoundError("Base image file not found for raster render")

            img_bgr = load_drawing_image(raw_path, dpi=result.get("dpi", 350))
            if result.get("rot"):
                from pidcorr.pipeline import rotate_bgr
                img_bgr = rotate_bgr(img_bgr, result["rot"])

            vis = render_marked_png(img_bgr, result, mode=mode)
            # Use safe write
            is_success, buffer = cv2.imencode(".png", vis)
            if is_success:
                with open(abs_file, "wb") as f:
                    f.write(buffer)
            else:
                cv2.imwrite(abs_file, vis)
            return abs_file

        raise ValueError(f"Unsupported export format: {export_format}")
