from typing import Callable, Dict, Any, List, Optional
import numpy as np

from ..interfaces.perception import BaseLineTracer
from ..lines import extract_pipe_runs


class MorphologyLineTracer(BaseLineTracer):
    """Directional morphology pipe line tracer with DSU elbow merging and suppression."""

    def trace(
        self,
        img_bgr: np.ndarray,
        dpi: int = 350,
        detections: Optional[List[Dict[str, Any]]] = None,
        furniture: Optional[List[List[int]]] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> List[Dict[str, Any]]:
        if progress:
            progress("Tracing pipe lines with morphology...")
        runs = extract_pipe_runs(
            img_bgr=img_bgr,
            dpi=dpi,
            detections=detections,
            furniture=furniture,
        )
        return runs
