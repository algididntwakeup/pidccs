"""
Sub-klasifikasi simbol (Fitur 1) — memberi KELAS BERMAKNA pada tiap simbol terdeteksi,
bukan sekadar 'instrument'/'valve'. Dua mekanisme, sesuai sifat objeknya:

1. INSTRUMENT -> OCR teks tag di dalam bubble + konvensi ISA-5.1 (DETERMINISTIK).
   Kode instrumen TERTULIS di simbolnya (PSV, PT, TIC, ...) dan maknanya dibakukan
   ISA-5.1 (huruf-1 = variabel: P/T/F/L...; huruf lanjutan = fungsi: I/C/T/SV...).
   Tidak butuh training sama sekali — pola yang sama dengan API 970 di Fitur 2:
   persepsi ML menemukan LOKASI, aturan standar menentukan MAKNA.

2. VALVE -> classifier bentuk khusus (valve_cls, YOLO11s-cls) yang dilatih dari
   440 crop valve drawing PetroChina sendiri (vision-label): gate (bowtie kosong),
   solid (bowtie terisi; asumsi = Globe gaya Chiyoda), globe (bowtie+titik), ball
   (bowtie+lingkaran), check (flap miring), control (beraktuator), relief (pegas),
   notvalve (deteksi salah). In-domain -> jauh lebih akurat dari transfer baseline.

3. EQUIPMENT -> disarankan oleh model baseline 20-kelas (pid20_baseline: Pump,
   Heat Exchanger, Filter, ...). Statusnya SARAN (transfer lintas-domain bisa
   noisy) — bisa dikoreksi user.

Field hasil: symbol['subtype'] (string, '' bila tak teridentifikasi).
"""
from __future__ import annotations
import re
import numpy as np
import cv2

_VALVE_MODELS = {}


# ------------------------------------------------------------------ ISA-5.1 -------
# Deskripsi kode instrumen umum (subset ISA-5.1 yang lazim di P&ID kilang).
ISA_DESC = {
    "PSV": "Pressure Safety Valve", "PRV": "Pressure Relief Valve",
    "PT": "Pressure Transmitter", "PI": "Pressure Indicator", "PG": "Pressure Gauge",
    "PIC": "Pressure Indicating Controller", "PIT": "Pressure Indicating Transmitter",
    "PDT": "Pressure Differential Transmitter", "PDI": "Pressure Differential Indicator",
    "PV": "Pressure (Control) Valve", "PSHH": "Pressure Switch High-High",
    "TT": "Temperature Transmitter", "TI": "Temperature Indicator",
    "TG": "Temperature Gauge", "TIC": "Temperature Indicating Controller",
    "TV": "Temperature (Control) Valve", "TE": "Temperature Element",
    "TA": "Temperature Alarm", "TZT": "Temperature Safety Transmitter",
    "FT": "Flow Transmitter", "FI": "Flow Indicator", "FE": "Flow Element",
    "FIC": "Flow Indicating Controller", "FV": "Flow (Control) Valve",
    "FC": "Flow Controller", "FO": "Flow Orifice (restriksi)",
    "LT": "Level Transmitter", "LI": "Level Indicator", "LG": "Level Gauge/Glass",
    "LIC": "Level Indicating Controller", "LV": "Level (Control) Valve",
    "LA": "Level Alarm", "LZT": "Level Safety Transmitter",
    "LSH": "Level Switch High", "LSL": "Level Switch Low",
    "LYC": "Level Computing Controller",
    "SDV": "Shutdown Valve", "BDV": "Blowdown Valve", "ESD": "Emergency Shutdown",
    "XV": "On-Off Valve", "ZI": "Position Indicator", "ZS": "Position Switch",
    "ZT": "Position Transmitter", "HS": "Hand Switch", "HIC": "Hand Indicating Controller",
    "HV": "Hand Valve", "XI": "Multivariable Indicator", "XA": "Multivariable Alarm",
    "UA": "Multivariable Alarm", "AI": "Analysis Indicator", "AT": "Analysis Transmitter",
    "SC": "Sample Connection", "RO": "Restriction Orifice", "SP": "Sample Point",
    "PY": "Pressure Relay/Compute", "TY": "Temperature Relay/Compute",
}
# huruf-pertama variabel proses yang sah menurut ISA-5.1 (dipakai utk validasi token)
_ISA_FIRST = set("ABCDEFGHIJKLMNPQRSTUVWXYZ")
_TAG_RE = re.compile(r"^[A-Z]{2,4}$")


