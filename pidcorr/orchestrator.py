import os
import time
import copy
from typing import Callable, Optional, Dict, Any, List, Literal
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
from .lines import associate, PipeRun
from .piping_id import PipingID
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
        mode: Literal["full", "lines_only"] = "full",
    ) -> Dict[str, Any]:
        """Execute complete digitization pipeline using configured component adapters.

        When mode == "lines_only":
            - Skips Stage 1 (OCR) -> pids = [], tokens = []
            - Skips Stage 2 (YOLO) -> syms = []
            - Runs Stage 4 (Line Tracing) directly
            - Skips Association (pids empty)
        """
        H, W = img_bgr.shape[:2]
        t0 = time.time()

        # Stage 0: Early PDF tier detection — BEFORE any heavy computation.
        pdf_tier = None
        is_vector_pdf = False
        if str(image_path).lower().endswith(".pdf"):
            try:
                from .implementations.vector_tracer import (
                    tier_of_pdf, extract_vector_runs,
                )
                pdf_tier = tier_of_pdf(image_path)
                is_vector_pdf = pdf_tier in ("A1", "A2")
                if progress:
                    if is_vector_pdf:
                        progress(f"PDF vektor terdeteksi (tier {pdf_tier}) — ekstraksi langsung...")
                    else:
                        progress(f"PDF raster (tier {pdf_tier}) — tracing pipa...")
            except Exception as e:
                if progress:
                    progress(f"Deteksi tier PDF gagal ({e}) — fallback...")

        if mode == "lines_only":
            pids = []
            tokens = []
            syms = []
            furniture = []
            if progress:
                progress("Mode 'lines_only' aktif: bypass OCR & YOLO, langsung mengekstrak garis...")
        else:
            # Stage 1: Piping ID OCR & candidate token extraction
            if progress:
                progress("Membaca teks line number (OCR)...")
            pids, tokens = self.extractor.extract(img_bgr=img_bgr, tile=1200, progress=progress)

            # Stage 2: Symbol Detection
            if is_vector_pdf:
                if progress:
                    progress("YOLO tiled di-bypass (vektor CAD) — deteksi simbol ringan...")
                try:
                    syms = self.detector.detect(img_bgr=img_bgr, conf=0.40, progress=progress,
                                                 tile=0)
                except TypeError:
                    syms = []
            else:
                if progress:
                    progress("Mendeteksi simbol equipment, valve, dan instrument...")
                syms = self.detector.detect(img_bgr=img_bgr, conf=0.30, progress=progress)

            if self.layout_weights and os.path.exists(self.layout_weights):
                if progress:
                    progress("Mendeteksi furniture (title block/tabel)...")
                try:
                    furniture = detect_furniture(img_bgr, self.layout_weights)
                except Exception:
                    furniture = []
            else:
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
        if progress:
            progress("Tracing jalur pipa...")
        runs = None
        tracer_used = "raster"
        if is_vector_pdf:
            try:
                if progress:
                    progress(f"PDF vektor (tier {pdf_tier}) — ekstraksi geometri vektor...")
                engine = os.environ.get("VECTOR_ENGINE", "pdfplumber").lower()
                vector_runs = extract_vector_runs(
                    image_path, dpi=dpi, rot=rot, progress=progress, engine=engine,
                )
                if vector_runs:
                    runs = vector_runs
                    tracer_used = f"vector:{pdf_tier}"
                    if progress:
                        progress(f"Tracing vektor selesai ({len(runs)} run)")
                elif progress:
                    progress("Vektor tidak menghasilkan run — fallback ke raster...")
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
        if mode != "lines_only" and pids:
            normalized_pids = []
            for p in pids:
                if isinstance(p, dict):
                    normalized_pids.append(PipingID(
                        pid=p.get("pid", ""),
                        x1=float(p.get("x1", 0)),
                        y1=float(p.get("y1", 0)),
                        x2=float(p.get("x2", 0)),
                        y2=float(p.get("y2", 0)),
                        unit=p.get("unit", ""),
                        size=p.get("size", ""),
                        fluid=p.get("fluid", ""),
                        pclass=p.get("pclass", ""),
                        seq=p.get("seq", ""),
                        conf=int(p.get("conf", 0)),
                        orient=int(p.get("orient", 0)),
                        manual=bool(p.get("manual", False)),
                    ))
                else:
                    normalized_pids.append(p)
            pids = normalized_pids

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
        else:
            pid_recs = []
            run_label_map = {}

        run_recs = []
        for i, r in enumerate(runs):
            run_id = getattr(r, "id", None) or (r.get("id") if isinstance(r, dict) else None) or f"run-{i}"
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
        conn_pts = []
        if mode != "lines_only" and tokens:
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

        # Split pipe runs at spec breaks (only if connection points detected)
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
        if mode != "lines_only" and tokens:
            try:
                from .opc_detector import detect_off_page_connectors
                opcs = detect_off_page_connectors(result, tokens=tokens, dpi=dpi)
            except Exception:
                pass
        result["opcs"] = opcs

        if progress:
            progress(f"Selesai dalam {time.time() - t0:.1f} detik.")

        return result

    def run_enrichment(
        self,
        img_bgr: np.ndarray,
        existing_runs: List[Any],
        image_path: str = "",
        dpi: int = 350,
        rot: int = 0,
        progress: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Execute on-demand enrichment (OCR, YOLO symbols, layout, association) on existing pipe runs.

        Preserves existing run identities (IDs, coordinates, manual markings) while attaching newly
        discovered line numbers (labels) and detecting symbols & connection points.
        """
        t0 = time.time()
        H, W = img_bgr.shape[:2]

        pdf_tier = None
        is_vector_pdf = False
        if str(image_path).lower().endswith(".pdf"):
            try:
                from .implementations.vector_tracer import tier_of_pdf
                pdf_tier = tier_of_pdf(image_path)
                is_vector_pdf = pdf_tier in ("A1", "A2")
            except Exception:
                pass

        # 1. OCR (Piping ID & candidate tokens)
        if progress:
            progress("Membaca teks line number (OCR)...")
        pids, tokens = self.extractor.extract(img_bgr=img_bgr, tile=1200, progress=progress)

        # Normalize pids to PipingID instances if they are dicts
        normalized_pids = []
        for p in pids:
            if isinstance(p, dict):
                normalized_pids.append(PipingID(
                    pid=p.get("pid", ""),
                    x1=float(p.get("x1", 0)),
                    y1=float(p.get("y1", 0)),
                    x2=float(p.get("x2", 0)),
                    y2=float(p.get("y2", 0)),
                    unit=p.get("unit", ""),
                    size=p.get("size", ""),
                    fluid=p.get("fluid", ""),
                    pclass=p.get("pclass", ""),
                    seq=p.get("seq", ""),
                    conf=int(p.get("conf", 0)),
                    orient=int(p.get("orient", 0)),
                    manual=bool(p.get("manual", False)),
                ))
            else:
                normalized_pids.append(p)
        pids = normalized_pids

        # 2. YOLO Symbol Detection
        if is_vector_pdf:
            if progress:
                progress("YOLO tiled di-bypass (vektor CAD) — deteksi simbol ringan...")
            try:
                syms = self.detector.detect(img_bgr=img_bgr, conf=0.40, progress=progress, tile=0)
            except TypeError:
                syms = []
        else:
            if progress:
                progress("Mendeteksi simbol equipment, valve, dan instrument...")
            syms = self.detector.detect(img_bgr=img_bgr, conf=0.30, progress=progress)

        # 3. Furniture & Subtype Classification
        if self.layout_weights and os.path.exists(self.layout_weights):
            if progress:
                progress("Mendeteksi furniture (title block/tabel)...")
            try:
                furniture = detect_furniture(img_bgr, self.layout_weights)
            except Exception:
                furniture = []
        else:
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

        # 4. Normalize existing runs to PipeRun objects for association
        run_objs = []
        for i, r in enumerate(existing_runs):
            if isinstance(r, PipeRun):
                run_objs.append(r)
            elif isinstance(r, dict):
                pts = [[int(pt[0]), int(pt[1])] for pt in r.get("points", [])]
                pr = PipeRun(
                    points=pts,
                    axis=r.get("axis", "poly"),
                    pid=r.get("label", r.get("pid", "")),
                    fluid=r.get("fluid", ""),
                    underline=bool(r.get("underline", False)),
                    color=r.get("color", "#2563EB"),
                    id=r.get("id", f"run-{i}"),
                    label=r.get("label", ""),
                    manual=bool(r.get("manual", False)),
                    equipment_outline=bool(r.get("equipment_outline", False)),
                )
                run_objs.append(pr)
            else:
                run_objs.append(r)

        # 5. Association between newly extracted pids and existing runs
        if progress:
            progress("Menghubungkan line number ke pipa eksisting...")
        assoc = associate(pids, run_objs, img_bgr, dpi=dpi) if (pids and run_objs) else []
        run_index = {id(r): i for i, r in enumerate(run_objs)}

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

        # 6. Update existing runs with new labels WITHOUT changing run IDs or structure
        updated_runs = copy.deepcopy(existing_runs)
        for i, r in enumerate(updated_runs):
            new_label = run_label_map.get(i)
            if not new_label:
                continue
            if isinstance(r, dict):
                # Don't overwrite if manual user edit already assigned a custom label
                if not r.get("manual") or not r.get("label"):
                    r["label"] = new_label
            else:
                if not getattr(r, "manual", False) or not getattr(r, "label", ""):
                    r.label = new_label

        # 7. Connection points & OPCs
        if progress:
            progress("Mendeteksi connection point (spec break)...")
        vocab = class_vocab(extra=[p.get("pclass", "") for p in pid_recs if p.get("pclass")])
        runs_dict_list = []
        for r in updated_runs:
            if isinstance(r, dict):
                runs_dict_list.append(r)
            elif hasattr(r, "to_dict"):
                runs_dict_list.append(r.to_dict())
            else:
                runs_dict_list.append({
                    "points": getattr(r, "points", []),
                    "underline": getattr(r, "underline", False),
                    "axis": getattr(r, "axis", "poly"),
                    "x1": getattr(r, "x1", 0),
                    "y1": getattr(r, "y1", 0),
                    "x2": getattr(r, "x2", 0),
                    "y2": getattr(r, "y2", 0),
                })
        conn_pts = find_connection_points(
            tokens=tokens,
            runs=runs_dict_list,
            img_bgr=img_bgr,
            dpi=dpi,
            vocab=vocab,
        )

        opcs = []
        try:
            from .opc_detector import detect_off_page_connectors
            temp_result = {
                "runs": updated_runs,
                "symbols": syms,
                "piping_ids": pid_recs,
                "w": int(W),
                "h": int(H),
            }
            opcs = detect_off_page_connectors(temp_result, tokens=tokens, dpi=dpi)
        except Exception:
            pass

        if progress:
            progress(f"Enrichment selesai dalam {time.time() - t0:.1f} detik.")

        return {
            "symbols": syms,
            "furniture": furniture,
            "piping_ids": pid_recs,
            "runs": updated_runs,
            "conn_points": conn_pts,
            "opcs": opcs,
        }
