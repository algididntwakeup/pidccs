"""Compare run counts before/after text-artifact suppression on a real P&ID.

Usage:
    python -m scripts.compare_runs_count "/path/to/pid.pdf" [page] [dpi]
"""
import sys

from app.adapters.pdf_renderer import load_drawing_image
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer


def total_len(run):
    pts = run.points
    return sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in zip(pts, pts[1:]))


def main():
    pdf = sys.argv[1]
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    print(f"Loading {pdf} p{page} @{dpi}dpi ...")
    img = load_drawing_image(pdf, dpi=dpi, page_number=page)

    tokens = []
    try:
        from pidcorr.implementations.rapidocr_extractor import RapidOCRExtractor
        _pids, toks = RapidOCRExtractor().extract(img_bgr=img, tile=1200)
        tokens = [dict(t) for t in toks]
        print(f"  OCR tokens = {len(tokens)}")
    except Exception as e:
        print(f"  [warn] OCR: {e}")

    detections = []
    try:
        from pidcorr.implementations.yolo_detector import YOLOTiledDetector
        detections = YOLOTiledDetector().detect(img_bgr=img, conf=0.30)
        print(f"  detections = {len(detections)}")
    except Exception as e:
        print(f"  [warn] detector: {e}")

    print("Tracing WITHOUT text-artifact suppression ...")
    t_off = SkeletonLineTracer(min_length_px=12, suppress_text_artifacts=False,
                               suppress_floating_stubs=False)
    runs_off = t_off.trace(img, dpi=dpi, detections=detections, furniture=[], tokens=tokens)

    print("Tracing WITH text-artifact suppression ...")
    t_on = SkeletonLineTracer(min_length_px=12, suppress_text_artifacts=True,
                              suppress_floating_stubs=True)
    runs_on = t_on.trace(img, dpi=dpi, detections=detections, furniture=[], tokens=tokens)

    t_on2 = SkeletonLineTracer(min_length_px=12, suppress_text_artifacts=True,
                               suppress_floating_stubs=False)
    runs_stage2 = t_on2.trace(img, dpi=dpi, detections=detections, furniture=[], tokens=tokens)

    def summ(runs, name):
        if not runs:
            print(f"  {name}: 0 runs")
            return
        lens = [total_len(r) for r in runs]
        short = sum(1 for L in lens if L < 60)
        diag = sum(1 for r in runs if getattr(r, "axis", "") == "d")
        print(f"  {name}: {len(runs)} runs | short(<60px)={short} | diag={diag} "
              f"| total_len={sum(lens):.0f}")

    summ(runs_off, "WITHOUT        ")
    summ(runs_stage2, "STAGE2 only    ")
    summ(runs_on, "STAGE2+POSTFILT")
    diff = len(runs_off) - len(runs_on)
    pct = (100.0 * diff / len(runs_off)) if runs_off else 0
    print(f"  >>> removed {diff} runs ({pct:.1f}%) total")


if __name__ == "__main__":
    main()
