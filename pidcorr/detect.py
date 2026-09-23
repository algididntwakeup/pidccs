"""
Tiled YOLO inference untuk P&ID besar (A3) + pemetaan ke taksonomi kasar.

P&ID kita ~3300px sedangkan YOLO dilatih pada tile 640px. Maka:
  - potong gambar jadi tile overlap (SAHI-style),
  - inferensi tiap tile,
  - geser kotak ke koordinat gambar penuh,
  - NMS antar-tile untuk buang duplikat di area overlap,
  - petakan 20 kelas dataset Roboflow -> {equipment, instrument, valve}.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import cv2

# --- pemetaan 20 kelas (pid-vktlg) -> taksonomi kasar proyek ---
COARSE = {
    "3 Way Gate Valve": "valve", "4 Way Gate Valve": "valve", "Ball Valve": "valve",
    "Butterfly Valve": "valve", "Check Valve": "valve", "Diaphragm Valve": "valve",
    "Diaphragm pneumatic control valve": "valve", "Electromagnetic globe valve": "valve",
    "Gate Valve": "valve", "Globe Valve": "valve", "Plug Valve": "valve",
    "Rotary Piston-Pneumatic Gate Valve": "valve",
    "Centrifugal Pump": "equipment", "Filter": "equipment", "Gear Pump": "equipment",
    "Heat Exchanger": "equipment",
    "Measurement Instrument": "instrument", "localMeasurement Instrument": "instrument",
    "device KKS": "instrument", "local device KKS": "instrument",
}
COARSE_COLORS = {  # BGR
    "equipment": (0, 140, 255), "instrument": (0, 180, 0),
    "valve": (255, 60, 0), "other": (128, 128, 128),
}


@dataclass
class Det:
    x1: float; y1: float; x2: float; y2: float
    conf: float; cls: str; coarse: str


def coarse_of(name: str) -> str:
    # model hasil fine-tune sudah pakai nama kelas kasar langsung
    if name in ("equipment", "instrument", "valve"):
        return name
    return COARSE.get(name, "other")


def tile_offsets(size: int, tile: int, overlap: float) -> list[int]:
    """Offset awal tile sepanjang satu sumbu, menjamin tile terakhir menutup tepi."""
    if size <= tile:
        return [0]
    step = max(1, int(round(tile * (1 - overlap))))
    offs = list(range(0, size - tile + 1, step))
    if offs[-1] != size - tile:
        offs.append(size - tile)
    return offs


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
    """NMS greedy class-agnostic (buang duplikat dari overlap tile)."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]]); yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]]); yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0, xx2 - xx1); h = np.maximum(0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thr]
    return keep


def predict_tiled(model, img_bgr: np.ndarray, tile: int = 640, overlap: float = 0.2,
                  conf: float = 0.25, iou_merge: float = 0.5, device="cpu") -> tuple[list[Det], int]:
    """Inferensi YOLO ber-tile pada gambar penuh. return (deteksi, jumlah tile)."""
    try:
        import torch as _torch
        use_half = bool(_torch.cuda.is_available())
    except Exception:
        use_half = False
    H, W = img_bgr.shape[:2]
    xs, ys = tile_offsets(W, tile, overlap), tile_offsets(H, tile, overlap)
    boxes, scores, names = [], [], []
    for oy in ys:
        for ox in xs:
            crop = img_bgr[oy:oy + tile, ox:ox + tile]
            res = model.predict(crop, conf=conf, imgsz=tile, device=device, half=use_half, verbose=False)[0]
            for b in res.boxes:
                x1, y1, x2, y2 = b.xyxy[0].tolist()
                boxes.append([x1 + ox, y1 + oy, x2 + ox, y2 + oy])
                scores.append(float(b.conf[0]))
                names.append(model.names[int(b.cls[0])])
    n_tiles = len(xs) * len(ys)
    if not boxes:
        return [], n_tiles
    keep = _nms(np.array(boxes, dtype=float), np.array(scores, dtype=float), iou_merge)
    dets = [Det(*boxes[i], scores[i], names[i], coarse_of(names[i])) for i in keep]
    dets.sort(key=lambda d: d.conf, reverse=True)
    return dets, n_tiles


def draw(img_bgr: np.ndarray, dets: list[Det], thickness: int = 2) -> np.ndarray:
    vis = img_bgr.copy()
    for d in dets:
        col = COARSE_COLORS.get(d.coarse, COARSE_COLORS["other"])
        cv2.rectangle(vis, (int(d.x1), int(d.y1)), (int(d.x2), int(d.y2)), col, thickness)
        cv2.putText(vis, f"{d.coarse[:4]} {d.conf:.2f}", (int(d.x1), int(d.y1) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
    return vis


def to_yolo_labels(dets: list[Det], img_w: int, img_h: int, class_list: list[str]) -> str:
    """Export label YOLO (pakai indeks kelas kasar) untuk koreksi / fine-tune."""
    idx = {c: i for i, c in enumerate(class_list)}
    lines = []
    for d in dets:
        if d.coarse not in idx:
            continue
        cx = (d.x1 + d.x2) / 2 / img_w
        cy = (d.y1 + d.y2) / 2 / img_h
        w = (d.x2 - d.x1) / img_w
        h = (d.y2 - d.y1) / img_h
        lines.append(f"{idx[d.coarse]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    return "\n".join(lines)
