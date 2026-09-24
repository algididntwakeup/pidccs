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

        # Furniture dan subtype tetap eksplisit di progres; tahap ini sebelumnya
        # terlihat macet di 65% walaupun YOLO tiled sudah selesai.
        if progress:
            progress("Mendeteksi furniture (title block/tabel)...")
        try:
            furniture = detect_furniture(img_bgr, self.layout_weights)
        except Exception:
            furniture = []

        if progress:
            progress("Mengklasifikasikan subtype simbol...")
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
        if progress:
            progress(f"Subtype selesai ({len(syms)} simbol)")

        # Stage 4: Pipe Line Tracing — HYBRID (vector-first, raster fallback).
        #
        # PDF vektor (AutoCAD/SmartPlant) menyimpan koordinat garis pipa secara
        # eksak. Mengekstraknya langsung dari geometri PDF jauh lebih cepat dan
        # menghasilkan garis 100% lurus, dibanding binarisasi + skeletonisasi
        # yang memakan waktu CPU dan menghasilkan garis bergerigi. Halaman scan
        # (raster) tidak punya geometri vektor, jadi otomatis memakai tracer
        # raster yang sudah ada (`SkeletonLineTracer` di production).
        if progress:
            progress("Tracing jalur pipa...")
        runs = None
        tracer_used = "raster"
        if str(image_path).lower().endswith(".pdf"):
            try:
                from .implementations.vector_tracer import (
                    tier_of_pdf, extract_vector_runs,
                )
                tier = tier_of_pdf(image_path)
                if tier in ("A1", "A2"):
                    if progress:
                        progress(f"PDF vektor (tier {tier}) — ekstraksi geometri vektor...")
                    # VECTOR_ENGINE: `pdfplumber` (default — mengutamakan kualitas
                    # hasil: /Rotate ditangani otomatis) atau `pymupdf` (~0.3 s,
                    # tetapi `rotation_matrix` wajib diterapkan manual).
                    engine = os.environ.get("VECTOR_ENGINE", "pdfplumber").lower()
                    vector_runs = extract_vector_runs(
                        image_path, dpi=dpi, rot=rot, progress=progress, engine=engine,
                    )
                    if vector_runs:
                        runs = vector_runs
                        tracer_used = f"vector:{tier}"
                        if progress:
                            progress(f"Tracing vektor selesai ({len(runs)} run)")
                    elif progress:
                        progress("Vektor tidak menghasilkan run — fallback ke raster...")
                elif progress:
                    progress(f"PDF raster (tier {tier}) — memakai skeleton tracer...")
            except Exception as e:
                if progress:
                    progress(f"Ekstraksi vektor dilewati ({e}) — fallback ke raster...")

        if runs is None:
            trace_kwargs = {
                "img_bgr": img_bgr,
                "dpi": dpi,
                "detections": syms,
                "furniture": furniture,
                "progress": progress,
            }
            import inspect
            sig = inspect.signature(self.tracer.trace)
            if "tokens" in sig.parameters:
                trace_kwargs["tokens"] = tokens
            if "pids" in sig.parameters:
                trace_kwargs["pids"] = pids
            runs = self.tracer.trace(**trace_kwargs)

        # Association: link piping IDs to pipe runs
        if progress:
            progress("Menghubungkan line number ke pipa...")
        assoc = associate(pids, runs, img_bgr, dpi=dpi)
        run_index = {id(r): i for i, r in enumerate(runs)}

        pid_recs = []
        run_label_map = {}
        for a in assoc:
            p = pids[a["pid_idx"]]
            ri = run_index.get(id(a["run"]), -1) if a["run"] is not None else -1
            pid_str = getattr(p, "pid", None) if not isinstance(p, dict) else p.get("pid", "")
            if ri >= 0 and pid_str:
                run_label_map[ri] = pid_str
            rec = {
                "pid": pid_str,
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
        for i, r in enumerate(runs):
            run_id = f"run-{i}"
            assigned_label = run_label_map.get(i, getattr(r, "label", getattr(r, "pid", "")))
            if hasattr(r, "points"):
                pts = [[int(x), int(y)] for x, y in r.points]
                axis = getattr(r, "axis", "poly")
                underline = bool(getattr(r, "underline", False))
                color = getattr(r, "color", "#2563EB")
                manual = bool(getattr(r, "manual", False))
            else:
                pts = r.get("points", [])
                axis = r.get("axis", "poly")
                underline = bool(r.get("underline", False))
                color = r.get("color", "#2563EB")
                manual = bool(r.get("manual", False))

            run_recs.append({
                "id": run_id,
                "points": pts,
                "axis": axis,
                "x1": min(p[0] for p in pts) if pts else 0,
                "y1": min(p[1] for p in pts) if pts else 0,
                "x2": max(p[0] for p in pts) if pts else 0,
                "y2": max(p[1] for p in pts) if pts else 0,
                "underline": underline,
                "color": color,
                "label": assigned_label or "",
                "manual": manual,
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
            "tracer": tracer_used,
        }

        # Split pipe runs at spec breaks
        if conn_pts:
            try:
                split_at_connection_points(result, dpi=dpi)
            except Exception:
                pass

        # Re-ensure every run has id, label, manual, color after any spec break split
        for idx, run_item in enumerate(result.get("runs", [])):
            if not run_item.get("id"):
                run_item["id"] = f"run-{idx}"
            if "color" not in run_item or not run_item["color"]:
                run_item["color"] = "#2563EB"
            if "label" not in run_item:
                run_item["label"] = ""
            if "manual" not in run_item:
                run_item["manual"] = False

        # Detect Off-Page Connectors (OPC)
        opcs = []
        try:
            from .opc_detector import detect_off_page_connectors
            opcs = detect_off_page_connectors(result, tokens=tokens, dpi=dpi)
        except Exception:
            pass
        result["opcs"] = opcs

        if progress:
            progress(f"Selesai dalam {time.time() - t0:.1f} detik.")

        return result

