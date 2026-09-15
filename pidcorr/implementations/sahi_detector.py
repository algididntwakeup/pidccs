import os
from typing import Callable, Dict, Any, List, Optional
import numpy as np

from ..interfaces.perception import BaseSymbolDetector
from ..detect import COARSE

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_WEIGHTS = os.path.join(_ROOT, "runs", "detect", "pid3_finetune", "weights", "best.pt")
EQUIP_BIG_WEIGHTS = os.path.join(_ROOT, "runs", "detect", "equip_big", "weights", "best.pt")


class SAHISymbolDetector(BaseSymbolDetector):
    """SAHI (Slicing Aided Hyper Inference) multi-scale detector for P&ID engineering symbols."""

    def __init__(
        self,
        weights_path: str = DEFAULT_WEIGHTS,
        slice_size: int = 640,
        overlap_ratio: float = 0.20,
    ):
        self.weights_path = weights_path
        self.slice_size = slice_size
        self.overlap_ratio = overlap_ratio
        self._sahi_model = None

    def load_weights(self, weights_path: str) -> None:
        self.weights_path = weights_path
        self._sahi_model = None

    def _get_sahi_model(self, conf: float = 0.3):
        if self._sahi_model is None:
            from sahi import AutoDetectionModel
            self._sahi_model = AutoDetectionModel.from_pretrained(
                model_type="yolov8",  # SAHI ultralytics backend supports YOLOv8-11
                model_path=self.weights_path,
                confidence_threshold=conf,
                device="cuda:0" if self._has_cuda() else "cpu",
            )
        return self._sahi_model

    def _has_cuda(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    def detect(
        self,
        img_bgr: np.ndarray,
        conf: float = 0.3,
        progress: Optional[Callable[[str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Run multi-scale SAHI sliced inference on the high-res P&ID image."""
        if progress:
            progress("Running SAHI multi-scale sliced inference (640px slices)...")

        try:
            from sahi.predict import get_sliced_prediction
            import cv2

            # SAHI expects RGB
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            model = self._get_sahi_model(conf=conf)

            sliced_result = get_sliced_prediction(
                img_rgb,
                model,
                slice_height=self.slice_size,
                slice_width=self.slice_size,
                overlap_height_ratio=self.overlap_ratio,
                overlap_width_ratio=self.overlap_ratio,
                verbose=0,
            )

            syms = []
            for obj in sliced_result.object_prediction_list:
                bbox = obj.bbox
                raw_cls = obj.category.name.lower()
                coarse = COARSE.get(raw_cls, "equipment" if "equip" in raw_cls else "other")
                syms.append({
                    "coarse": coarse,
                    "cls": raw_cls,
                    "conf": round(float(obj.score.value), 3),
                    "x1": float(bbox.minx),
                    "y1": float(bbox.miny),
                    "x2": float(bbox.maxx),
                    "y2": float(bbox.maxy),
                })

            return syms

        except Exception as e:
            # Fallback to standard tiled detector if SAHI fails
            if progress:
                progress(f"SAHI fallback to YOLOTiledDetector: {e}")
            from .yolo_detector import YOLOTiledDetector
            fallback = YOLOTiledDetector(self.weights_path)
            return fallback.detect(img_bgr, conf=conf, progress=progress)
