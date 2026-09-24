import os
from typing import Callable, Dict, Any, List, Optional
import numpy as np

from ..interfaces.perception import BaseSymbolDetector
from ..detect import predict_tiled, Det
from ..layout import detect_fullpage, suppress_nested
from ..pipeline import classify_boxes, merge_equipment, detect_boxes

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_WEIGHTS = os.path.join(_ROOT, "runs", "detect", "pid3_finetune", "weights", "best.pt")
EQUIP_BIG_WEIGHTS = os.path.join(_ROOT, "runs", "detect", "equip_big", "weights", "best.pt")


class YOLOTiledDetector(BaseSymbolDetector):
    """Standard tiled YOLO detector wrapping Ultralytics YOLO11 model with 640px sliding tiles."""

    def __init__(self, weights_path: str = DEFAULT_WEIGHTS, equip_big_weights: str = EQUIP_BIG_WEIGHTS):
        self.weights_path = weights_path
        self.equip_big_weights = equip_big_weights
        self._model = None
        self._equip_big_model = None

    def load_weights(self, weights_path: str) -> None:
        self.weights_path = weights_path
        self._model = None

    def _get_model(self):
        if self._model is None and os.path.exists(self.weights_path):
            from ultralytics import YOLO
            self._model = YOLO(self.weights_path)
        return self._model

    def detect(
        self,
        img_bgr: np.ndarray,
        conf: float = 0.3,
        progress: Optional[Callable[[str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Detect symbols using tiled YOLO plus large-equipment fallback."""
        if progress:
            progress("YOLO tiled detection: mulai")
        model = self._get_model()
        if model is not None:
            try:
                import torch
                dev = 0 if torch.cuda.is_available() else "cpu"
            except Exception:
                dev = "cpu"
            dets, _ = predict_tiled(
                model=model, img_bgr=img_bgr, tile=640, overlap=0.20,
                conf=conf, device=dev, progress=progress,
            )
        else:
            dets = []
        if progress:
            progress("YOLO tiled detection: selesai")
        # Contour-based box detection for equipment/detail outlines.
        boxes = detect_boxes(img_bgr)
        eq_boxes, _ = classify_boxes(img_bgr, boxes)
        for b, name in eq_boxes:
            dets.append(Det(b[0], b[1], b[2], b[3], 0.95,
                            name or "box_contour", "equipment"))
        if progress:
            progress("Deteksi kontur equipment selesai")
        if os.path.exists(self.equip_big_weights):
            if progress:
                progress("Deteksi equipment besar (full-page)")
            eq_big = detect_fullpage(
                img_bgr, weights=self.equip_big_weights,
                imgsz=1024, conf=0.25, with_conf=True,
            )
            for b in eq_big:
                dets.append(Det(b[0], b[1], b[2], b[3], b[4], "equip_big", "equipment"))
        raw_syms = [
            {"coarse": d.coarse, "cls": d.cls, "conf": round(float(d.conf), 3),
             "x1": float(d.x1), "y1": float(d.y1), "x2": float(d.x2), "y2": float(d.y2)}
            for d in dets
        ]
        final_syms = merge_equipment(raw_syms, overlap=0.25)
        if progress:
            progress(f"Deteksi simbol selesai ({len(final_syms)} objek)")
        return final_syms
