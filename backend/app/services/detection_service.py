import os
import sys
import json
from typing import Callable, Optional, Dict, Any

# Ensure project root is in sys.path so pidcorr package can be imported
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr import pipeline
from ..adapters.pdf_renderer import load_drawing_image, get_default_pdf_renderer
from ..adapters.storage import LocalStorageAdapter
from ..config import settings

storage = LocalStorageAdapter(settings.STORAGE_DIR)


def execute_sheet_detection(
    file_rel_path: str,
    dpi: int = 350,
    rot: int = 0,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    """Execute the full P&ID digitization pipeline on a sheet.

    progress_callback signature: (step: str, current: int, total: int, message: str)
    """
    abs_path = storage.get_file_path(file_rel_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Drawing file not found at: {abs_path}")

    def _say(msg: str):
        if not progress_callback:
            return
        step = "processing"
        current, total = 0, 100
        if "OCR tile" in msg:
            step = "ocr_tiled"
            try:
                parts = msg.split(" ")[2].split("/")
                current, total = int(parts[0]), int(parts[1])
            except Exception:
                pass
        elif "equipment" in msg or "YOLO" in msg:
            step = "symbol_detection"
            current, total = 65, 100
        elif "tracing" in msg or "line" in msg:
            step = "line_tracing"
            current, total = 80, 100
        elif "connection point" in msg:
            step = "spec_break"
            current, total = 90, 100
        elif "selesai" in msg:
            step = "completed"
            current, total = 100, 100
        progress_callback(step, current, total, msg)

    _say("Memuat citra P&ID...")
    img = load_drawing_image(abs_path, dpi=dpi)
    if rot != 0:
        img = pipeline.rotate_bgr(img, rot)

    from pidcorr.factory import get_configured_orchestrator
    orchestrator = get_configured_orchestrator()

    result = orchestrator.run(
        img_bgr=img,
        image_path=abs_path,
        dpi=dpi,
        rot=rot,
        progress=_say,
    )
    return result
