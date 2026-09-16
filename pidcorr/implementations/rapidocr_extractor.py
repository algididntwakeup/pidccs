from typing import Callable, Dict, Any, List, Optional, Tuple
import numpy as np

from ..interfaces.perception import BaseTextExtractor
from ..piping_id import detect_piping_ids


class RapidOCRExtractor(BaseTextExtractor):
    """RapidOCR ONNX extractor using multi-angle (0°, 90°, 270°) tiled scanning."""

    def extract(
        self,
        img_bgr: np.ndarray,
        tile: int = 1200,
        progress: Optional[Callable[[str], None]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        tokens_out = []
        pids = detect_piping_ids(
            img_bgr=img_bgr,
            tile=tile,
            overlap=0.25,
            progress=progress,
            tokens_out=tokens_out,
        )
        return pids, tokens_out
