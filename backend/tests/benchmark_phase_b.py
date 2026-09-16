"""A/B benchmark for the Phase B OCR adapters.

The six benchmark sheets are selected from the Excel ground truth entries that
also have a matching raster/PDF drawing in ``Contoh P&ID``.  The ground truth
does not contain OCR boxes, so locating recall is measured by recovered line
number identity rather than box overlap.
"""

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import openpyxl

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = Path(os.path.abspath(os.path.join(_BACKEND_DIR, "..")))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from app.adapters.pdf_renderer import load_drawing_image
from pidcorr.implementations.paddleocr_extractor import PaddleOCRExtractor
from pidcorr.implementations.rapidocr_extractor import RapidOCRExtractor


def _key(value: str) -> str:
    """Compare line IDs independent of whitespace and inch-mark spelling."""
    return str(value or "").upper().replace(" ", "").replace('"', "")


def _ground_truth() -> dict[str, set[str]]:
    by_source: dict[str, set[str]] = defaultdict(set)
    for workbook_path in sorted((_ROOT_DIR / "combined_dataset").glob("*.xlsx")):
        workbook = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, ())
            try:
                source_col = header.index("Source File")
                pid_col = header.index("Piping ID")
            except ValueError:
                continue
            for row in rows:
                if len(row) > max(source_col, pid_col) and row[source_col] and row[pid_col]:
                    by_source[str(row[source_col]).lower()].add(_key(row[pid_col]))
        workbook.close()
    return dict(by_source)


def _samples(gt: dict[str, set[str]]) -> list[tuple[Path, set[str]]]:
    drawings = {
        path.stem.lower(): path
        for path in sorted((_ROOT_DIR / "Contoh P&ID").glob("*"))
        if path.suffix.lower() in {".png", ".pdf"}
    }
    selected = []
    for source, expected in sorted(gt.items()):
        stem = Path(source).stem.lower()
        drawing = drawings.get(stem)
        if drawing is None:
            continue
        # Prefer PNG because it avoids an unnecessary PDF rasterization step.
        png = drawing.with_suffix(".png")
        if png.exists():
            drawing = png
        selected.append((drawing, expected))
        if len(selected) == 6:
            break
    if len(selected) != 6:
        raise RuntimeError(f"Expected 6 matched samples, found {len(selected)}")
    return selected


def _run_extractor(extractor, image):
    started = time.perf_counter()
    pids, _tokens = extractor.extract(image)
    elapsed = time.perf_counter() - started
    found = {_key(item.pid) for item in pids}
    return found, elapsed


def _metrics(results: list[tuple[set[str], float, set[str]]], expected_total: int) -> tuple[float, float, float]:
    found_total = sum(len(found) for found, _, _ in results)
    correct_total = sum(len(found & expected) for found, _, expected in results)
    recall = 100.0 * correct_total / expected_total if expected_total else 0.0
    accuracy = 100.0 * correct_total / found_total if found_total else 0.0
    latency = sum(elapsed for _, elapsed, _ in results) / len(results)
    return recall, accuracy, latency


def run_benchmark() -> None:
    gt = _ground_truth()
    samples = _samples(gt)
    print("\n=======================================================")
    print("   PHASE B.05 OCR A/B BENCHMARK — P&ID Studio Web")
    print("   Metric: identity-based recall/accuracy (no GT boxes)")
    print("=======================================================\n")
    print("Samples:")
    for path, expected in samples:
        print(f"  - {path.name} ({len(expected)} GT IDs)")

    extractors = {
        "RapidOCR": RapidOCRExtractor(),
        "PaddleOCR": PaddleOCRExtractor(),
    }
    all_results = {name: [] for name in extractors}
    expected_total = sum(len(expected) for _, expected in samples)

    for sample_path, expected in samples:
        image = load_drawing_image(str(sample_path), dpi=150)
        print(f"\n{sample_path.name}")
        for name, extractor in extractors.items():
            found, elapsed = _run_extractor(extractor, image)
            all_results[name].append((found, elapsed, expected))
            correct = len(found & expected)
            print(f"  {name:10s}: {correct}/{len(expected)} located, "
                  f"{len(found)} returned, {elapsed:.2f}s")

    print("\n+------------+-------------------+------------------+-------------------+")
    print("| Extractor  | Locating Recall % | Text Accuracy % | Latency (s/sheet) |")
    print("+------------+-------------------+------------------+-------------------+")
    for name, results in all_results.items():
        recall, accuracy, latency = _metrics(results, expected_total)
        print(f"| {name:10s} | {recall:17.2f} | {accuracy:16.2f} | {latency:17.2f} |")
    print("+------------+-------------------+------------------+-------------------+")
    print(f"\nAggregate ground-truth IDs: {expected_total}")


if __name__ == "__main__":
    run_benchmark()
