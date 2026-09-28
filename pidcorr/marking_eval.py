"""
Validasi MARKING vs ground truth CCD marked — COLOR-AGNOSTIC (bukan bandingkan warna).

Ide penulis: spec break tak bisa jadi patokan (CP CCC|CCB dgn fluid sama tidak ganti warna
di level system). Yang benar: bandingkan STRUKTUR marking terhadap CCD yang sudah dimarking
engineer — (A) apakah tracing line mendarat di PIPA yang sama, dan (B) apakah titik PERUBAHAN
warna (batas circuit) di LOKASI yang sama. Identitas warna diabaikan; hanya 'ditandai/tidak'
dan 'berubah/tidak' yang dibandingkan.

Metrik:
  A. PLACEMENT (level run/pipa): untuk tiap pipa — engineer menandainya? sistem menandainya?
     -> precision/recall/F1 penempatan marking. Menjawab "presisi tracing di pipa yg benar".
  B. BOUNDARY (level sambungan): untuk tiap pasang pipa BERSEBELAHAN yang dua-duanya
     ditandai kedua pihak — engineer ganti warna di sambungan itu? sistem ganti circuit?
     -> agreement + precision/recall batas. Menjawab "presisi lokasi perubahan warna".

Ground truth diekstrak dari goresan berwarna PDF CCD marked (vektor). Marked & unmarked =
drawing dasar sama -> koordinat sejajar. Raster CCD dilewati (ekstraksi warna beda).
"""
import os
import glob
from collections import Counter

import numpy as np
import cv2
import fitz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CCD_DIRS = ["605 CCD2", "610 CCD2", "650 CCD2", "660 CCD2", "695-IS"]


def find_original(stem):
    from .unmark import page_is_raster  # noqa
    for d in CCD_DIRS:
        for p in glob.glob(os.path.join(ROOT, d, "*.pdf")):
            if os.path.splitext(os.path.basename(p))[0] == stem:
                return p
    return None


def _is_closed_path(d):
    """Jalur TERTUTUP (titik awal ~ titik akhir, >=3 segmen) = kotak metadata CCD atau
    OUTLINE EQUIPMENT yang ikut diwarnai engineer — BUKAN pipa. Marking pipa selalu
    polyline terbuka."""
    pts = []
    for it in d.get("items", []):
        if it[0] == "l":
            pts += [it[1], it[2]]
        elif it[0] == "c":
            pts += [it[1], it[4]]
        elif it[0] == "re":
            return True
    if len(pts) < 6:
        return False
    a, b = pts[0], pts[-1]
    return abs(a.x - b.x) < 2.0 and abs(a.y - b.y) < 2.0


def gt_color_map(pdf_path, out_w, out_h, pipes_only=False, rot=0):
    """Rasterisasi goresan marking berwarna PDF asli -> idx_map [h,w] (0=tak ditandai,
    >0 = indeks kelompok warna). Return (idx_map, colors) atau (None,None) bila raster.

    pipes_only=True membuang jalur TERTUTUP (kotak metadata CCD & outline equipment yang
    juga diwarnai engineer) sehingga yang tersisa hanya goresan PIPA. Wajib dipakai saat
    mengukur kualitas line tracing — tanpa ini recall tampak rendah palsu."""
    from .unmark import page_is_raster
    doc = fitz.open(pdf_path)
    pg = doc[0]
    if page_is_raster(pg):
        return None, None
    R = pg.rotation_matrix
    disp = pg.rect
    # Sebagian P&ID digambar LANDSCAPE di dalam mediabox PORTRAIT tanpa /Rotate, sehingga
    # citra yang dipakai sistem diputar lebih dulu (rot). GT digambar di ruang halaman, jadi
    # peta dibangun pada ukuran SEBELUM diputar lalu ikut diputar di akhir.
    rot = int(rot) % 360
    uw, uh = (out_h, out_w) if rot in (90, 270) else (out_w, out_h)
    # sisa ketidakcocokan orientasi = arah putar tak dapat dipastikan dari PDF saja ->
    # drawing dikeluarkan (keterbatasan alignment GT, BUKAN kegagalan tracing)
    if (disp.width > disp.height) != (uw > uh):
        return None, None
    sx, sy = uw / disp.width, uh / disp.height
    colors = []
    idx = np.zeros((uh, uw), np.int32)
    for d in pg.get_drawings():
        col = d.get("color")
        if col is None:
            continue
        r, g, b = [int(255 * c) for c in col]
        if max(r, g, b) - min(r, g, b) < 30:            # hitam/abu = gambar dasar
            continue
        if pipes_only and _is_closed_path(d):
            continue
        key = None
        for k, (cr, cg, cb) in enumerate(colors):
            if abs(cr - r) + abs(cg - g) + abs(cb - b) < 60:
                key = k + 1; break
        if key is None:
            colors.append((r, g, b)); key = len(colors)
        for item in d["items"]:
            if item[0] == "l":
                pts = [item[1], item[2]]
            elif item[0] == "c":
                pts = [item[1], item[4]]
            else:
                continue
            q = [fitz.Point(p) * R for p in pts]
            p1 = (int(q[0].x * sx), int(q[0].y * sy))
            p2 = (int(q[1].x * sx), int(q[1].y * sy))
            cv2.line(idx, p1, p2, int(key), 7)
    if rot:
        from .pipeline import rotate_bgr
        idx = rotate_bgr(idx, rot)          # cv2.rotate bekerja pada int32 juga
    return idx, colors


