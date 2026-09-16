"""A/B benchmark for Phase B line tracers: MorphologyLineTracer vs SkeletonLineTracer.

Evaluates line run count, axis distribution, continuity length, association rate
with piping IDs, and latency across 6 sample P&ID drawings.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = Path(os.path.abspath(os.path.join(_BACKEND_DIR, "..")))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from app.adapters.pdf_renderer import load_drawing_image
from pidcorr.implementations.morphology_tracer import MorphologyLineTracer
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer
from pidcorr.lines import associate
from pidcorr.piping_id import PipingID


BENCHMARK_SAMPLES = [
    "BCD3-605-42-PID-1-001-01 REV.6J0-CCD2",
    "BCD3-605-42-PID-1-004-01 REV.4J00-CCD2",
    "BCD3-605-42-PID-1-005-01 Rev.4-CCD2",
    "BCD3-605-42-PID-1-006-01 Rev.10-CCD2",
    "BCD3-605-42-PID-1-007-01 REv.9-CCD2",
    "BCD3-605-42-PID-1-007-02 Rev.2-CCD2",
]


def load_sample_data(sample_name: str) -> Dict[str, Any]:
    cache_path = _ROOT_DIR / ".pidcache" / f"{sample_name}.pidcorr.json"
    if not cache_path.exists():
        raise FileNotFoundError(f"Cache data not found: {cache_path}")

    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    img_path = _ROOT_DIR / data["image_path"]
    if not img_path.exists():
        # Fallback to Contoh P&ID folder
        img_path = _ROOT_DIR / "Contoh P&ID" / f"{sample_name}.png"
    if not img_path.exists():
        img_path = _ROOT_DIR / "Contoh P&ID" / f"{sample_name}.pdf"

    img = load_drawing_image(str(img_path))
    data_w = data.get("w", img.shape[1])
    scale = img.shape[1] / float(data_w) if data_w else 1.0

    symbols = [
        {
            "x1": s["x1"] * scale,
            "y1": s["y1"] * scale,
            "x2": s["x2"] * scale,
            "y2": s["y2"] * scale,
            "coarse": s.get("coarse", ""),
        }
        for s in data.get("symbols", [])
    ]
    furniture = [[int(c * scale) for c in box] for box in data.get("furniture", [])]
    pids = [
        PipingID(
            pid=p["pid"],
            x1=p["x1"] * scale,
            y1=p["y1"] * scale,
            x2=p["x2"] * scale,
            y2=p["y2"] * scale,
        )
        for p in data.get("piping_ids", [])
    ]

    return {
        "name": sample_name,
        "image": img,
        "symbols": symbols,
        "furniture": furniture,
        "pids": pids,
        "dpi": data.get("dpi", 350),
    }


def evaluate_tracer(tracer, sample: Dict[str, Any]) -> Dict[str, Any]:
    img = sample["image"]
    dpi = sample["dpi"]
    symbols = sample["symbols"]
    furniture = sample["furniture"]
    pids = sample["pids"]

    t0 = time.perf_counter()
    runs = tracer.trace(img, dpi=dpi, detections=symbols, furniture=furniture)
    latency = time.perf_counter() - t0

    lengths = [r.length for r in runs]
    mean_len = sum(lengths) / len(lengths) if lengths else 0.0
    sorted_len = sorted(lengths)
    med_len = sorted_len[len(sorted_len) // 2] if sorted_len else 0.0
    max_len = max(lengths) if lengths else 0.0

    h_count = sum(1 for r in runs if getattr(r, "axis", "") == "h")
    v_count = sum(1 for r in runs if getattr(r, "axis", "") == "v")
    d_count = sum(1 for r in runs if getattr(r, "axis", "") in ("d", "poly", "manual"))

    # Association
    assoc = associate(pids, runs, img, dpi=dpi)
    attached_count = sum(1 for a in assoc if a.get("state") in ("attached", "leader"))
    assoc_rate = (attached_count / len(pids) * 100.0) if pids else 0.0

    return {
        "run_count": len(runs),
        "latency": latency,
        "mean_len": mean_len,
        "med_len": med_len,
        "max_len": max_len,
        "h_count": h_count,
        "v_count": v_count,
        "d_count": d_count,
        "attached_count": attached_count,
        "total_pids": len(pids),
        "assoc_rate": assoc_rate,
    }


def run_benchmark() -> None:
    print("\n=========================================================================")
    print("   PHASE B.10 TRACING A/B BENCHMARK — P&ID Studio Web")
    print("   Comparing: MorphologyLineTracer vs SkeletonLineTracer")
    print("=========================================================================\n")

    tracers = {
        "Morphology": MorphologyLineTracer(),
        "Skeleton": SkeletonLineTracer(min_length_px=40),
    }

    per_sample_results: Dict[str, Dict[str, Dict[str, Any]]] = {}
    aggregate_results: Dict[str, Dict[str, float]] = {
        name: {
            "total_runs": 0,
            "total_latency": 0.0,
            "total_attached": 0,
            "total_pids": 0,
            "lengths": [],
        }
        for name in tracers
    }

    for sample_name in BENCHMARK_SAMPLES:
        print(f"Loading {sample_name}...")
        sample = load_sample_data(sample_name)
        per_sample_results[sample_name] = {}
        print(f"  P&ID Drawing: {sample['image'].shape}, Symbols: {len(sample['symbols'])}, PIDs: {len(sample['pids'])}")

        for t_name, tracer in tracers.items():
            metrics = evaluate_tracer(tracer, sample)
            per_sample_results[sample_name][t_name] = metrics

            agg = aggregate_results[t_name]
            agg["total_runs"] += metrics["run_count"]
            agg["total_latency"] += metrics["latency"]
            agg["total_attached"] += metrics["attached_count"]
            agg["total_pids"] += metrics["total_pids"]
            agg["lengths"].append(metrics["mean_len"])

            print(
                f"  [{t_name:10s}] {metrics['run_count']:4d} runs | "
                f"{metrics['attached_count']:2d}/{metrics['total_pids']:2d} attached ({metrics['assoc_rate']:5.1f}%) | "
                f"H:{metrics['h_count']:3d} V:{metrics['v_count']:3d} D:{metrics['d_count']:3d} | "
                f"AvgLen:{metrics['mean_len']:5.1f}px | Latency: {metrics['latency']:.3f}s"
            )

    print("\n" + "=" * 80)
    print("                      AGGREGATE BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"{'Tracer':12s} | {'Total Runs':10s} | {'Avg Runs/Sheet':14s} | {'Association Rate':18s} | {'Avg Latency (s)':15s}")
    print("-" * 80)

    for t_name in tracers:
        agg = aggregate_results[t_name]
        n_sheets = len(BENCHMARK_SAMPLES)
        avg_runs = agg["total_runs"] / n_sheets
        overall_assoc = (agg["total_attached"] / agg["total_pids"] * 100.0) if agg["total_pids"] else 0.0
        avg_latency = agg["total_latency"] / n_sheets

        print(
            f"{t_name:12s} | {agg['total_runs']:10d} | {avg_runs:14.1f} | "
            f"{agg['total_attached']:3d}/{agg['total_pids']:3d} ({overall_assoc:5.1f}%) | "
            f"{avg_latency:15.3f}s"
        )
    print("=" * 80 + "\n")


if __name__ == "__main__":
    run_benchmark()
