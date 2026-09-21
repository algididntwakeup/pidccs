"""Diagnostic: measure text-artifact islands surviving OCR masking before skeletonization.

Usage (inside container):
    python -m scripts.diag_trace_artifacts "/path/to/pid.pdf" [page_index]

It reproduces the exact pre-skeleton binary produced by SkeletonLineTracer.trace,
then reports connected-component geometry to identify candidate text islands.
"""
import sys
import numpy as np
import cv2

from app.adapters.pdf_renderer import load_drawing_image
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer


def build_pre_skeleton(img_bgr, dpi, detections, furniture, tokens):
    """Mirror SkeletonLineTracer.trace stages 0-2 (binary -> masks applied)."""
    H, W = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 6
    )
    clean_binary = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    )
    # OCR dilated masking
    dil_r = max(3, int(5 * (dpi / 350.0)))
    for t in tokens:
        tx1 = int(t.get("x1", 0)); ty1 = int(t.get("y1", 0))
        tx2 = int(t.get("x2", 0)); ty2 = int(t.get("y2", 0))
        if tx2 > tx1 and ty2 > ty1:
            clean_binary[max(0, ty1 - dil_r):min(H, ty2 + dil_r),
                         max(0, tx1 - dil_r):min(W, tx2 + dil_r)] = 0
    # equipment interior masking
    eq_margin = max(3, int(4 * (dpi / 350.0)))
    for d in detections:
        if d.get("coarse") == "equipment":
            ex1 = int(d.get("x1", 0)) + eq_margin; ey1 = int(d.get("y1", 0)) + eq_margin
            ex2 = int(d.get("x2", 0)) - eq_margin; ey2 = int(d.get("y2", 0)) - eq_margin
            if ex2 > ex1 and ey2 > ey1:
                clean_binary[ey1:ey2, ex1:ex2] = 0
    for f in (furniture or []):
        fx1, fy1, fx2, fy2 = f
        clean_binary[max(0, int(fy1)):min(H, int(fy2)),
                     max(0, int(fx1)):min(W, int(fx2))] = 0
    return binary, clean_binary


def analyze(clean_binary, min_len, label):
    ink = (clean_binary > 0).astype(np.uint8)
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(ink, 8)
    H, W = ink.shape
    # Bucket components by geometry
    buckets = {"tiny_dot": 0, "text_like": 0, "elongated": 0, "large": 0}
    text_like = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 8:
            buckets["tiny_dot"] += 1
            continue
        ar = max(w, h) / max(1, min(w, h))
        if w <= 50 and h <= 50 and 12 <= area <= 900 and ar < 4.0:
            buckets["text_like"] += 1
            text_like.append((x, y, w, h, area, round(ar, 2)))
        elif max(w, h) > 250:
            buckets["large"] += 1
        else:
            buckets["elongated"] += 1
    print(f"--- {label} ---")
    print(f"  total components      : {n - 1}")
    print(f"  tiny_dot  (area<8)    : {buckets['tiny_dot']}")
    print(f"  text_like (candidate) : {buckets['text_like']}")
    print(f"  elongated (pipes etc) : {buckets['elongated']}")
    print(f"  large                 : {buckets['large']}")
    print(f"  sample text_like bboxes (first 15):")
    for b in text_like[:15]:
        print(f"     x={b[0]:5d} y={b[1]:5d} w={b[2]:3d} h={b[3]:3d} area={b[4]:4d} ar={b[5]}")
    return text_like


def candidate_filter_params(clean_binary):
    """Report candidate text islands + safety analysis on pipe-like geometry."""
    ink = (clean_binary > 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    H, W = ink.shape
    removed = 0
    kept = 0
    MAX_SIDE = 50
    MIN_AREA = 12
    MAX_AREA = 900
    MAX_AR = 4.0
    keep_stats = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        ar = max(w, h) / max(1, min(w, h))
        if area < MIN_AREA:
            removed += 1
            continue
        if w <= MAX_SIDE and h <= MAX_SIDE and area <= MAX_AREA and ar < MAX_AR:
            removed += 1
        else:
            kept += 1
            keep_stats.append((w, h, area, round(ar, 2)))
    print(f"  >>> Proposed filter would blackout {removed} components, keep {kept}")
    print(f"  kept sample (w,h,area,ar): {keep_stats[:12]}")
    # Thickness of REMAINING thin components that could be short pipe stubs:
    # a kept thin comp is either elongated pipe or large. Verify none kept is
    # a tiny elbow by listing kept comps with max_side < 60.
    small_kept = [k for k in keep_stats if max(k[0], k[1]) < 60]
    print(f"  kept comps with max_side<60 (potential elbow risk): {len(small_kept)}")
    for k in small_kept[:10]:
        print(f"     w={k[0]} h={k[1]} area={k[2]} ar={k[3]}")
    return removed, kept


def main():
    pdf = sys.argv[1]
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    print(f"Loading {pdf} page {page} @ {dpi} dpi ...")
    img = load_drawing_image(pdf, dpi=dpi, page_number=page)
    print(f"  image shape = {img.shape}")

    # Use real OCR to reproduce the exact mask state seen by the tracer.
    tokens = []
    detections = []
    furniture = []

    try:
        from pidcorr.implementations.paddleocr_extractor import PaddleOCRExtractor
        ext = PaddleOCRExtractor()
        _pids, tokens = ext.extract(img_bgr=img, tile=1200)
        tokens = [dict(t) if isinstance(t, dict) else {
            "x1": int(getattr(t, "x1", 0)), "y1": int(getattr(t, "y1", 0)),
            "x2": int(getattr(t, "x2", 0)), "y2": int(getattr(t, "y2", 0)),
            "text": getattr(t, "text", ""),
        } for t in tokens]
        print(f"  OCR tokens = {len(tokens)}")
    except Exception as e:
        print(f"  [warn] OCR failed: {e}")

    raw_binary, clean = build_pre_skeleton(img, dpi, detections, furniture, tokens)
    analyze(raw_binary, 0, "RAW adaptive-threshold binary")
    analyze(clean, int(12 * dpi / 350.0), "AFTER OCR + equipment masking (pre-skeleton)")
    candidate_filter_params(clean)


if __name__ == "__main__":
    main()
