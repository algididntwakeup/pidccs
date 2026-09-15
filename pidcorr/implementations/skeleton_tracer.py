import math
from typing import Callable, Dict, Any, List, Optional, Tuple
import cv2
import numpy as np

from ..interfaces.perception import BaseLineTracer
from ..lines import (
    suppress_box_edges,
    suppress_equipment_interior,
    suppress_furniture,
    suppress_box_outlines,
    detect_boxes,
)


def _morphological_skeleton(binary_img: np.ndarray) -> np.ndarray:
    """Fast morphological skeletonization using OpenCV 3x3 cross kernel."""
    skel = np.zeros(binary_img.shape, dtype=np.uint8)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    temp = np.empty(binary_img.shape, dtype=np.uint8)
    img = binary_img.copy()

    while True:
        cv2.morphologyEx(img, cv2.MORPH_OPEN, element, temp)
        cv2.bitwise_not(temp, temp)
        cv2.bitwise_and(img, temp, temp)
        cv2.bitwise_or(skel, temp, skel)
        cv2.erode(img, element, img)
        if cv2.countNonZero(img) == 0:
            break

    return skel


def _find_junctions_and_endpoints(skel: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Classify skeleton pixels by 8-connected degree."""
    # Kernel summing 8 neighbors
    kernel = np.array([
        [1, 1, 1],
        [1, 0, 1],
        [1, 1, 1]
    ], dtype=np.uint8)

    # Count neighbor ink pixels
    skel_binary = (skel > 0).astype(np.uint8)
    neighbor_count = cv2.filter2D(skel_binary, -1, kernel) * skel_binary

    endpoints = (neighbor_count == 1).astype(np.uint8)
    t_junctions = (neighbor_count == 3).astype(np.uint8)
    crossovers = (neighbor_count >= 4).astype(np.uint8)

    return endpoints, t_junctions, crossovers


class SkeletonLineTracer(BaseLineTracer):
    """Graph-based skeletonization line tracer with crossover vs T-junction classification."""

    def __init__(self, min_length_px: int = 40):
        self.min_length_px = min_length_px

    def trace(
        self,
        img_bgr: np.ndarray,
        dpi: int = 350,
        detections: Optional[List[Dict[str, Any]]] = None,
        furniture: Optional[List[List[int]]] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Extract pipe runs via skeletonization and junction analysis."""
        if progress:
            progress("Skeletonizing P&ID pipe network...")

        H, W = img_bgr.shape[:2]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr

        # Adaptive thresholding to capture clean pipe strokes
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 8
        )

        # Remove very small noise dots
        clean_binary = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        )

        # 1. Skeletonize
        skel = _morphological_skeleton(clean_binary)

        # 2. Extract linear segments using Hough on skeleton
        min_len = int(self.min_length_px * (dpi / 350.0))
        lines_p = cv2.HoughLinesP(
            skel,
            rho=1,
            theta=np.pi / 180,
            threshold=25,
            minLineLength=min_len,
            maxLineGap=int(12 * (dpi / 350.0)),
        )

        raw_runs = []
        if lines_p is not None:
            for line in lines_p:
                x1, y1, x2, y2 = line[0]
                dx, dy = abs(x2 - x1), abs(y2 - y1)
                axis = "h" if dx >= 3 * dy else ("v" if dy >= 3 * dx else "d")
                raw_runs.append({
                    "points": [[int(x1), int(y1)], [int(x2), int(y2)]],
                    "axis": axis,
                    "x1": int(min(x1, x2)),
                    "y1": int(min(y1, y2)),
                    "x2": int(max(x1, x2)),
                    "y2": int(max(y1, y2)),
                    "underline": False,
                })

        # 3. Apply standard suppressions (symbol edges, equipment interiors, furniture)
        filtered = suppress_box_edges(raw_runs, detections or [])
        filtered = suppress_equipment_interior(filtered, detections or [], margin=int(10 * dpi / 350))
        if furniture:
            filtered = suppress_furniture(filtered, furniture)

        boxes = detect_boxes(img_bgr)
        if boxes:
            filtered = suppress_box_outlines(filtered, boxes)

        return filtered
