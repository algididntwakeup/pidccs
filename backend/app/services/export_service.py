import os
import sys
import hashlib
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
    def _output_path(clean_name: str, export_format: str, mode: str) -> str:
        suffix = f"_marked_{mode}" if export_format in {"pdf", "png"} else "_line_register" if export_format == "xlsx" else "_asset_register"
        extension = export_format
        return storage.get_file_path(os.path.join("exports", clean_name, f"{clean_name}{suffix}.{extension}"))

    @staticmethod
    def _cached_base_image(result: Dict[str, Any]):
        """Render a source drawing once per file/dpi/rotation and reuse it for PNG exports."""
        raw_path = result.get("image_path")
        if not raw_path or not os.path.exists(raw_path):
            raise FileNotFoundError("Base image file not found for raster render")
        stat = os.stat(raw_path)
        cache_key = hashlib.sha256(
            f"{raw_path}|{stat.st_mtime_ns}|{stat.st_size}|{result.get('dpi', 350)}|{result.get('rot', 0)}".encode()
        ).hexdigest()[:24]
        cache_path = storage.get_file_path(os.path.join("cache", f"base-{cache_key}.png"))
        if os.path.exists(cache_path):
            cached = cv2.imread(cache_path, cv2.IMREAD_COLOR)
            if cached is not None:
                return cached

        img_bgr = load_drawing_image(raw_path, dpi=result.get("dpi", 350))
        if result.get("rot"):
            from pidcorr.pipeline import rotate_bgr
            img_bgr = rotate_bgr(img_bgr, result["rot"])
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        cv2.imwrite(cache_path, img_bgr, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        return img_bgr

    @staticmethod
    def export(
        result: Dict[str, Any],
        drawing_name: str,
        export_format: Literal["xlsx", "docx", "pdf", "png"],
        mode: Literal["system", "circuit", "engineer"] = "engineer",
    ) -> str:
        """Export digitization deliverables. Returns the generated file path."""
        clean_name = os.path.splitext(os.path.basename(drawing_name))[0]
        if export_format == "xlsx":
            abs_file = ExportService._output_path(clean_name, export_format, mode)
            os.makedirs(os.path.dirname(abs_file), exist_ok=True)
            export_register_xlsx(result, abs_file, drawing_name=clean_name)
            return abs_file

        elif export_format == "docx":
            abs_file = ExportService._output_path(clean_name, export_format, mode)
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
            abs_file = ExportService._output_path(clean_name, export_format, mode)
            os.makedirs(os.path.dirname(abs_file), exist_ok=True)
            export_marked_pdf(result, abs_file, mode=mode)
            return abs_file

        elif export_format == "png":
            abs_file = ExportService._output_path(clean_name, export_format, mode)
            os.makedirs(os.path.dirname(abs_file), exist_ok=True)

            img_bgr = ExportService._cached_base_image(result)
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
