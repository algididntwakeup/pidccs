"""Deteksi region FURNITURE (title block / notes / tabel info) pada P&ID.

Dipakai 2 tahap:
  1. Bootstrap: `furniture_boxes()` = heuristik untuk auto-label awal (dikoreksi di
     Roboflow), supaya penulis tidak melabeli dari nol.
  2. Runtime (setelah model dilatih): YOLO 'layout detector' 1-kelas 'furniture'
     menggantikan heuristik; region furniture di-mask agar tracing/OCR hanya proses
     AREA GAMBAR.

Prinsip heuristik (bootstrap): furniture = blok padat garis+teks yang membentuk
grid rapat, menempel tepi kertas. Border frame terluar dibuang dulu (kalau tidak,
seluruh cincin tepi menyambung jadi satu region — kegagalan pendekatan density lama).
"""
from __future__ import annotations
import os
import numpy as np
import cv2

_MODELS = {}                                  # cache per-weights (layout & equip_big beda model!)


def detect_fullpage(img_bgr, weights, conf=0.4, imgsz=1024, with_conf=False):
    """Inferensi YOLO 1-kelas FULL-PAGE (tanpa tiling) -> list box (x0,y0,x1,y1) piksel
    (atau (x0,y0,x1,y1,conf) bila with_conf). Dipakai utk region BESAR: furniture
    (title block/tabel) dan equipment besar (vessel/exchanger) — objek yg lebih besar
    dari tile 640 sehingga detektor tiled tak pernah melihatnya utuh.
    Kalau weights/torch tak ada -> [] (pipeline tetap jalan)."""
    if not weights or not os.path.exists(weights):
        return []
    try:
        import torch
        from ultralytics import YOLO
        if weights not in _MODELS:
            _MODELS[weights] = YOLO(weights)
        dev = 0 if torch.cuda.is_available() else "cpu"
        r = _MODELS[weights].predict(img_bgr, imgsz=imgsz, conf=conf, device=dev, verbose=False)[0]
        out = []
        for b in r.boxes:
            x0, y0, x1, y1 = (int(v) for v in b.xyxy[0].tolist())
            out.append((x0, y0, x1, y1, float(b.conf[0])) if with_conf else (x0, y0, x1, y1))
        return out
    except Exception:
        return []


def detect_furniture(img_bgr, weights, conf=0.4, imgsz=1024):
    """(kompat) deteksi furniture full-page — lihat detect_fullpage."""
    return detect_fullpage(img_bgr, weights, conf=conf, imgsz=imgsz)


def suppress_nested(boxes, contain=0.55):
    """Buang box yang sebagian besar TERKANDUNG dalam box lain yang lebih besar
    (irisan/luas_box_kecil > contain). Kasus nyata: equipment tinggi (vessel) kadang
    terdeteksi berlapis — fragmen atas/bawah + box utuh; NMS biasa tak menggabungkan
    karena IoU antar fragmen rendah. Input/output: (x0,y0,x1,y1[,conf])."""
    def area(b):
        return max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    keep = []
    for b in sorted(boxes, key=area, reverse=True):
        inside = False
        for k in keep:
            ix0, iy0 = max(b[0], k[0]), max(b[1], k[1])
            ix1, iy1 = min(b[2], k[2]), min(b[3], k[3])
            inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
            if area(b) > 0 and inter / area(b) > contain:
                inside = True; break
        if not inside:
            keep.append(b)
    return keep


def _ink(img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    return (gray < 128).astype(np.uint8)


def furniture_boxes(img_bgr, n_cells=90, cross_cover=0.5, min_area_frac=0.006,
                    edge_frac=0.14, merge_gap_frac=0.03):
    """HINT auto-label furniture (x0,y0,x1,y1 piksel) untuk bootstrap anotasi.

    CATATAN: ini HANYA tebakan kasar untuk mempercepat anotasi Roboflow, BUKAN
    detektor final. Heuristik murni mentok memisahkan furniture dari cluster simbol
    (bubble instrumen juga membentuk grid) -> maka dipakai model YOLO 'layout' hasil
    training (menggantikan fungsi ini saat runtime).

    Metode: densitas PERSILANGAN garis grid (H∩V) -> region rapat crossing; buang
    ring frame/tick; simpan hanya region BESAR & DEKAT TEPI (title block/tabel khas
    di tepi, cluster instrumen di interior dibuang).
    """
    ink = _ink(img_bgr)
    H, W = ink.shape
    L = max(12, int(0.006 * max(H, W)))
    hor = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
    ver = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (1, L)))
    inter = cv2.dilate(cv2.bitwise_and(hor, ver), np.ones((int(L * 1.5),) * 2, np.uint8))
    cell = max(8, int(max(H, W) / n_cells))
    gh, gw = H // cell, W // cell
    if gh < 8 or gw < 8:
        return []
    dens = cv2.resize(inter.astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA)
    dense = (dens > cross_cover).astype(np.uint8)
    dense[:2, :] = 0; dense[-2:, :] = 0; dense[:, :2] = 0; dense[:, -2:] = 0  # buang ring tick/frame
    dense = cv2.morphologyEx(dense, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)
    n, _, stats, _ = cv2.connectedComponentsWithStats(dense, 8)
    min_cells = max(6, int(min_area_frac * gh * gw))
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        cx, cy = (x + w / 2) / gw, (y + h / 2) / gh
        near_edge = (cx < edge_frac or cx > 1 - edge_frac or
                     cy < edge_frac - 0.01 or cy > 1 - edge_frac + 0.01)
        if area >= min_cells and near_edge:
            boxes.append([x * cell, y * cell, (x + w) * cell, (y + h) * cell])
    return _merge(boxes, int(merge_gap_frac * max(H, W)))


def _merge(boxes, gap):
    """Gabung box yang tumpang tindih / berdekatan (< gap px) secara iteratif."""
    boxes = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        while boxes:
            a = boxes.pop()
            merged = True
            while merged:
                merged = False
                rest = []
                for b in boxes:
                    if (a[0] - gap <= b[2] and b[0] - gap <= a[2] and
                            a[1] - gap <= b[3] and b[1] - gap <= a[3]):
                        a = [min(a[0], b[0]), min(a[1], b[1]),
                             max(a[2], b[2]), max(a[3], b[3])]
                        merged = True; changed = True
                    else:
                        rest.append(b)
                boxes = rest
            out.append(a)
        boxes = out
    return [tuple(b) for b in boxes]


def to_yolo(boxes, W, H, cls=0):
    """Box piksel -> baris label YOLO (cls cx cy w h ternormalisasi)."""
    lines = []
    for x0, y0, x1, y1 in boxes:
        cx = (x0 + x1) / 2 / W; cy = (y0 + y1) / 2 / H
        bw = (x1 - x0) / W; bh = (y1 - y0) / H
        lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return "\n".join(lines) + ("\n" if lines else "")


def draw(img_bgr, boxes, color=(0, 0, 255), thick=None):
    vis = img_bgr.copy()
    t = thick or max(2, img_bgr.shape[1] // 900)
    for x0, y0, x1, y1 in boxes:
        cv2.rectangle(vis, (int(x0), int(y0)), (int(x1), int(y1)), color, t)
    return vis
