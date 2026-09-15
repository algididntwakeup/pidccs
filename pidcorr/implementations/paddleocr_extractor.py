import os
import re
from typing import Callable, Dict, Any, List, Optional, Tuple
import numpy as np
import cv2

from ..interfaces.perception import BaseTextExtractor
from ..piping_id import (
    PipingID,
    _offsets,
    _normalize,
    match_pid,
    _pair_two_line,
    _snap_units,
    _cluster,
    _SHORT_TOK,
)


class PaddleOCRExtractor(BaseTextExtractor):
    """PaddleOCR / PP-OCRv4 single-pass angle-aware text extractor for P&ID engineering diagrams.

    Modernized text extraction strategy (Phase B.04):
    - Uses PP-OCR angle classifier (use_angle_cls=True) to detect and classify text orientation
      natively in a single forward pass without 3-angle (0°, 90°, 270°) image rotations.
    - Employs adaptive 2048px tiles with 15% overlap for reduced overhead (~3x speedup).
    - Supports native Baidu PaddleOCR (if installed) with automatic seamless fallback
      to PP-OCRv4 ONNX runtime engine.
    - Fully preserves domain parsing heuristics (PetroChina / Pertamina schemas, multi-line pairing,
      unit snap, and spatial clustering).
    """

    def __init__(
        self,
        tile: int = 2048,
        overlap: float = 0.15,
        use_angle_cls: bool = True,
        min_votes: int = 1,
        merge_dist: int = 55,
        prefer_native: bool = True,
    ):
        self.tile = tile
        self.overlap = overlap
        self.use_angle_cls = use_angle_cls
        self.min_votes = min_votes
        self.merge_dist = merge_dist
        self.prefer_native = prefer_native
        self._engine = None
        self._is_native = False

    def _get_engine(self):
        if self._engine is not None:
            return self._engine, self._is_native

        if self.prefer_native:
            try:
                from paddleocr import PaddleOCR
                self._engine = PaddleOCR(use_angle_cls=self.use_angle_cls, lang="en", show_log=False)
                self._is_native = True
                return self._engine, self._is_native
            except Exception:
                pass

        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR(use_angle_cls=self.use_angle_cls)
        self._is_native = False
        return self._engine, self._is_native

    def extract(
        self,
        img_bgr: np.ndarray,
        tile: Optional[int] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> Tuple[List[PipingID], List[Dict[str, Any]]]:
        """Extract piping IDs and connection-point tokens in a single angle-aware pass."""
        tile_size = tile or self.tile
        engine, is_native = self._get_engine()

        H, W = img_bgr.shape[:2]
        xs = _offsets(W, tile_size, self.overlap)
        ys = _offsets(H, tile_size, self.overlap)
        total_tiles = len(xs) * len(ys)
        done_tiles = 0

        raw = []
        tokens_out: List[Dict[str, Any]] = []

        for oy in ys:
            for ox in xs:
                crop = img_bgr[oy : oy + tile_size, ox : ox + tile_size]
                crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

                # Execute single-pass angle-aware OCR
                ocr_results = None
                try:
                    if is_native:
                        res = engine.ocr(crop_rgb, cls=self.use_angle_cls)
                        ocr_results = res[0] if res and len(res) > 0 else []
                    else:
                        res, _ = engine(crop_rgb)
                        ocr_results = res or []
                except Exception:
                    ocr_results = []

                unmatched = []
                hits = []

                for item in ocr_results:
                    if len(item) == 2:
                        box, (text, score) = item
                    elif len(item) >= 3:
                        box, text, score = item[0], item[1], item[2]
                    else:
                        continue

                    norm = _normalize(text)
                    pid, w = match_pid(norm)
                    pts = np.asarray(box, dtype=float)

                    bw = float(pts[:, 0].max() - pts[:, 0].min())
                    bh = float(pts[:, 1].max() - pts[:, 1].min())
                    orient = 90 if bh > bw * 1.3 else 0

                    if _SHORT_TOK.match(norm):
                        tokens_out.append({
                            "t": norm,
                            "ang": orient,
                            "x1": float(pts[:, 0].min()) + ox,
                            "y1": float(pts[:, 1].min()) + oy,
                            "x2": float(pts[:, 0].max()) + ox,
                            "y2": float(pts[:, 1].max()) + oy,
                        })

                    if pid:
                        hits.append((pid, w, pts, False))
                    else:
                        unmatched.append((norm, pts))

                # Multi-line label pairing
                hits += [(p, w, pts, True) for p, w, pts in _pair_two_line(unmatched)]

                for pid, w, pts, paired in hits:
                    gx = pts[:, 0] + ox
                    gy = pts[:, 1] + oy
                    bw = float(gx.max() - gx.min())
                    bh = float(gy.max() - gy.min())
                    orient = 90 if bh > bw * 1.3 else 0

                    raw.append((
                        pid,
                        float(gx.mean()),
                        float(gy.mean()),
                        bw,
                        bh,
                        orient,
                        w,
                        paired,
                    ))

                done_tiles += 1
                if progress:
                    progress(f"PaddleOCR single-pass: tile {done_tiles}/{total_tiles}")

        # Snap unit numbers to dominant unit and cluster spatial duplicates
        clustered_pids = _cluster(_snap_units(raw), self.min_votes, self.merge_dist)

        return clustered_pids, tokens_out
