"""B.07 benchmark: tiled YOLO versus SAHI on the symbol test set."""

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT_DIR = _BACKEND_DIR.parent
for path in (str(_BACKEND_DIR), str(_ROOT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pidcorr.implementations.sahi_detector import SAHISymbolDetector
from pidcorr.implementations.yolo_detector import YOLOTiledDetector


CLASSES = ("equipment", "instrument", "valve")


def _label_for(image: Path) -> Path:
    labels = _ROOT_DIR / "data" / "equip_test" / "labels"
    suffix = image.stem.lower().split("__", 1)[-1].replace("-ccd2", "-ccd")
    matches = [p for p in labels.glob("*.txt")
               if p.stem.lower().split("__", 1)[-1].replace("-ccd2", "-ccd") == suffix]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one label for {image.name}, found {len(matches)}")
    return matches[0]


def _load_truth(image: Path):
    height, width = cv2.imread(str(image)).shape[:2]
    records = []
    for line in _label_for(image).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != 5:
            continue
        cls, cx, cy, bw, bh = map(float, fields)
        records.append((int(cls), ((cx - bw / 2) * width, (cy - bh / 2) * height,
                                   (cx + bw / 2) * width, (cy + bh / 2) * height)))
    return records


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return inter / (area_a + area_b - inter) if area_a + area_b - inter else 0.0


def _evaluate(predictions, truths):
    metrics = {}
    for class_name in CLASSES:
        class_id = CLASSES.index(class_name)
        gt_total = sum(sum(cls == class_id for cls, _ in rows) for rows in truths.values())
        candidates = []
        for image, rows in truths.items():
            used = set()
            for pred in [p for p in predictions[image] if p[1] == class_name]:
                best = max(((idx, _iou(pred[2], box)) for idx, (cls, box) in enumerate(rows)
                            if cls == class_id and idx not in used), key=lambda x: x[1], default=(-1, 0.0))
                is_tp = best[1] >= 0.5
                if is_tp:
                    used.add(best[0])
                candidates.append((pred[0], is_tp))
        candidates.sort(key=lambda item: item[0], reverse=True)
        tp = np.cumsum([int(ok) for _, ok in candidates])
        fp = np.cumsum([int(not ok) for _, ok in candidates])
        recall = tp / gt_total if gt_total else np.array([])
        precision = tp / np.maximum(tp + fp, 1)
        ap = 0.0
        if gt_total:
            recall_points = np.r_[0.0, recall, 1.0]
            precision_points = np.r_[0.0, precision, 0.0]
            for idx in range(len(precision_points) - 2, -1, -1):
                precision_points[idx] = max(precision_points[idx], precision_points[idx + 1])
            changes = np.where(recall_points[1:] != recall_points[:-1])[0]
            ap = float(np.sum((recall_points[changes + 1] - recall_points[changes]) * precision_points[changes + 1]))
        metrics[class_name] = {
            "ap50": ap,
            "recall": float(tp[-1] / gt_total) if gt_total and len(tp) else 0.0,
            "gt": gt_total,
        }
    metrics["mAP50"] = sum(item["ap50"] for item in metrics.values()) / len(CLASSES)
    return metrics


def _run(detector, images):
    predictions = {}
    started = time.perf_counter()
    for image in images:
        result = detector.detect(cv2.imread(str(image)), conf=0.3)
        predictions[image] = [(float(item.get("conf", 0.0)), item.get("coarse", "other"),
                              (item["x1"], item["y1"], item["x2"], item["y2"])) for item in result]
    return predictions, (time.perf_counter() - started) / len(images)


def main():
    image_dir = _ROOT_DIR / "data" / "equip_test" / "images"
    images = sorted(image_dir.glob("*.png"))
    truths = {image: _load_truth(image) for image in images}
    print(f"B.07 SAHI A/B benchmark: {len(images)} labelled images, IoU threshold 0.50")
    print("Note: labels with no objects contribute zero ground-truth objects.")
    results = {}
    for name, detector in (("YOLOTiled", YOLOTiledDetector()), ("SAHI", SAHISymbolDetector())):
        predictions, latency = _run(detector, images)
        results[name] = (_evaluate(predictions, truths), latency)
        print(f"  {name}: {latency:.2f}s/sheet")
    print("\n+------------+----------+-------------------+-------------------+-------------------+")
    print("| Detector  | mAP@0.5 | Equipment Recall% | Instrument Recall% | Valve Recall%   |")
    print("+------------+----------+-------------------+-------------------+-------------------+")
    for name, (metrics, _latency) in results.items():
        print(f"| {name:10s} | {metrics['mAP50'] * 100:8.2f} | "
              f"{metrics['equipment']['recall'] * 100:17.2f} | "
              f"{metrics['instrument']['recall'] * 100:17.2f} | "
              f"{metrics['valve']['recall'] * 100:15.2f} |")
    print("+------------+----------+-------------------+-------------------+-------------------+")


if __name__ == "__main__":
    main()
