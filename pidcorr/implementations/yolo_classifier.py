import os
from typing import Dict, Any, Tuple
import numpy as np

from ..interfaces.perception import BaseSubtypeClassifier
from ..subtype import classify_valve_crops, classify_instruments

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_VALVE_WEIGHTS = os.path.join(_ROOT, "runs", "classify", "valve_cls", "weights", "best.pt")


class YOLOValveClassifier(BaseSubtypeClassifier):
    """Subtype classifier using YOLO11-cls for valves and ISA-5.1 OCR reading for instruments."""

    def __init__(self, weights_path: str = DEFAULT_VALVE_WEIGHTS):
        self.weights_path = weights_path

    def classify_valve(self, img_bgr: np.ndarray, sym: Dict[str, Any]) -> str:
        classify_valve_crops(img_bgr, [sym], weights=self.weights_path)
        return sym.get("subtype", "")

    def classify_instrument(self, img_bgr: np.ndarray, sym: Dict[str, Any]) -> Tuple[str, str]:
        classify_instruments(img_bgr, [sym])
        return sym.get("subtype", ""), ""
