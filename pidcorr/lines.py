"""
Line tracing (Fitur 1a) + asosiasi piping ID -> pipa. Pendekatan GRAPH-based
(RASTER-first, satu frame koordinat dgn piping ID):

  1. extract_segments : ekstrak segmen garis lurus H & V via morfologi (open kernel
     panjang -> hanya garis panjang lolos; teks/simbol/dash terbuang; close -> sambung gap).
  2. suppress_box_edges: buang segmen yang berimpit tepi bbox simbol/equipment (outline
     kotak, BUKAN pipa) memakai hasil deteksi YOLO.
  3. merge_elbows     : sambung segmen di simpul derajat-2 (siku/kolinear) jadi RUN
     polyline utuh; simpul cabang (T/cross, derajat>=3) & ujung bebas jadi batas run.
     -> pipa bengkok = 1 run (klik ID highlight pipa penuh; fondasi marking Fitur 2).
  4. associate        : tiap piping ID -> MAIN run terdekat yg SEJAJAR (attached) atau via
     garis penunjuk/leader (kontinuitas tinta). Polyline-aware (cek per-segmen).

Model run = POLYLINE: `points=[(x,y),...]` (>=2 vertex). Segmen lurus = 2 titik.
`axis` in {h, v, d, poly}. GUI merender polyline + dot bisa digeser tiap vertex.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict
import numpy as np
import cv2


@dataclass
class PipeRun:
    points: list                              # [(x,y),...] >=2 vertex (px citra)
    axis: str = "poly"                        # 'h','v','d','poly'
    pid: str = ""
    fluid: str = ""
    underline: bool = False                   # True = garis-penunjuk ber-label (BUKAN pipa)

    # kompat lama: x1,y1,x2,y2 = ujung-ujung polyline
    @property
    def x1(self): return int(self.points[0][0])
    @property
    def y1(self): return int(self.points[0][1])
    @property
    def x2(self): return int(self.points[-1][0])
    @property
    def y2(self): return int(self.points[-1][1])

    @property
    def length(self):
        return sum(((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5
                   for a, b in zip(self.points, self.points[1:]))

    def segments(self):
        return list(zip(self.points, self.points[1:]))

    def __getitem__(self, key):
        if key == "points": return self.points
        if key == "axis": return self.axis
        if key == "pid": return self.pid
        if key == "fluid": return self.fluid
        if key == "underline": return self.underline
        if key == "x1": return min(p[0] for p in self.points)
        if key == "y1": return min(p[1] for p in self.points)
        if key == "x2": return max(p[0] for p in self.points)
        if key == "y2": return max(p[1] for p in self.points)
        if key == "length": return self.length
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


# --------------------------------------------------- 1) ekstraksi segmen ----------
def extract_segments(img_bgr, dpi=350, min_len_pt=26, gap_pt=10, border_pt=24):
    """Segmen lurus H & V (2-titik) dari citra P&ID kosongan."""
    S = dpi / 72.0
    H, W = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    ink = (gray < 128).astype(np.uint8)
    minlen = max(8, int(min_len_pt * S))
    gap = max(2, int(gap_pt * S))
    border = int(border_pt * S)

    segs = []
    for axis, ksize, gksize in (("h", (minlen, 1), (gap, 1)), ("v", (1, minlen), (1, gap))):
        m = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, ksize))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, gksize))
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if axis == "h":
                if w < minlen or w > 0.92 * W:
                    continue
                yc = y + h // 2
                p, q = (x, yc), (x + w, yc)
            else:
                if h < minlen or h > 0.92 * H:
                    continue
                xc = x + w // 2
                p, q = (xc, y), (xc, y + h)
            cx, cy = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            if cx < border or cx > W - border or cy < border or cy > H - border:
                continue
            segs.append(PipeRun([p, q], axis))
    return segs


# --------------------------------------------------- 2) buang tepi kotak ----------
def suppress_box_edges(segs, detections, dpi=350, margin_pt=7):
    """Buang segmen yg berimpit TEPI bbox simbol/equipment (outline kotak, bukan pipa).
    Hanya perimeter (bukan interior) -> pipa yg lewat DALAM box besar tetap aman."""
    if not detections:
        return segs
    m = margin_pt * dpi / 72.0
    boxes = [(d["x1"], d["y1"], d["x2"], d["y2"]) for d in detections]
    out = []
    for s in segs:
        (x0, y0), (x1, y1) = s.points[0], s.points[-1]
        drop = False
        for bx0, by0, bx1, by1 in boxes:
            if s.axis == "h":
                lo, hi, yy = min(x0, x1), max(x0, x1), y0
                near_edge = abs(yy - by0) <= m or abs(yy - by1) <= m
                inside_span = lo >= bx0 - m and hi <= bx1 + m
            else:
                lo, hi, xx = min(y0, y1), max(y0, y1), x0
                near_edge = abs(xx - bx0) <= m or abs(xx - bx1) <= m
                inside_span = lo >= by0 - m and hi <= by1 + m
            if near_edge and inside_span:
                drop = True
                break
        if not drop:
            out.append(s)
    return out


# --------------------------------------------------- 3) merge elbow (graph) -------
class _UF:
    def __init__(self, n): self.p = list(range(n))
    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]; a = self.p[a]
        return a
    def union(self, a, b): self.p[self.find(a)] = self.find(b)


def _axis_of(coords, tol):
    if len(coords) == 2:
        dx = abs(coords[0][0] - coords[1][0]); dy = abs(coords[0][1] - coords[1][1])
        if dy <= tol and dx > tol: return "h"
        if dx <= tol and dy > tol: return "v"
        return "d"
    return "poly"


def _dedup_collinear(coords, tol):
    """Buang vertex tengah yg kolinear (jarak perp kecil) -> polyline minimal."""
    if len(coords) <= 2:
        return coords
    out = [coords[0]]
    for i in range(1, len(coords) - 1):
        ax, ay = out[-1]; bx, by = coords[i]; cx, cy = coords[i + 1]
        # jarak titik b ke garis a-c
        d = abs((cx - ax) * (ay - by) - (ax - bx) * (cy - ay))
        norm = ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5 + 1e-6
        if d / norm > tol:
            out.append(coords[i])
    out.append(coords[-1])
    return out


def merge_elbows(segs, dpi=350, join_pt=2.5):
    """Sambung segmen di simpul derajat-2 (siku/kolinear) jadi run polyline.
    Simpul cabang (derajat>=3) & ujung bebas -> batas run (tak di-merge).
    CATATAN: join_pt SENGAJA kecil (~beberapa px) -> hanya endpoint yg benar2 berimpit
    (elbow asli) yg menyatu; tol besar bikin union transitif 'sprawl' di area padat
    (tabel) -> centroid node melebar -> polyline diagonal palsu."""
    if not segs:
        return []
    tol = max(3, int(join_pt * dpi / 72.0))
    # kumpulkan endpoint tiap segmen
    pts = []
    for i, s in enumerate(segs):
        pts.append((i, 0, s.points[0][0], s.points[0][1]))
        pts.append((i, 1, s.points[-1][0], s.points[-1][1]))
    uf = _UF(len(pts))
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            if abs(pts[a][2] - pts[b][2]) <= tol and abs(pts[a][3] - pts[b][3]) <= tol:
                uf.union(a, b)
    members = defaultdict(list)
    node_of = {}
    for k, (ri, ei, x, y) in enumerate(pts):
        r = uf.find(k); members[r].append((x, y)); node_of[(ri, ei)] = r
    node_coord = {r: (float(np.mean([p[0] for p in mm])), float(np.mean([p[1] for p in mm])))
                  for r, mm in members.items()}
    adj = defaultdict(list); seg_nodes = {}
    for i in range(len(segs)):
        na, nb = node_of[(i, 0)], node_of[(i, 1)]
        seg_nodes[i] = (na, nb)
        if na != nb:
            adj[na].append((i, nb)); adj[nb].append((i, na))

    def walk(node, from_seg, used):
        seq = []; cur = node; prev = from_seg
        while len(adj[cur]) == 2:
            others = [(s, nn) for (s, nn) in adj[cur] if s != prev]
            if not others or others[0][0] in used:
                break
            s, nn = others[0]
            used.add(s); seq.append(nn); prev = s; cur = nn
        return seq

    collin_tol = 3.0                                   # KECIL: buang vertex yg BENAR2 kolinear
    used = set(); out = []                             # (bukan tol join -> tangga pipa tak kolaps jadi diagonal)
    for i in range(len(segs)):
        if i in used:
            continue
        used.add(i)
        na, nb = seg_nodes[i]
        if na == nb:                                   # segmen degenerate
            out.append(segs[i]); continue
        left = walk(na, i, used); right = walk(nb, i, used)
        node_seq = list(reversed(left)) + [na, nb] + right
        coords = _dedup_collinear([node_coord[n] for n in node_seq], collin_tol)
        out.append(PipeRun([(int(x), int(y)) for x, y in coords], _axis_of(coords, tol)))
    return out


def extract_diagonal_segments(img_bgr, dpi, connect_pts, detections=None,
                              min_len_pt=42, connect_pt=13, ang_lo=20, ang_hi=70):
    """Segmen DIAGONAL (pipa miring) via Hough pada residu (ink - garis H/V - simbol).
    CONNECT-CONSTRAINED: hanya simpan diagonal yg KEDUA ujungnya dekat titik jaringan
    pipa H/V (connect_pts) -> buang hatching/garis cone-stack/teks yg tak nyambung."""
    S = dpi / 72.0
    H, W = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    ink = (gray < 128).astype(np.uint8)
    minlen = int(min_len_pt * S)
    # buang struktur H & V panjang (biar Hough fokus ke diagonal)
    hv = cv2.dilate(
        cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (minlen, 1)))
        | cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, minlen))),
        np.ones((3, 3), np.uint8))
    resid = cv2.bitwise_and(ink, cv2.bitwise_not(hv))
    for d in (detections or []):                      # buang area simbol
        cv2.rectangle(resid, (int(d["x1"]), int(d["y1"])), (int(d["x2"]), int(d["y2"])), 0, -1)
    lines = cv2.HoughLinesP(resid * 255, 1, np.pi / 180, threshold=int(40 * S / 4.86),
                            minLineLength=minlen, maxLineGap=int(6 * S))
    if lines is None:
        return []
    cpts = np.array(connect_pts, float) if connect_pts else np.empty((0, 2))
    ctol = connect_pt * S

    def connected(x, y):
        if len(cpts) == 0:
            return False
        return bool((np.abs(cpts[:, 0] - x) <= ctol).__and__(np.abs(cpts[:, 1] - y) <= ctol).any())

    out = []
    for x1, y1, x2, y2 in lines[:, 0]:
        ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        ang = min(ang, 180 - ang)
        if not (ang_lo <= ang <= ang_hi):             # bukan diagonal jelas -> lewati
            continue
        if connected(x1, y1) and connected(x2, y2):   # kedua ujung nyambung jaringan
            out.append(PipeRun([(int(x1), int(y1)), (int(x2), int(y2))], "d"))
    return out


def _subtract_intervals(lo, hi, ins, min_keep):
    """Kurangi interval 'ins' (bagian di dalam equipment) dari [lo,hi]; kembalikan potongan
    LUAR yg panjangnya >= min_keep."""
    if not ins:
        return [(lo, hi)]
    ins = sorted(ins)
    merged = [list(ins[0])]
    for a, b in ins[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, cur = [], lo
    for a, b in merged:
        if a - cur >= min_keep:
            out.append((cur, a))
        cur = max(cur, b)
    if hi - cur >= min_keep:
        out.append((cur, hi))
    return out


# Sebuah SIMBOL equipment tidak masuk akal menempati sepersepuluh lembar P&ID; kotak
# sebesar itu adalah batas package/skid atau salah deteksi. Menekan interiornya akan
# menghapus pipa asli di dalamnya — pada 013-01 satu kotak 16,2% halaman menelan 32% dari
# seluruh pipa bertanda engineer. Sebaran 123 kotak equipment di dataset: median 1,1%,
# persentil-95 6,1%, persentil-99 8,9%, dan hanya kotak itu yang melampaui ambang ini.
MAX_EQUIP_AREA_FRAC = 0.10


def suppress_equipment_interior(segs, detections, dpi=350, margin_pt=3, page_wh=None):
    """Equipment TIDAK di-trace jadi pipa SAMA SEKALI (permintaan penulis): tiap segmen H/V
    di-CLIP di batas bbox equipment — bagian DI DALAM equipment dibuang, bagian di LUAR
    (pipa asli menuju nozzle) dipertahankan. Beda dari versi lama yg cuma buang segmen yg
    KEDUA ujungnya di dalam (pipa masuk equipment jadi masih ke-trace ke dalam).
    Efek: pipa berhenti di tepi equipment. Bila sebuah piping ID kehilangan pipanya karena
    ini -> otomatis jadi 'pid_none' di panel Review (user yang memutuskan)."""
    eqs = [d for d in (detections or []) if d.get("coarse") == "equipment"]
    if page_wh:
        page = float(page_wh[0]) * float(page_wh[1])
        big = [d for d in eqs
               if (d["x2"] - d["x1"]) * (d["y2"] - d["y1"]) > MAX_EQUIP_AREA_FRAC * page]
        if big:
            # kotaknya TETAP dilaporkan sebagai deteksi (engineer bisa mengoreksinya di GUI);
            # yang dilewati hanya penekanan interiornya
            eqs = [d for d in eqs if d not in big]
    if not eqs:
        return segs
    m = margin_pt * dpi / 72.0
    boxes = [(d["x1"] + m, d["y1"] + m, d["x2"] - m, d["y2"] - m) for d in eqs]
    boxes = [b for b in boxes if b[0] < b[2] and b[1] < b[3]]
    keep = 12 * dpi / 72.0
    out = []
    for s in segs:
        (x0, y0), (x1, y1) = s.points[0], s.points[-1]
        if abs(x1 - x0) >= abs(y1 - y0):                # horizontal
            y = (y0 + y1) / 2.0; lo, hi = sorted((x0, x1))
            ins = [(max(bx0, lo), min(bx1, hi)) for bx0, by0, bx1, by1 in boxes
                   if by0 <= y <= by1 and max(bx0, lo) < min(bx1, hi)]
            if not ins:
                out.append(s); continue
            for a, b in _subtract_intervals(lo, hi, ins, keep):
                out.append(PipeRun([(a, y), (b, y)], s.axis))
        else:                                           # vertical
            x = (x0 + x1) / 2.0; lo, hi = sorted((y0, y1))
            ins = [(max(by0, lo), min(by1, hi)) for bx0, by0, bx1, by1 in boxes
                   if bx0 <= x <= bx1 and max(by0, lo) < min(by1, hi)]
            if not ins:
                out.append(s); continue
            for a, b in _subtract_intervals(lo, hi, ins, keep):
                out.append(PipeRun([(x, a), (x, b)], s.axis))
    return out


def suppress_furniture(segs, furniture, dpi=350, margin_pt=6):
    """Buang segmen yg berada DI DALAM region furniture (title block/tabel/notes) hasil
    layout-detector -> tabel tak ke-trace jadi pipa. Pakai titik tengah segmen: garis grid
    tabel (pendek, seluruhnya di dalam) kena; pipa yg cuma menyerempet tepi box tetap aman."""
    if not furniture:
        return segs
    m = margin_pt * dpi / 72.0
    out = []
    for s in segs:
        cx = (s.points[0][0] + s.points[-1][0]) / 2
        cy = (s.points[0][1] + s.points[-1][1]) / 2
        inside = any(x0 - m <= cx <= x1 + m and y0 - m <= cy <= y1 + m
                     for x0, y0, x1, y1 in furniture)
        if not inside:
            out.append(s)
    return out


def detect_boxes(img_bgr, dpi=350, min_side_pt=7, max_area_frac=0.35):
    """Deteksi KOTAK TERTUTUP ber-outline (equipment kotak yg tak terdeteksi YOLO, detail
    inset 'DETAIL A/B', note box 'NOTE 3 LUBE OIL UNIT'). Outline kotak BUKAN pipa -> perimeter-
    nya harus di-suppress dari tracing. Kriteria: kontur ~4 sudut, konveks, persegi (isi≈bbox),
    ukuran sisi >= min_side_pt dan LUAS < max_area_frac halaman (buang bingkai gambar).
    Return list (x0,y0,x1,y1) piksel."""
    S = dpi / 72.0
    H, W = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    ink = (gray < 128).astype(np.uint8)
    k = max(2, int(2 * S))
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    cnts, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    ms = min_side_pt * S
    page = float(W * H)
    boxes = []
    for c in cnts:
        peri = cv2.arcLength(c, True)
        if peri < 4 * ms:
            continue
        ap = cv2.approxPolyDP(c, 0.03 * peri, True)
        if not (4 <= len(ap) <= 6):                   # 4 sudut; toleransi 5-6 utk stub pipa menempel
            continue
        x, y, w, h = cv2.boundingRect(ap)
        if w < ms or h < ms:
            continue
        if w * h > max_area_frac * page:              # bingkai gambar (raksasa) -> lewati
            continue
        if cv2.contourArea(c) < 0.72 * w * h:         # isi kontur ≈ bbox -> benar-benar persegi
            continue
        boxes.append((x, y, x + w, y + h))
    return boxes


def suppress_box_outlines(segs, boxes, dpi=350, band_pt=7):
    """Buang segmen yang BERIMPIT tepi kotak (dari detect_boxes) — garis outline kotak,
    BUKAN pipa. Ketat: segmen sejajar tepi (H utk tepi atas/bawah, V utk kiri/kanan),
    berada dalam pita tipis di tepi itu, DAN span-nya TERKANDUNG dalam sisi kotak. Pipa yg
    MENEMBUS kotak (tegak lurus tepi, atau menjulur keluar span) tetap aman."""
    if not boxes:
        return segs
    b = band_pt * dpi / 72.0
    out = []
    for s in segs:
        (x0, y0), (x1, y1) = s.points[0], s.points[-1]
        horiz = abs(y1 - y0) <= b
        vert = abs(x1 - x0) <= b
        on_edge = False
        for bx0, by0, bx1, by1 in boxes:
            if horiz:
                ym = (y0 + y1) / 2
                if ((abs(ym - by0) <= b or abs(ym - by1) <= b)
                        and min(x0, x1) >= bx0 - b and max(x0, x1) <= bx1 + b):
                    on_edge = True; break
            if vert:
                xm = (x0 + x1) / 2
                if ((abs(xm - bx0) <= b or abs(xm - bx1) <= b)
                        and min(y0, y1) >= by0 - b and max(y0, y1) <= by1 + b):
                    on_edge = True; break
        if not on_edge:
            out.append(s)
    return out


def extract_pipe_runs(img_bgr, dpi=350, detections=None, furniture=None, diagonal=False,
                      boxes=None, **kw):
    """Pipeline lengkap: segmen -> buang tepi-box simbol -> buang interior equipment ->
    buang furniture -> buang OUTLINE kotak (equipment kotak/detail/note) -> merge elbow.
    `boxes` = hasil detect_boxes (bila None, dihitung di sini)."""
    segs = extract_segments(img_bgr, dpi=dpi, **kw)
    segs = suppress_box_edges(segs, detections, dpi=dpi)
    segs = suppress_equipment_interior(segs, detections, dpi=dpi,
                                       page_wh=(img_bgr.shape[1], img_bgr.shape[0]))
    segs = suppress_furniture(segs, furniture, dpi=dpi)
    if boxes is None:
        boxes = detect_boxes(img_bgr, dpi=dpi)
    segs = suppress_box_outlines(segs, boxes, dpi=dpi)
    if diagonal:
        nodes = [pt for s in segs for pt in (s.points[0], s.points[-1])]
        segs += extract_diagonal_segments(img_bgr, dpi, nodes, detections)
    return merge_elbows(segs, dpi=dpi)


# --------------------------------------------------- 4) asosiasi -------------------
def _foot_on_segment(ax, ay, bx, by, x, y):
    """Titik terdekat pada segmen a-b dari (x,y) (proyeksi ter-clamp)."""
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return ax, ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
    return ax + t * dx, ay + t * dy


def _foot_on_run(r: PipeRun, x, y):
    best, bestd = (r.points[0][0], r.points[0][1]), 1e18
    for (ax, ay), (bx, by) in r.segments():
        fx, fy = _foot_on_segment(ax, ay, bx, by, x, y)
        d = (fx - x) ** 2 + (fy - y) ** 2
        if d < bestd:
            bestd, best = d, (fx, fy)
    return best


def _ink_frac(ink, x0, y0, x1, y1, n=44):
    H, W = ink.shape
    xs = np.linspace(x0, x1, n).astype(int); ys = np.linspace(y0, y1, n).astype(int)
    m = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
    if m.sum() == 0:
        return 0.0
    b = 2; hit = 0
    for x, y in zip(xs[m], ys[m]):
        if ink[max(0, y - b):y + b + 1, max(0, x - b):x + b + 1].any():
            hit += 1
    return hit / int(m.sum())


def _underline_idxs(pids, runs, dpi=350):
    """Deteksi run 'LABEL UNDERLINE' = garis penunjuk yang label piping ID-nya duduk
    persis DI ATASNYA (pola PetroChina: label ditulis di atas garis pendek, lalu garis
    diagonal menunjuk ke pipa asli). Ciri pembeda vs pipa attached asli:
      (1) garis menempel tepi BAWAH bbox label (label horizontal; utk label vertikal:
          tepi kiri/kanan), (2) mayoritas bentang garis berada di bawah label, dan
      (3) panjang garis ~ lebar label (pipa asli menerus JAUH melampaui label).
    Return dict {pid_idx: [run_idx, ...]}. Run ini BUKAN pipa: jangan diasosiasi &
    jangan diwarnai; endpoint-nya jadi ANCHOR menelusuri diagonal ke pipa asli."""
    S = dpi / 72.0
    near_lo, near_hi = 8 * S, 9 * S            # toleransi jarak garis ke tepi bbox
    out = {}
    for pi, p in enumerate(pids):
        horizontal = (p.x2 - p.x1) >= (p.y2 - p.y1)
        lw = (p.x2 - p.x1) if horizontal else (p.y2 - p.y1)
        if lw <= 0:
            continue
        hits = []
        for i, r in enumerate(runs):
            if r.length > 1.9 * lw:            # terlalu panjang utk underline -> pipa asli
                continue
            if horizontal and r.axis == "h":
                y = (r.y1 + r.y2) / 2
                if not (p.y2 - near_lo <= y <= p.y2 + near_hi):
                    continue
                lo, hi = min(r.x1, r.x2), max(r.x1, r.x2)
                ov = min(hi, p.x2) - max(lo, p.x1)
                if ov > 0 and ov >= 0.55 * (hi - lo):
                    hits.append(i)
            elif not horizontal and r.axis == "v":
                x = (r.x1 + r.x2) / 2
                if not (p.x1 - near_hi <= x <= p.x1 + near_lo or
                        p.x2 - near_lo <= x <= p.x2 + near_hi):
                    continue
                lo, hi = min(r.y1, r.y2), max(r.y1, r.y2)
                ov = min(hi, p.y2) - max(lo, p.y1)
                if ov > 0 and ov >= 0.55 * (hi - lo):
                    hits.append(i)
        if hits:
            out[pi] = hits
    return out


def _hsegs(r, tol):
    for (ax, ay), (bx, by) in r.segments():
        if abs(ay - by) <= tol and abs(bx - ax) > tol:
            yield (min(ax, bx), max(ax, bx), (ay + by) / 2)


def _vsegs(r, tol):
    for (ax, ay), (bx, by) in r.segments():
        if abs(ax - bx) <= tol and abs(by - ay) > tol:
            yield (min(ay, by), max(ay, by), (ax + bx) / 2)


def associate(pids, runs, img_bgr, dpi=350, main_len_pt=45,
              attach_gap_pt=34, leader_max_pt=80, ink_frac_min=0.45, overlap_pad_pt=12,
              under_leader_max_pt=48):
    """Asosiasi piping ID -> pipa (polyline-aware). Aturan domain (dari penulis):

      * Teks HORIZONTAL  -> pipa = garis HORIZONTAL yang PERSIS DI BAWAH teks (x-nya
        menaungi teks). Garis di ATAS teks diabaikan: itu milik line number lain.
      * Teks VERTIKAL    -> pipa = garis VERTIKAL di SAMPING teks (kiri/kanan; y-nya
        menaungi teks). Sisi terdekat menang.
      * Garis penunjuk (leader): bila tak ada pipa paralel yang menaungi, telusuri stub
        pendek dari teks ke ujungnya yang MENEMPEL garis H/V panjang = pipa asli.
      * 1 pipa TIDAK boleh diklaim 2 piping ID BERBEDA: yang lebih dekat menang, yang
        kalah dicoba-ulang leader lalu 'none' (masuk Review) — mencegah 'nyomot pipa
        milik ID lain'.

    Perubahan kunci vs versi lama: TIDAK lintas-orientasi (dulu teks-H boleh nempel pipa-V
    milik ID lain), dan arah 'bawah/samping' ditegakkan. Leader/underline TIDAK diwarnai.
    Return list assoc dict {pid_idx, run, state}."""
    S = dpi / 72.0
    main_len = main_len_pt * S
    gap = attach_gap_pt * S                        # jarak tegak-lurus maks teks->pipa
    leadmax = leader_max_pt * S
    pad = overlap_pad_pt * S
    tol = max(3, int(7 * S))
    ink = (cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr) < 128
    H, W = ink.shape
    frame_len = 0.6 * max(H, W)                     # run sepanjang ini ~ bingkai halaman

    # kandidat garis penunjuk ber-label (label duduk di atas garis pendek). CATATAN: JANGAN
    # langsung dikeluarkan dari kandidat — dulu ini men-flag banyak PIPA ASLI (label yg
    # kebetulan menempel pipanya) sbg 'underline' -> pipanya hilang. Sekarang direct() jalan
    # dulu; run di sini hanya di-flag underline bila TAK jadi pipa milik siapa pun (di bawah).
    umap = _underline_idxs(pids, runs, dpi)
    already_ul = {i for i, r in enumerate(runs) if r.underline}   # dari mark_text_underlines

    def _tables(idxs):
        Hs = [(i, lo, hi, c) for i in idxs for (lo, hi, c) in _hsegs(runs[i], tol)]
        Vs = [(i, lo, hi, c) for i in idxs for (lo, hi, c) in _vsegs(runs[i], tol)]
        return Hs, Vs

    ok = [i for i, r in enumerate(runs)
          if i not in already_ul and r.length < frame_len]  # buang bingkai + garis-bawah teks
    main_idx = [i for i in ok if runs[i].length >= main_len]
    Hm, Vm = _tables(main_idx)                      # prioritas: pipa panjang (main line)
    Ha, Va = _tables(ok)                            # fallback: termasuk stub pendek

    def direct(p, Hs, Vs):
        """(run_idx, jarak) pipa paralel yang menaungi label di sisi bawah(H)/samping(V).
        'Menaungi' = pusat label di atas pipa ATAU pipa menutupi >=55% bentang teks (label
        sering sedikit bergeser dari pipanya / pipa terpotong simbol). None bila tak ada."""
        horiz = (p.x2 - p.x1) >= (p.y2 - p.y1)
        cx, cy = p.cx, p.cy
        best, bestkey = None, 1e18
        if horiz:
            tw = max(1.0, p.x2 - p.x1); th = p.y2 - p.y1
            for i, lo, hi, yc in Hs:
                ov = (min(hi, p.x2) - max(lo, p.x1)) / tw
                if not (lo - pad <= cx <= hi + pad or ov >= 0.55):
                    continue
                if yc < p.y1 - pad:                 # DI ATAS teks -> punya line number lain
                    continue
                d = yc - p.y2                        # >0 = di bawah tepi bawah teks
                if d > gap:
                    continue
                # KEY selalu POSITIF & berjenjang (bug lama: d negatif utk pipa di dalam/atas
                # teks -> key negatif -> label BAWAH mencuri pipa milik label ATAS-nya):
                #   pipa DI BAWAH teks  -> key=d (utamakan terdekat)
                #   pipa DI DALAM teks  -> penalti ringan (label duduk di atas pipanya sendiri)
                #   pipa DI ATAS teks   -> penalti berat (hampir pasti punya label lain)
                if d >= 0:
                    key = d
                elif d >= -th:
                    key = -d
                else:
                    key = (-d) * 3.0 + 500.0
                if key < bestkey:
                    bestkey, best = key, i
        else:
            th = max(1.0, p.y2 - p.y1)
            for i, lo, hi, xc in Vs:
                ov = (min(hi, p.y2) - max(lo, p.y1)) / th
                if not (lo - pad <= cy <= hi + pad or ov >= 0.55):
                    continue
                d = abs(xc - cx)
                if d > gap:
                    continue
                if d < bestkey:
                    bestkey, best = d, i
        return (best, bestkey) if best is not None else None

    def leader(p, anchors=None, targets=None, maxd=None):
        """Telusuri garis penunjuk via kontinuitas tinta dari anchor ke kandidat pipa."""
        best, bestscore = None, -1.0
        md = maxd or leadmax
        for i in (targets if targets is not None else main_idx):
            r = runs[i]
            for ax, ay in (anchors or [(p.cx, p.cy)]):
                fx, fy = _foot_on_run(r, ax, ay)
                d = ((ax - fx) ** 2 + (ay - fy) ** 2) ** 0.5
                if not (1 < d <= md):
                    continue
                if _ink_frac(ink, ax, ay, fx, fy) < ink_frac_min:
                    continue
                score = _ink_frac(ink, ax, ay, fx, fy) - (d / md) * 0.3
                if score > bestscore:
                    bestscore, best = score, i
        return best

    # ---- tahap 1: tentukan kandidat + jarak per piping ID (belum resolve konflik) ----
    # PRIORITAS: direct() (pipa persis di bawah/samping) selalu dicoba DULU, termasuk utk
    # label yg 'di atas garis pendek' — karena garis itu sering justru pipanya sendiri.
    # Baru bila direct() gagal, telusuri leader (dgn anchor ujung garis penunjuk bila ada).
    picks = []                                      # {pid_idx, run_idx, key, state}
    for idx, p in enumerate(pids):
        hit = direct(p, Hm, Vm) or direct(p, Ha, Va)   # main dulu, lalu stub pendek
        if hit is not None:
            picks.append({"pid_idx": idx, "run_idx": hit[0], "key": hit[1],
                          "state": "attached"})
            continue
        anchors = [(p.cx, p.cy)]
        maxd = leadmax
        if idx in umap:                             # ada garis penunjuk pendek -> pakai ujungnya
            for ri in umap[idx]:
                u = runs[ri]
                anchors += [tuple(u.points[0]), tuple(u.points[-1])]
            maxd = under_leader_max_pt * S
        ri = leader(p, anchors=anchors, targets=ok, maxd=maxd)
        picks.append({"pid_idx": idx, "run_idx": ri, "key": 1e6,
                      "state": "leader" if ri is not None else "none"})

    # ---- tahap 2: resolusi konflik 1-pipa-2-ID (piping ID string BERBEDA) ----
    # Untuk tiap run yang diklaim >1 ID berbeda: yang jaraknya paling kecil menang; yang
    # kalah dicoba-ulang leader (targets = run yg belum terpakai), gagal -> none.
    def _claim_map():
        m = defaultdict(list)
        for k, pk in enumerate(picks):
            if pk["run_idx"] is not None:
                m[pk["run_idx"]].append(k)
        return m

    for _ in range(3):                              # beberapa putaran (rantai konflik)
        conflict = False
        used = {pk["run_idx"] for pk in picks if pk["run_idx"] is not None}
        for ri, ks in _claim_map().items():
            names = {pids[picks[k]["pid_idx"]].pid for k in ks}
            if len(ks) < 2 or len(names) < 2:       # identik = boleh berbagi (dedup nanti)
                continue
            conflict = True
            keep = min(ks, key=lambda k: picks[k]["key"])
            for k in ks:
                if k == keep:
                    continue
                p = pids[picks[k]["pid_idx"]]
                free = [i for i in ok if i not in used]
                ri2 = leader(p, targets=free)
                picks[k]["run_idx"] = ri2
                picks[k]["key"] = 1e6
                picks[k]["state"] = "leader" if ri2 is not None else "none"
                if ri2 is not None:
                    used.add(ri2)
        if not conflict:
            break

    # garis penunjuk (kandidat underline) yang TIDAK menjadi pipa milik siapa pun -> flag
    # underline (bukan pipa: keluarkan dari coverage & propagasi). Yg dipakai sbg pipa attached
    # tetap pipa.
    chosen = {pk["run_idx"] for pk in picks if pk["run_idx"] is not None}
    for lst in umap.values():
        for ri in lst:
            if ri not in chosen:
                runs[ri].underline = True

    result = []
    for pk in picks:
        r = runs[pk["run_idx"]] if pk["run_idx"] is not None else None
        if r is not None:
            p = pids[pk["pid_idx"]]
            r.pid = p.pid; r.fluid = p.fluid
        result.append({"pid_idx": pk["pid_idx"], "run": r, "state": pk["state"]})
    return result


# --------------------------------------------------- visual ------------------------
def _fluid_color(fluid: str):
    palette = [(0, 0, 220), (220, 120, 0), (0, 170, 0), (200, 0, 200), (0, 200, 220),
               (120, 60, 0), (0, 120, 255), (180, 0, 90), (90, 90, 0), (0, 90, 180)]
    if not fluid:
        return (160, 160, 160)
    return palette[hash(fluid) % len(palette)]


def _polyline(vis, r, col, th):
    pts = np.array(r.points, np.int32).reshape(-1, 1, 2)
    cv2.polylines(vis, [pts], False, col, th)


def draw(img_bgr, runs, pids, assoc) -> np.ndarray:
    vis = img_bgr.copy()
    for r in runs:
        _polyline(vis, r, (195, 195, 195), 1)
    for a in assoc:
        r = a["run"]
        if r is None:
            continue
        p = pids[a["pid_idx"]]
        col = _fluid_color(p.fluid)
        _polyline(vis, r, col, 5)
        if a["state"] == "leader":
            fx, fy = _foot_on_run(r, p.cx, p.cy)
            cv2.line(vis, (int(p.cx), int(p.cy)), (int(fx), int(fy)), col, 1, cv2.LINE_AA)
    for p in pids:
        cv2.rectangle(vis, (int(p.x1), int(p.y1)), (int(p.x2), int(p.y2)),
                      _fluid_color(p.fluid), 2)
    return vis
