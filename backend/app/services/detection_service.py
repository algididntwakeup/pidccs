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

# Process-level singleton: the orchestrator holds the heavy YOLO / OCR models in memory.
# Building it once per task (as before) reloaded weights from disk for every sheet, which
# is a major source of the "web app gets heavier with each P&ID opened" degradation.
_ORCHESTRATOR = None


def get_orchestrator():
    """Return the process-wide PipelineOrchestrator singleton (lazy, built once)."""
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        from pidcorr.factory import get_configured_orchestrator
        _ORCHESTRATOR = get_configured_orchestrator()
    return _ORCHESTRATOR


def _resolve_drawing_abs_path(file_rel_path: str) -> str:
    """Resolve file relative or absolute path to an existing absolute filesystem path."""
    abs_path = storage.get_file_path(file_rel_path)
    if not os.path.exists(abs_path):
        if os.path.isabs(file_rel_path) and os.path.exists(file_rel_path):
            abs_path = file_rel_path
        elif os.path.exists(os.path.join(_ROOT, file_rel_path)):
            abs_path = os.path.join(_ROOT, file_rel_path)
        else:
            raise FileNotFoundError(f"Drawing file not found at: {abs_path}")
    return abs_path


def _create_progress_reporter(progress_callback: Optional[Callable[[str, int, int, str], None]]):
    def _say(*args, **kwargs):
        if not progress_callback:
            return
        if len(args) == 2 and isinstance(args[0], (int, float)) and isinstance(args[1], (int, float)):
            current, total = int(args[0]), int(args[1])
            progress_callback("ocr_tiled", current, total, f"OCR tile {current}/{total}")
            return

        msg = str(args[0]) if args else ""
        step, current, total = "processing", 0, 100
        if msg.startswith("YOLO tile "):
            step = "symbol_detection"
            try:
                done, count = msg.rsplit(" ", 1)[-1].split("/")
                current = 65 + round(10 * int(done) / max(1, int(count)))
                total = 100
            except (ValueError, IndexError):
                current = 65
        elif "YOLO tiled detection: mulai" in msg:
            step, current = "symbol_detection", 65
        elif "Mendeteksi furniture" in msg:
            step, current = "symbol_detection", 78
        elif "Mengklasifikasikan subtype" in msg:
            step, current = "symbol_detection", 79
        elif "Subtype selesai" in msg:
            step, current = "symbol_detection", 79
        elif "YOLO tiled detection: selesai" in msg:
            step, current = "symbol_detection", 75
        elif "equipment besar" in msg:
            step, current = "symbol_detection", 77
        elif "kontur equipment" in msg:
            step, current = "symbol_detection", 76
        elif "Deteksi simbol selesai" in msg:
            step, current = "symbol_detection", 79
        elif "equipment" in msg or "YOLO" in msg:
            step, current = "symbol_detection", 65
        elif "tracing" in msg or "line" in msg:
            step, current = "line_tracing", 80
        elif "connection point" in msg:
            step, current = "spec_break", 90
        elif "selesai" in msg:
            step, current = "completed", 100
        progress_callback(step, current, total, msg)

    return _say


def execute_sheet_detection(
    file_rel_path: str,
    dpi: int = 350,
    rot: int = 0,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
    mode: str = "full",
) -> Dict[str, Any]:
    """Execute the P&ID digitization pipeline on a sheet (mode='full' or 'lines_only').

    progress_callback signature: (step: str, current: int, total: int, message: str)
    """
    abs_path = _resolve_drawing_abs_path(file_rel_path)
    _say = _create_progress_reporter(progress_callback)

    _say("Memuat citra P&ID...")
    img = load_drawing_image(abs_path, dpi=dpi)
    if rot != 0:
        img = pipeline.rotate_bgr(img, rot)

    # Reuse the process-wide singleton so YOLO/OCR weights are loaded only once.
    orchestrator = get_orchestrator()

    result = orchestrator.run(
        img_bgr=img,
        image_path=abs_path,
        dpi=dpi,
        rot=rot,
        progress=_say,
        mode=mode,
    )
    return result


def execute_sheet_enrichment(
    file_rel_path: str,
    existing_runs: list,
    dpi: int = 350,
    rot: int = 0,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    """Execute AI enrichment (OCR, YOLO symbol detection, association) on previously traced sheet runs.

    progress_callback signature: (step: str, current: int, total: int, message: str)
    """
    abs_path = _resolve_drawing_abs_path(file_rel_path)
    _say = _create_progress_reporter(progress_callback)

    _say("Memuat citra P&ID untuk enrichment...")
    img = load_drawing_image(abs_path, dpi=dpi)
    if rot != 0:
        img = pipeline.rotate_bgr(img, rot)

    orchestrator = get_orchestrator()

    enrichment_result = orchestrator.run_enrichment(
        img_bgr=img,
        existing_runs=existing_runs,
        image_path=abs_path,
        dpi=dpi,
        rot=rot,
        progress=_say,
    )
    return enrichment_result
