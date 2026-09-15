"""
Metrik performa MODEL AI (digitisasi) untuk panel Report GUI.

Sumber = artefak training Ultralytics YOLO di `runs/`: `results.csv` (precision/recall/mAP
per epoch) + `confusion_matrix*.png` + `*PR_curve.png`. Angka & gambar ini DIREGENERASI tiap
kali model dilatih ulang -> saat penulis mengoreksi deteksi di GUI (tambah equipment, hapus
FP) lalu retrain dgn `data/feedback`, panel Report otomatis menampilkan performa TERBARU.

Ini metrik 'Jenis A' (dev-time, terhadap val/test berlabel) — mengukur seberapa bagus lapis
PERSEPSI (AI). Berbeda dari cek konsistensi runtime & agreement grouping vs ground truth.
"""
import os
import csv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (nama tampilan, task, folder run, tipe)
MODELS = [
    ("Simbol E/P/I (finetune 3-kelas)", "detect", "pid3_finetune", "detect"),
    ("Equipment besar (full-page)", "detect", "equip_big", "detect"),
    ("Baseline 20-kelas publik", "detect", "pid20_baseline", "detect"),
    ("Valve subtype (classifier)", "classify", "valve_cls", "classify"),
]


def _read_last_row(csv_path):
    try:
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return None
        return {k.strip(): v for k, v in rows[-1].items()}, len(rows)
    except Exception:
        return None


def _f(row, key, default=None):
    try:
        return float(row[key])
    except Exception:
        return default


def model_metrics():
    """List dict per model: {name, exists, epochs, precision, recall, map50, map, acc,
    confusion, confusion_norm, pr_curve, updated(epoch mtime)}."""
    out = []
    for name, task, run, kind in MODELS:
        d = os.path.join(ROOT, "runs", task, run)
        csv_path = os.path.join(d, "results.csv")
        rec = {"name": name, "run": run, "kind": kind, "dir": d, "exists": False}
        r = _read_last_row(csv_path)
        if r:
            row, n = r
            rec.update(exists=True, epochs=n)
            if kind == "classify":
                rec["acc"] = _f(row, "metrics/accuracy_top1")
            else:
                rec["precision"] = _f(row, "metrics/precision(B)")
                rec["recall"] = _f(row, "metrics/recall(B)")
                rec["map50"] = _f(row, "metrics/mAP50(B)")
                rec["map"] = _f(row, "metrics/mAP50-95(B)")
            for attr, fn in (("confusion", "confusion_matrix.png"),
                             ("confusion_norm", "confusion_matrix_normalized.png"),
                             ("pr_curve", "BoxPR_curve.png")):
                p = os.path.join(d, fn)
                rec[attr] = p if os.path.exists(p) else None
            wp = os.path.join(d, "weights", "best.pt")
            rec["updated"] = os.path.getmtime(wp) if os.path.exists(wp) else \
                (os.path.getmtime(csv_path) if os.path.exists(csv_path) else None)
        out.append(rec)
    return out


def holdout_recall():
    """Recall equipment pada TEST SET holdout (data/equip_test) dgn model saat ini —
    'seberapa bagus SEKARANG'. Return dict {tp,fn,fp,recall,n} atau None bila gagal."""
    test = os.path.join(ROOT, "data", "equip_test")
    weights = os.path.join(ROOT, "runs", "detect", "equip_big", "weights", "best.pt")
    if not (os.path.isdir(test) and os.path.exists(weights)):
        return None
    try:
        import glob
        import cv2
        from .layout import detect_fullpage, suppress_nested
    except Exception:
        return None

    def iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
        return inter / u if u else 0

    tp = fn = fp = 0
    imgs = sorted(glob.glob(os.path.join(test, "images", "*.png")))
    for imgp in imgs:
        stem = os.path.splitext(os.path.basename(imgp))[0]
        lblp = os.path.join(test, "labels", stem + ".txt")
        if not os.path.exists(lblp):
            continue
        img = cv2.imread(imgp)
        if img is None:
            continue
        H, W = img.shape[:2]
        gts = []
        for ln in open(lblp).read().splitlines():
            if not ln.strip():
                continue
            _, cx, cy, w, h = map(float, ln.split())
            gts.append(((cx-w/2)*W, (cy-h/2)*H, (cx+w/2)*W, (cy+h/2)*H))
        preds = suppress_nested(detect_fullpage(img, weights, conf=0.25, with_conf=True))
        used = set()
        for g in gts:
            best, bi = 0.30, None
            for i, p in enumerate(preds):
                if i in used:
                    continue
                v = iou(g, p[:4])
                if v > best:
                    best, bi = v, i
            if bi is not None:
                tp += 1; used.add(bi)
            else:
                fn += 1
        fp += len(preds) - len(used)
    n = tp + fn
    return {"tp": tp, "fn": fn, "fp": fp, "n": n,
            "recall": (tp / n * 100.0) if n else 0.0} if n else None
