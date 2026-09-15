"""
Deteksi CONNECTION POINT (penanda spec break) pada P&ID — Fitur 1f.

Connection point = notasi baku di P&ID yang menandai titik PERUBAHAN piping class di
sepanjang satu jalur pipa. Bentuknya sepasang kode class pendek yang menghimpit sebuah
garis pembatas (division line), mis:

    BCB --->|<--- CCB          (pasangan MENDATAR, pembatas tegak)

              ASA
            -------            (pasangan BERTUMPUK, pembatas mendatar)
              CCB

Kenapa penting (kontribusi utama modul ini):
  * Batas circuit yang DITULIS EKSPLISIT oleh perancang drawing. Circuitization yang
    hanya membaca piping class dari line number tidak melihat spec break yang terjadi di
    TENGAH satu line number — connection point menutup celah itu.
  * Jadi BARRIER saat propagasi label antar pipa (lihat propagate.py): pewarnaan berhenti
    persis di titik spec break, bukan menyeberang ke circuit tetangga.
  * Jadi bahan CROSS-CHECK otomatis: pasangan (A,B) di gambar dibandingkan dengan class
    hasil parsing line number di kedua sisi -> ketidakcocokan = temuan validasi.

Referensi: Toral dkk. (2021), "A deep learning digitisation framework to mark up corrosion
circuits in P&IDs" (ICDAR 2021 Workshops) — mendeteksi pipe spec + connection point dengan
DUA model YOLOv5 terpisah (1653 + 537 anotasi manual). Modul ini mencapai tujuan yang sama
secara RULE-BASED di atas hasil OCR yang sudah ada: tanpa anotasi, tanpa training, dan
deterministik (auditable) — sejalan dengan prinsip proyek: ML hanya di lapis persepsi,
logika korosi berbasis aturan.

CATATAN: modul ini tidak menerjemahkan kode class (CLAUDE.md §6.3) — kode dipakai apa adanya.
"""
from __future__ import annotations
import os, json

# ------------------------------------------------- registry kode piping class -----
# Vocab kode class dikumpulkan AKUMULATIF dari seluruh drawing yang pernah diproses di
# project ini (bukan daftar hardcode) -> sheet yang line number-nya sedikit tetap bisa
# mengenali connection point yang kodenya muncul di sheet lain. General lintas perusahaan.
_CLS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".pidcache", "piping_classes.json")


def class_vocab(extra=()):
    """Himpunan kode piping class yang dikenal: registry project + material_map.csv +
    `extra` (kode dari drawing yang sedang diproses). Kode baru ikut disimpan."""
    try:
        with open(_CLS_PATH, encoding="utf-8") as f:
            reg = set(json.load(f))
    except Exception:
        reg = set()
    new = {c.strip().upper() for c in extra if c and c.strip()}
    if new - reg:
        reg |= new
        try:
            os.makedirs(os.path.dirname(_CLS_PATH), exist_ok=True)
            tmp = _CLS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(sorted(reg), f, indent=1)
            os.replace(tmp, _CLS_PATH)
        except Exception:
            pass                                   # gagal simpan -> tetap benar sesi ini
    try:
        from .systemize import _load_matmap
        reg |= set(_load_matmap().keys())
    except Exception:
        pass
    return reg


# ---------------------------------------------------------------- parameter (pt) --
GAP_H_PT = 26.0      # jarak maks antar kotak teks utk pasangan MENDATAR
GAP_V_PT = 14.0      # jarak maks antar kotak teks utk pasangan BERTUMPUK
SNAP_PT = 22.0       # radius maks snap titik break ke run pipa (terkalibrasi: snap benar
                     # terukur 34-67 px @350dpi, snap salah mulai >120 px)
OVERLAP_MIN = 0.45   # overlap sisi tegak-lurus minimum (fraksi sisi terpendek)


def _iou_1d(a0, a1, b0, b1):
    """Fraksi tumpang tindih 1-D terhadap interval TERPENDEK."""
    inter = min(a1, b1) - max(a0, b0)
    short = min(a1 - a0, b1 - b0)
    return inter / short if short > 0 else 0.0


