import os
import sys
import time
import cv2
import numpy as np

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from app.adapters.pdf_renderer import load_drawing_image
from pidcorr.implementations.yolo_detector import YOLOTiledDetector
from pidcorr.implementations.sahi_detector import SAHISymbolDetector
from pidcorr.implementations.morphology_tracer import MorphologyLineTracer
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer
from pidcorr.factory import get_configured_orchestrator


def run_benchmark():
    sample_file = os.path.join(_ROOT_DIR, "Contoh P&ID", "BCD3-605-42-PID-1-005-01 Rev.4-CCD2.png")
    if not os.path.exists(sample_file):
        sample_file = os.path.join(_ROOT_DIR, "Contoh P&ID", "BCD3-605-42-PID-1-005-01 Rev.4-CCD2.pdf")

    print(f"\n=======================================================")
    print(f"   PHASE B PERCEPTION BENCHMARK — P&ID Studio Web")
    print(f"   Test drawing: {os.path.basename(sample_file)}")
    print(f"=======================================================\n")

    img = load_drawing_image(sample_file, dpi=150)  # fast benchmark resolution
    H, W = img.shape[:2]
    print(f"Drawing dimensions: {W} x {H} px\n")

    # -------------------------------------------------------------
    # 1. Benchmark Symbol Detection: Standard Tiled vs SAHI
    # -------------------------------------------------------------
    print("[1/2] Benchmarking Symbol Detectors...")
    yolo_tiled = YOLOTiledDetector()
    t0 = time.time()
    syms_tiled = yolo_tiled.detect(img, conf=0.30)
    dur_tiled = time.time() - t0
    print(f"  -> YOLOTiledDetector:  {len(syms_tiled)} symbols detected in {dur_tiled:.2f}s")

    sahi_detector = SAHISymbolDetector()
    t0 = time.time()
    syms_sahi = sahi_detector.detect(img, conf=0.30)
    dur_sahi = time.time() - t0
    print(f"  -> SAHISymbolDetector: {len(syms_sahi)} symbols detected in {dur_sahi:.2f}s")

    # -------------------------------------------------------------
    # 2. Benchmark Line Tracing: Morphology vs Skeleton
    # -------------------------------------------------------------
    print("\n[2/2] Benchmarking Line Tracers...")
    morph_tracer = MorphologyLineTracer()
    t0 = time.time()
    runs_morph = morph_tracer.trace(img, dpi=150, detections=syms_tiled)
    dur_morph = time.time() - t0
    print(f"  -> MorphologyLineTracer: {len(runs_morph)} pipe runs in {dur_morph:.2f}s")

    skel_tracer = SkeletonLineTracer()
    t0 = time.time()
    runs_skel = skel_tracer.trace(img, dpi=150, detections=syms_tiled)
    dur_skel = time.time() - t0
    print(f"  -> SkeletonLineTracer:   {len(runs_skel)} pipe runs in {dur_skel:.2f}s")

    print("\n=======================================================")
    print("   BENCHMARK COMPLETED SUCCESSFULLY")
    print("=======================================================\n")


if __name__ == "__main__":
    run_benchmark()