def _run_pts(r):
    return r.get("points") or [[r["x1"], r["y1"]], [r["x2"], r["y2"]]]


def line_gt(idx_map, r, band=3):
    """(indeks warna dominan, jumlah vote) di sepanjang run. 0 = tak ditandai. band = radius
    piksel sampling (marking bisa sedikit meleset dari garis pipa)."""
    H, W = idx_map.shape
    votes = Counter()
    pts = _run_pts(r)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(2, int(np.hypot(x1 - x0, y1 - y0) / 8))
        for t in np.linspace(0, 1, n):
            x, y = int(x0 + t * (x1 - x0)), int(y0 + t * (y1 - y0))
            sub = idx_map[max(0, y-band):y+band+1, max(0, x-band):x+band+1]
            nz = sub[sub > 0]
            if nz.size:
                votes[int(np.bincount(nz).argmax())] += 1
    if not votes:
        return 0, 0
    k, v = votes.most_common(1)[0]
    return k, v


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p * 100, r * 100, f * 100


def evaluate(result, pdf_path, min_votes=2):
    """Metrik A (placement) + B (boundary) marking sistem vs CCD marked. Return dict / None
    bila drawing raster (GT tak bisa diekstrak)."""
    from .systemize import circuitize
    from .propagate import build_adjacency

    runs = result.get("runs", [])
    W, H = result.get("w"), result.get("h")
    if not runs or not W:
        return None
    idx_map, colors = gt_color_map(pdf_path, W, H, rot=result.get("rot", 0))
    if idx_map is None:
        return {"skip": "raster"}

    # sistem: run_idx -> kode circuit (marking sistem)
    systems = circuitize(result)
    sys_circ = {}
    for s in systems:
        for c in s["circuits"]:
            for ri in c["run_idxs"]:
                sys_circ[ri] = c["code"]

    pipe = [i for i, r in enumerate(runs) if not r.get("underline")]
    gt_col = {}
    for i in pipe:
        k, v = line_gt(idx_map, runs[i])
        gt_col[i] = k if v >= min_votes else 0          # 0 = engineer tak menandai

    # ---- A. PLACEMENT (per pipa) ----
    tp = fp = fn = 0
    for i in pipe:
        g = gt_col[i] > 0
        s = i in sys_circ
        if g and s:
            tp += 1
        elif s and not g:
            fp += 1
        elif g and not s:
            fn += 1
    aP, aR, aF = _prf(tp, fp, fn)

    # ---- B. BOUNDARY (per sambungan pipa bersebelahan) ----
    adj = build_adjacency(runs, symbols=result.get("symbols") or [])
    seen = set()
    bt = bf_p = bf_n = tn = 0
    for i in adj:
        for j in adj[i]:
            e = (min(i, j), max(i, j))
            if e in seen:
                continue
            seen.add(e)
            # hanya sambungan yang KEDUA sisinya ditandai kedua pihak -> pertanyaan batas valid
            if not (gt_col.get(i, 0) and gt_col.get(j, 0) and i in sys_circ and j in sys_circ):
                continue
            gt_change = gt_col[i] != gt_col[j]
            sys_change = sys_circ[i] != sys_circ[j]
            if gt_change and sys_change:
                bt += 1
            elif sys_change and not gt_change:
                bf_p += 1
            elif gt_change and not sys_change:
                bf_n += 1
            else:
                tn += 1
    bP, bR, bF = _prf(bt, bf_p, bf_n)
    n_edge = bt + bf_p + bf_n + tn
    b_agree = 100.0 * (bt + tn) / n_edge if n_edge else None

    return {
        "n_colors_gt": len(colors),
        "placement": {"tp": tp, "fp": fp, "fn": fn, "precision": aP, "recall": aR, "f1": aF,
                      "n_gt_marked": tp + fn, "n_sys_marked": tp + fp},
        "boundary": {"tp": bt, "fp": bf_p, "fn": bf_n, "tn": tn, "n_edges": n_edge,
                     "precision": bP, "recall": bR, "f1": bF, "agreement": b_agree},
    }


def evaluate_by_stem(result, stem):
    """Bungkus evaluate() dgn mencari PDF CCD asli dari stem. None bila PDF tak ada."""
    pdf = find_original(stem)
    if not pdf:
        return None
    return evaluate(result, pdf)