def _dedup_tokens(tokens, tol=14.0):
    """Token yang sama muncul berkali-kali (tile overlap x 3 sudut) -> satukan.
    Ambil median bbox tiap klaster supaya koordinat stabil."""
    out = []
    for tk in sorted(tokens, key=lambda t: (t["t"], t["x1"], t["y1"])):
        cx, cy = (tk["x1"] + tk["x2"]) / 2, (tk["y1"] + tk["y2"]) / 2
        hit = None
        for o in out:
            if o["t"] == tk["t"] and abs(o["cx"] - cx) < tol and abs(o["cy"] - cy) < tol:
                hit = o
                break
        if hit is None:
            out.append({**tk, "cx": cx, "cy": cy, "n": 1})
        else:
            n = hit["n"]
            for k in ("x1", "y1", "x2", "y2"):
                hit[k] = (hit[k] * n + tk[k]) / (n + 1)
            hit["cx"] = (hit["x1"] + hit["x2"]) / 2
            hit["cy"] = (hit["y1"] + hit["y2"]) / 2
            hit["n"] = n + 1
    return out


def _has_divider(img_bgr, x0, y0, x1, y1, axis):
    """Ada garis pembatas (division line) di celah antar dua teks?
    axis='v' -> cari goresan TEGAK di dalam kotak celah; 'h' -> goresan MENDATAR."""
    if img_bgr is None:
        return False
    H, W = img_bgr.shape[:2]
    xa, xb = int(max(0, min(x0, x1))), int(min(W, max(x0, x1)))
    ya, yb = int(max(0, min(y0, y1))), int(min(H, max(y0, y1)))
    if xb - xa < 2 or yb - ya < 2:
        return False
    sub = img_bgr[ya:yb, xa:xb]
    gray = sub if sub.ndim == 2 else sub.mean(axis=2)
    ink = gray < 128
    if not ink.any():
        return False
    # proyeksi tegak lurus arah goresan: pembatas = kolom/baris yang hampir penuh tinta
    prof = ink.mean(axis=0) if axis == "v" else ink.mean(axis=1)
    return bool(prof.max() >= 0.55)


def _snap_to_run(runs, bx, by, prefer_axis, snap_px):
    """Run pipa terdekat dari titik break. Return (run_idx, x, y, jarak) atau None.
    Run SEJAJAR arah pasangan teks diprioritaskan (bobot jarak 0.6x)."""
    best = None
    for i, r in enumerate(runs):
        if r.get("underline"):
            continue                                   # garis penunjuk label, bukan pipa
        pts = r.get("points") or [[r["x1"], r["y1"]], [r["x2"], r["y2"]]]
        for (ax, ay), (cx2, cy2) in zip(pts, pts[1:]):
            vx, vy = cx2 - ax, cy2 - ay
            L2 = vx * vx + vy * vy
            if L2 <= 0:
                continue
            t = max(0.0, min(1.0, ((bx - ax) * vx + (by - ay) * vy) / L2))
            px, py = ax + t * vx, ay + t * vy
            d = ((px - bx) ** 2 + (py - by) ** 2) ** 0.5
            seg_axis = "h" if abs(vx) >= abs(vy) else "v"
            score = d * (0.6 if seg_axis == prefer_axis else 1.0)
            if d <= snap_px and (best is None or score < best[0]):
                best = (score, i, px, py, d, seg_axis)
    if best is None:
        return None
    _, i, px, py, d, seg_axis = best
    return {"run_idx": i, "x": float(px), "y": float(py), "dist": float(d),
            "run_axis": seg_axis}