def isa_describe(code: str) -> str:
    """Kode tag -> deskripsi (dari tabel; fallback generik berbasis huruf pertama)."""
    c = (code or "").upper()
    if c in ISA_DESC:
        return ISA_DESC[c]
    base = {"P": "Pressure", "T": "Temperature", "F": "Flow", "L": "Level",
            "A": "Analysis", "Z": "Position", "H": "Hand", "S": "Speed/Safety",
            "V": "Vibration", "X": "Multivariable"}.get(c[:1], "")
    return f"{base} instrument".strip() if base else "Instrument"


def classify_instruments(img_bgr, symbols, progress=None):
    """Isi symbol['subtype'] utk instrument via OCR crop bubble + validasi pola ISA.
    Aman dipanggil kapan pun; simbol non-instrument dilewati."""
    from .piping_id import get_rapid
    eng = get_rapid()
    H, W = img_bgr.shape[:2]
    inst = [s for s in symbols if s.get("coarse") == "instrument"]
    n_ok = 0
    for i, s in enumerate(inst):
        x1, y1, x2, y2 = int(s["x1"]), int(s["y1"]), int(s["x2"]), int(s["y2"])
        pw, ph = int((x2 - x1) * 0.15) + 2, int((y2 - y1) * 0.15) + 2
        crop = img_bgr[max(0, y1 - ph):min(H, y2 + ph), max(0, x1 - pw):min(W, x2 + pw)]
        if crop.size == 0:
            continue
        if crop.shape[0] < 72:                       # bubble kecil -> upscale utk OCR
            f = 72 / crop.shape[0]
            crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        try:
            res, _ = eng(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        except Exception:
            res = None
        cands = []
        for _box, text, _score in (res or []):
            for tok in re.split(r"[^A-Za-z]+", text.upper()):
                if _TAG_RE.match(tok) and tok[0] in _ISA_FIRST:
                    # prioritas: kode yang dikenal tabel > panjang token
                    cands.append((tok in ISA_DESC, len(tok), tok))
        if cands:
            s["subtype"] = max(cands)[2]
            n_ok += 1
        if progress and (i + 1) % 20 == 0:
            progress(f"subtype instrument {i + 1}/{len(inst)}...")
    return n_ok


# ------------------------------------------------- valve/equipment via baseline ---
# nama kelas baseline yang panjang -> label ringkas utk UI
SHORT_NAME = {
    "3 Way Gate Valve": "3-Way Valve", "4 Way Gate Valve": "4-Way Valve",
    "Ball Valve": "Ball Valve", "Butterfly Valve": "Butterfly Valve",
    "Check Valve": "Check Valve", "Diaphragm Valve": "Diaphragm Valve",
    "Diaphragm pneumatic control valve": "Control Valve",
    "Electromagnetic globe valve": "Globe Valve (elektrik)",
    "Gate Valve": "Gate Valve", "Globe Valve": "Globe Valve", "Plug Valve": "Plug Valve",
    "Rotary Piston-Pneumatic Gate Valve": "Gate Valve (pneumatik)",
    "Centrifugal Pump": "Centrifugal Pump", "Gear Pump": "Gear Pump",
    "Filter": "Filter", "Heat Exchanger": "Heat Exchanger",
}


def _iou(a, b):
    ix1, iy1 = max(a["x1"], b["x1"]), max(a["y1"], b["y1"])
    ix2, iy2 = min(a["x2"], b["x2"]), min(a["y2"], b["y2"])
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    ua = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"]) + \
         (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) - inter
    return inter / ua if ua > 0 else 0.0


def classify_valves_equipment(img_bgr, symbols, weights, conf=0.3, progress=None,
                              targets=("valve", "equipment")):
    """Isi symbol['subtype'] utk kelas di `targets` dari model baseline 20-kelas
    (pid20_baseline). SARAN — transfer lintas-domain bisa noisy, bisa dikoreksi user.
    Sejak ada valve_cls, pipeline memanggil ini hanya utk equipment.
    Kalau weights tak ada -> no-op (pipeline tetap jalan)."""
    import os
    if not weights or not os.path.exists(weights):
        return 0
    try:
        import torch
        from ultralytics import YOLO
        from .detect import predict_tiled, COARSE
        dev = 0 if torch.cuda.is_available() else "cpu"
        model = YOLO(weights)
        dets, _ = predict_tiled(model, img_bgr, tile=640, overlap=0.2, conf=conf, device=dev)
    except Exception:
        return 0
    fine = [{"x1": d.x1, "y1": d.y1, "x2": d.x2, "y2": d.y2,
             "cls": d.cls, "coarse": COARSE.get(d.cls, "other"), "conf": d.conf}
            for d in dets]
    n_ok = 0
    for s in symbols:
        if s.get("coarse") not in targets or s.get("cls") in ("equipment_big", "equipment_box"):
            continue
        best, bi = 0.25, None                        # syarat IoU minimal
        for f in fine:
            if f["coarse"] != s["coarse"]:
                continue
            v = _iou(s, f)
            if v > best:
                best, bi = v, f
        if bi is not None:
            s["subtype"] = SHORT_NAME.get(bi["cls"], bi["cls"])
            n_ok += 1
    return n_ok


# ------------------------------------------------- valve via classifier bentuk ----
# kelas classifier valve_cls -> nama tampil. 'solid' (bowtie terisi penuh) DIASUMSIKAN
# Globe Valve (konvensi Chiyoda/JIS: globe digambar pejal) — konfirmasi ke legend
# sheet; kalau ternyata beda, cukup ganti satu baris ini. 'notvalve' = deteksi salah
# -> subtype dikosongkan (simbol TIDAK dihapus otomatis; keputusan di user).
VALVE_CLS_NAME = {
    "gate": "Gate Valve", "solid": "Globe Valve", "globe": "Globe Valve",
    "ball": "Ball Valve", "check": "Check Valve", "control": "Control Valve",
    "relief": "Relief Valve", "notvalve": "",
}
_VALVE_PAD = 0.30      # padding crop — HARUS sama dgn saat membangun dataset training


def classify_valve_crops(img_bgr, symbols, weights, conf_thr=0.6, progress=None):
    """Isi subtype valve; model YOLO classifier di-cache per path proses."""
    import os
    if not weights or not os.path.exists(weights):
        return 0
    try:
        import torch
        from ultralytics import YOLO
        model = _VALVE_MODELS.get(weights)
        if model is None:
            model = YOLO(weights)
            _VALVE_MODELS[weights] = model
        dev = 0 if torch.cuda.is_available() else "cpu"
    except Exception:
        return 0
    H, W = img_bgr.shape[:2]
    pairs = []                                      # (symbol, crop) valid saja
    for s in symbols:
        if s.get("coarse") != "valve":
            continue
        x1, y1, x2, y2 = s["x1"], s["y1"], s["x2"], s["y2"]
        pw, ph = (x2 - x1) * _VALVE_PAD, (y2 - y1) * _VALVE_PAD
        crop = img_bgr[int(max(0, y1 - ph)):int(min(H, y2 + ph)),
                       int(max(0, x1 - pw)):int(min(W, x2 + pw))]
        if crop.size:
            pairs.append((s, crop))
    n_ok, B = 0, 64
    for i in range(0, len(pairs), B):
        batch = pairs[i:i + B]
        try:
            results = model([c for _, c in batch], imgsz=128, device=dev, verbose=False)
        except Exception:
            break
        for (s, _), r in zip(batch, results):
            name = VALVE_CLS_NAME.get(r.names[int(r.probs.top1)], "")
            if name and float(r.probs.top1conf) >= conf_thr:
                s["subtype"] = name
                n_ok += 1
        if progress:
            progress(f"subtype valve {min(i + B, len(pairs))}/{len(pairs)}...")
    return n_ok


# ------------------------------------------------------------------ tampilan ------
def sym_class(s) -> str:
    """Nama kelas BERMAKNA utk UI/filter: subtype bila ada; bila belum terklasifikasi
    lebih halus -> '<Kelas> lainnya' (bukan '(umum)' yg membingungkan)."""
    sub = (s.get("subtype") or "").strip()
    if sub:
        return sub
    return {"equipment": "Other equipment", "instrument": "Other instrument",
            "valve": "Other valve"}.get(s.get("coarse"), "Other")
