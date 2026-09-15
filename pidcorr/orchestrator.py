import os
import time
from typing import Callable, Optional, Dict, Any, List
import numpy as np

from .interfaces.perception import (
    BaseSymbolDetector,
    BaseTextExtractor,
    BaseLineTracer,
    BaseSubtypeClassifier,
)
from .implementations.yolo_detector import YOLOTiledDetector
from .implementations.rapidocr_extractor import RapidOCRExtractor
from .implementations.morphology_tracer import MorphologyLineTracer
from .implementations.yolo_classifier import YOLOValveClassifier

from .layout import detect_furniture
from .lines import associate
from .connpoint import find_connection_points, class_vocab
from .propagate import split_at_connection_points, propagate_labels


def _dedup_pid_recs(recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate piping ID records by canonical line number."""
    by_pid = {}
    for r in recs:
        k = r["pid"]
        if k not in by_pid or r.get("conf", 0) > by_pid[k].get("conf", 0):
            by_pid[k] = r
    return list(by_pid.values())


class PipelineOrchestrator:
    """Dependency-injected orchestrator for the P&ID digitization pipeline."""

    def __init__(
        self,
        detector: Optional[BaseSymbolDetector] = None,
        extractor: Optional[BaseTextExtractor] = None,
        tracer: Optional[BaseLineTracer] = None,
        classifier: Optional[BaseSubtypeClassifier] = None,
        layout_weights: Optional[str] = None,
    ):
        self.detector = detector or YOLOTiledDetector()
        self.extractor = extractor or RapidOCRExtractor()
        self.tracer = tracer or MorphologyLineTracer()
        self.classifier = classifier or YOLOValveClassifier()
        if layout_weights is None:
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            layout_weights = os.path.join(_root, "runs", "detect", "layout_furniture", "weights", "best.pt")
        self.layout_weights = layout_weights

    def run(
        self,
        img_bgr: np.ndarray,
        image_path: str = "",
        dpi: int = 350,
        rot: int = 0,
        progress: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Execute complete digitization pipeline using configured component adapters."""
        H, W = img_bgr.shape[:2]
        t0 = time.time()

        # Stage 1: Piping ID OCR & candidate token extraction
        if progress:
            progress("Membaca teks line number (OCR)...")
        pids, tokens = self.extractor.extract(img_bgr=img_bgr, tile=1200, progress=progress)

        # Stage 2: Symbol Detection
        if progress:
            progress("Mendeteksi simbol equipment, valve, dan instrument...")
        syms = self.detector.detect(img_bgr=img_bgr, conf=0.30, progress=progress)

        # Stage 3: Furniture Detection (Title blocks, notes, drawing borders)
        try:
            furniture = detect_furniture(img_bgr, self.layout_weights)
        except Exception:
            furniture = []

        # Subtype classification for valves & instruments
        for s in syms:
            if s.get("coarse") == "valve" and not s.get("subtype"):
                try:
                    s["subtype"] = self.classifier.classify_valve(img_bgr, s)
                except Exception:
                    pass
            elif s.get("coarse") == "instrument" and not s.get("subtype"):
                try:
                    func, loop = self.classifier.classify_instrument(img_bgr, s)
                    if func:
                        s["subtype"] = func
                        s["isa_func"] = func
                        s["isa_loop"] = loop
                except Exception:
                    pass

        # Stage 4: Pipe Line Tracing
        if progress:
            progress("Tracing jalur pipa...")
        runs = self.tracer.trace(
            img_bgr=img_bgr,
            dpi=dpi,
            detections=syms,
            furniture=furniture,
            progress=progress,
        )

        # Association: link piping IDs to pipe runs
        if progress:
            progress("Menghubungkan line number ke pipa...")
        assoc = associate(pids, runs, img_bgr, dpi=dpi)
        run_index = {id(r): i for i, r in enumerate(runs)}

        pid_recs = []
        for a in assoc:
            p = pids[a["pid_idx"]]
            ri = run_index.get(id(a["run"]), -1) if a["run"] is not None else -1
            rec = {
                "pid": getattr(p, "pid", None) if not isinstance(p, dict) else p.get("pid", ""),
                "x1": float(getattr(p, "x1", 0) if not isinstance(p, dict) else p.get("x1", 0)),
                "y1": float(getattr(p, "y1", 0) if not isinstance(p, dict) else p.get("y1", 0)),
                "x2": float(getattr(p, "x2", 0) if not isinstance(p, dict) else p.get("x2", 0)),
                "y2": float(getattr(p, "y2", 0) if not isinstance(p, dict) else p.get("y2", 0)),
                "unit": getattr(p, "unit", "") if not isinstance(p, dict) else p.get("unit", ""),
                "size": getattr(p, "size", "") if not isinstance(p, dict) else p.get("size", ""),
                "fluid": getattr(p, "fluid", "") if not isinstance(p, dict) else p.get("fluid", ""),
                "pclass": getattr(p, "pclass", "") if not isinstance(p, dict) else p.get("pclass", ""),
                "seq": getattr(p, "seq", "") if not isinstance(p, dict) else p.get("seq", ""),
                "conf": int(getattr(p, "conf", 0) if not isinstance(p, dict) else p.get("conf", 0)),
                "run_idx": int(ri),
                "state": a["state"],
                "manual": False,
                "extra_runs": [],
            }
            pid_recs.append(rec)

        pid_recs = _dedup_pid_recs(pid_recs)

        run_recs = []
        for r in runs:
            if hasattr(r, "points"):
                run_recs.append({
                    "points": [[int(x), int(y)] for x, y in r.points],
                    "axis": getattr(r, "axis", "poly"),
                    "x1": int(r.x1),
                    "y1": int(r.y1),
                    "x2": int(r.x2),
                    "y2": int(r.y2),
                    "underline": bool(getattr(r, "underline", False)),
                })
            else:
                pts = r.get("points", [])
                run_recs.append({
                    "points": pts,
                    "axis": r.get("axis", "poly"),
                    "x1": int(r.get("x1", pts[0][0] if pts else 0)),
                    "y1": int(r.get("y1", pts[0][1] if pts else 0)),
                    "x2": int(r.get("x2", pts[-1][0] if pts else 0)),
                    "y2": int(r.get("y2", pts[-1][1] if pts else 0)),
                    "underline": bool(r.get("underline", False)),
                })

        # Stage 5: Connection Points (Spec breaks) detection
        if progress:
            progress("Mendeteksi connection point (spec break)...")
        vocab = class_vocab(extra=[p.get("pclass", "") for p in pid_recs if p.get("pclass")])
        conn_pts = find_connection_points(
            tokens=tokens,
            runs=run_recs,
            img_bgr=img_bgr,
            dpi=dpi,
            vocab=vocab,
        )

        result = {
            "image_path": image_path,
            "dpi": dpi,
            "rot": rot,
            "w": int(W),
            "h": int(H),
            "symbols": syms,
            "furniture": furniture,
            "runs": run_recs,
            "conn_points": conn_pts,
            "piping_ids": pid_recs,
        }

        # Split pipe runs at spec breaks
        if conn_pts:
            try:
                split_at_connection_points(result, dpi=dpi)
            except Exception:
                pass

        if progress:
            progress(f"Selesai dalam {time.time() - t0:.1f} detik.")

        return result