def find_connection_points(tokens, runs, img_bgr=None, dpi=350, vocab=()):
    """Cari connection point dari token OCR pendek + geometri.

    tokens : list dict {t,x1,y1,x2,y2,ang} — dari detect_piping_ids(tokens_out=...)
    runs   : list dict run pipa (hasil tracing)
    vocab  : himpunan kode piping class yang dikenal (dari line number drawing ini +
             material_map.csv). Kode di luar vocab tetap boleh, tapi conf lebih rendah.

    Return list dict:
      {codes:[A,B], orient:'h'|'v', x,y (titik break), run_idx, dist,
       side_a,side_b ('left'/'right'/'top'/'bottom' atau '' bila tak sejajar),
       divider:bool, in_vocab:bool, conf:0..1}
    """
    S = dpi / 72.0
    toks = [t for t in _dedup_tokens(tokens) if t["n"] >= 1]
    vocab = {v.upper() for v in vocab}
    gap_h, gap_v, snap = GAP_H_PT * S, GAP_V_PT * S, SNAP_PT * S

    cands = []
    for i in range(len(toks)):
        for j in range(len(toks)):
            if i == j:
                continue
            a, b = toks[i], toks[j]
            if a["t"] == b["t"]:
                continue                               # spec break = class BERBEDA
            ha, hb = a["y2"] - a["y1"], b["y2"] - b["y1"]
            if min(ha, hb) <= 0 or not (0.6 <= ha / hb <= 1.7):
                continue                               # ukuran teks harus sepadan
            # --- pasangan MENDATAR: a kiri, b kanan, sebaris ---
            if (a["x2"] <= b["x1"] and 0 < b["x1"] - a["x2"] <= gap_h
                    and _iou_1d(a["y1"], a["y2"], b["y1"], b["y2"]) >= OVERLAP_MIN):
                cands.append(("h", a, b, (a["x2"] + b["x1"]) / 2, (a["cy"] + b["cy"]) / 2,
                              a["x2"], min(a["y1"], b["y1"]), b["x1"], max(a["y2"], b["y2"])))
            # --- pasangan BERTUMPUK: a atas, b bawah, sekolom ---
            if (a["y2"] <= b["y1"] and 0 < b["y1"] - a["y2"] <= gap_v
                    and _iou_1d(a["x1"], a["x2"], b["x1"], b["x2"]) >= OVERLAP_MIN):
                cands.append(("v", a, b, (a["cx"] + b["cx"]) / 2, (a["y2"] + b["y1"]) / 2,
                              min(a["x1"], b["x1"]), a["y2"], max(a["x2"], b["x2"]), b["y1"]))

    out = []
    for orient, a, b, bx, by, gx0, gy0, gx1, gy1 in cands:
        # SYARAT MUTLAK: kedua kode harus kode piping class yang dikenal. Tanpa ini,
        # pasangan teks biasa ('NOTE|DCS', 'TA|HH') ikut tertangkap — terukur presisi
        # anjlok ke ~10%. Vocab dibangun otomatis dari line number drawing + registry
        # class se-project + material_map.csv, jadi tetap general untuk perusahaan mana pun.
        in_vocab = a["t"] in vocab and b["t"] in vocab
        if not in_vocab:
            continue
        # pembatas: pasangan mendatar dihimpit garis TEGAK, dan sebaliknya
        div = _has_divider(img_bgr, gx0, gy0, gx1, gy1, "v" if orient == "h" else "h")
        snapped = _snap_to_run(runs, bx, by, orient, snap)
        conf = 0.55 + (0.25 if div else 0.0) + (0.20 if snapped else 0.0)
        rec = {"codes": [a["t"], b["t"]], "orient": orient,
               "x": float(bx), "y": float(by), "divider": bool(div),
               "in_vocab": bool(in_vocab), "conf": round(min(conf, 1.0), 2),
               "run_idx": -1, "dist": None, "side_a": "", "side_b": ""}
        if snapped:
            rec.update(run_idx=snapped["run_idx"], x=snapped["x"], y=snapped["y"],
                       dist=round(snapped["dist"], 1))
            # sisi hanya ditetapkan bila arah pasangan SEJAJAR arah pipa — kalau tidak,
            # connection point tetap dipakai sbg BARRIER tanpa klaim sisi (jujur & aman).
            if snapped["run_axis"] == orient:
                rec["side_a"], rec["side_b"] = (("left", "right") if orient == "h"
                                                else ("top", "bottom"))
        out.append(rec)

    # buang duplikat (pasangan sama terdeteksi 2 arah / berdekatan)
    out.sort(key=lambda r: -r["conf"])
    keep = []
    for r in out:
        if not any(abs(k["x"] - r["x"]) < 30 * S and abs(k["y"] - r["y"]) < 30 * S
                   for k in keep):
            keep.append(r)
    return keep


def barriers_by_run(cps):
    """run_idx -> [(x,y), ...] titik break, utk dipakai propagate.py sebagai penghalang."""
    out = {}
    for c in cps:
        if c.get("run_idx", -1) >= 0:
            out.setdefault(c["run_idx"], []).append((c["x"], c["y"]))
    return out
