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
import math
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
    color: str = "#2563EB"                    # Default neutral blue (Phase B.5)
    id: str = ""                              # Unique run identifier (e.g. run-0)
    label: str = ""                           # Custom tag or line label
    manual: bool = False                      # True if manually created/edited by engineer
    equipment_outline: bool = False           # True = kontur alat (vessel/tangki), BUKAN pipa

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
        if key == "color": return getattr(self, "color", "#2563EB")
        if key == "id": return getattr(self, "id", "")
        if key == "label": return getattr(self, "label", getattr(self, "pid", ""))
        if key == "manual": return getattr(self, "manual", False)
        if key == "equipment_outline": return getattr(self, "equipment_outline", False)
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
        # Kontur alat (equipment outline) bukan tepi kotak simbol -> jangan dibuang di sini.
        if getattr(s, "equipment_outline", False):
            out.append(s)
            continue
        (x0, y0), (x1, y1) = s.points[0], s.points[-1]
        drop = False
        for bx0, by0, bx1, by1 in boxes:
            # inside_span HARUS ketat: garis tepi kotak yg sejajar tepi biasanya
            # punya kedua ujung tepat di tepi (±2px). Stub pipa yg keluar dari
            # dalam kotak (nozzle) punya min. satu ujung di luar -> jangan dibuang.
            if s.axis == "h":
                lo, hi, yy = min(x0, x1), max(x0, x1), y0
                near_edge = abs(yy - by0) <= m or abs(yy - by1) <= m
                inside_span = lo >= bx0 - 2 and hi <= bx1 + 2
            else:
                lo, hi, xx = min(y0, y1), max(y0, y1), x0
                near_edge = abs(xx - bx0) <= m or abs(xx - bx1) <= m
                inside_span = lo >= by0 - 2 and hi <= by1 + 2
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


def _seg_crosses_boxes(p, q, boxes, step_px=4.0):
    """True bila segmen p->q melewati interior salah satu kotak (mis. bbox instrumen).
    Dipakai bridging: celah di instrumen TIDAK boleh dijembatani pipa.
    Dioptimasi menggunakan Fast AABB Pre-filtering + Liang-Barsky clipping analitik (O(1) per box)."""
    if not boxes:
        return False
    px, py = float(p[0]), float(p[1])
    qx, qy = float(q[0]), float(q[1])
    dx = qx - px
    dy = qy - py
    sx1 = px if dx >= 0 else qx
    sx2 = qx if dx >= 0 else px
    sy1 = py if dy >= 0 else qy
    sy2 = qy if dy >= 0 else py

    for bx1, by1, bx2, by2 in boxes:
        # 1. Fast AABB reject (eliminasi 98%+ box dalam 4 perbandingan)
        if sx2 < bx1 or sx1 > bx2 or sy2 < by1 or sy1 > by2:
            continue
        # 2. Bila salah satu endpoint berada di dalam atau di batas box
        if (bx1 <= px <= bx2 and by1 <= py <= by2) or (bx1 <= qx <= bx2 and by1 <= qy <= by2):
            return True
        # 3. Liang-Barsky parametric line-segment AABB clipping
        t0, t1 = 0.0, 1.0
        p_q = ((-dx, px - bx1), (dx, bx2 - px), (-dy, py - by1), (dy, by2 - py))
        cross = True
        for p_k, q_k in p_q:
            if p_k == 0:
                if q_k < 0:
                    cross = False
                    break
            else:
                r = q_k / p_k
                if p_k < 0:
                    if r > t1:
                        cross = False
                        break
                    if r > t0:
                        t0 = r
                else:
                    if r < t0:
                        cross = False
                        break
                    if r < t1:
                        t1 = r
        if cross and t0 <= t1:
            return True
    return False


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
MAX_EQUIP_AREA_FRAC = 0.95


def suppress_equipment_interior(segs, detections, dpi=350, margin_pt=3, page_wh=None):
    """Equipment TIDAK di-trace jadi pipa SAMA SEKALI (permintaan penulis): tiap segmen H/V
    di-CLIP di batas bbox equipment — bagian DI DALAM equipment dibuang, bagian di LUAR
    (pipa asli menuju nozzle) dipertahankan. Beda dari versi lama yg cuma buang segmen yg
    KEDUA ujungnya di dalam (pipa masuk equipment jadi masih ke-trace ke dalam).
    Efek: pipa berhenti di tepi equipment. Bila sebuah piping ID kehilangan pipanya karena
    ini -> otomatis jadi 'pid_none' di panel Review (user yang memutuskan)."""
    eqs = [d for d in (detections or []) if d.get("coarse") == "equipment"]
    if not eqs:
        return segs
    m = margin_pt * dpi / 72.0
    boxes = [(d["x1"] + m, d["y1"] + m, d["x2"] - m, d["y2"] - m) for d in eqs]
    boxes = [b for b in boxes if b[0] < b[2] and b[1] < b[3]]
    keep = 12 * dpi / 72.0
    out = []
    for s in segs:
        # Kontur alat (equipment outline) BUKAN pipa: jangan di-clip di batas bbox alat,
        # justru ia MENELUSURI batas itu.
        if getattr(s, "equipment_outline", False):
            out.append(s)
            continue
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


def snap_endpoints_to_equipment(runs, detections, snap_radius_pt=14, dpi=350, snap_mask=None):
    """Proyeksikan ujung garis pipa (endpoints) yang berakhir dekat perimeter
    bounding box equipment agar menempel persis ke dinding alat (nozzle connection),
    bukan mengambang di luar kotak. (Phase B.5)

    Guard penting (fix x=1362):
      * Run `equipment_outline` TIDAK di-snap sama sekali — konturnya sudah benar.
      * Bila `snap_mask` diberikan (mask ink outline alat): ujung pipa di-snap ke INK
        DINDING SEBENARNYA via ray-search searah sumbu pipa — bukan ke tepi bbox
        deteksi. Kotak YOLO sering lebih lebar dari alat (vessel 605-V-221-B:
        kotak x1=1362, dinding asli x≈1393); menarik ujung ke tepi kotak membuat
        kolom palsu x=1362 yang kemudian ter-bridge jadi garis zigzag.
      * Tanpa mask (fallback lama): endpoint yang sudah BERADA di dalam kotak hanya
        di-snap bila arah datangnya menuju tepi terdekat (tidak ditarik menyamping).
    """
    if not runs or not detections:
        return runs
    eqs = [d for d in detections if d.get("coarse") == "equipment"]
    if not eqs:
        return runs

    def _is_eq(r):
        if hasattr(r, "equipment_outline"):
            return bool(r.equipment_outline)
        return bool(r.get("equipment_outline", False))

    snap_radius_px = snap_radius_pt * dpi / 72.0
    boxes = [(float(d["x1"]), float(d["y1"]), float(d["x2"]), float(d["y2"])) for d in eqs]

    _mask_h, _mask_w = (snap_mask.shape[:2] if snap_mask is not None else (0, 0))

    def _ray_to_mask(px, py, ux, uy, max_r, win=2):
        """Titik pertama di sepanjang arah (ux,uy) yang menyentuh ink-mask (dinding alat)."""
        for s in range(2, int(max_r) + 1):
            x = px + ux * s
            y = py + uy * s
            for o in (-win, -1, 0, 1, win):
                xx = int(round(x - uy * o))
                yy = int(round(y + ux * o))
                if 0 <= xx < _mask_w and 0 <= yy < _mask_h and snap_mask[yy, xx] > 0:
                    return (int(round(x)), int(round(y)))
        return None

    out = []
    for r in runs:
        if _is_eq(r):
            out.append(r)
            continue
        if hasattr(r, "points"):
            pts = [[float(p[0]), float(p[1])] for p in r.points]
        else:
            pts = [[float(p[0]), float(p[1])] for p in r.get("points", [])]

        if len(pts) < 2:
            out.append(r)
            continue

        modified = False
        # Check both ends: pts[0] and pts[-1]
        for end_idx in (0, -1):
            px, py = pts[end_idx][0], pts[end_idx][1]
            prev_idx = 1 if end_idx == 0 else -2
            vx = px - pts[prev_idx][0]
            vy = py - pts[prev_idx][1]
            vlen = math.hypot(vx, vy)
            if vlen < 1e-6:
                continue

            if snap_mask is not None:
                # Ray-search ke ink dinding alat searah sumbu pipa (hanya bila dekat alat).
                # Bila ujung sudah berada DI atas ink (menempel dinding), jangan digeser.
                already = False
                ex, ey = int(round(px)), int(round(py))
                for oy in (-2, 0, 2):
                    for ox in (-2, 0, 2):
                        xx, yy = ex + ox, ey + oy
                        if 0 <= xx < _mask_w and 0 <= yy < _mask_h and snap_mask[yy, xx] > 0:
                            already = True
                            break
                    if already:
                        break
                if already:
                    continue
                near = any(bx1 - snap_radius_px <= px <= bx2 + snap_radius_px and
                           by1 - snap_radius_px <= py <= by2 + snap_radius_px
                           for bx1, by1, bx2, by2 in boxes)
                if near:
                    hit = _ray_to_mask(px, py, vx / vlen, vy / vlen, snap_radius_px)
                    if hit is not None:
                        pts[end_idx] = [hit[0], hit[1]]
                        modified = True
                continue

            best_snap = None
            min_d = float("inf")

            for bx1, by1, bx2, by2 in boxes:
                cx = max(bx1, min(px, bx2))
                cy = max(by1, min(py, by2))

                # If endpoint is slightly inside the box: only snap when the approach
                # direction points TOWARD that nearest edge (i.e. the line overshot the
                # wall). Never pull an endpoint sideways/backwards to a box edge.
                if bx1 <= px <= bx2 and by1 <= py <= by2:
                    dl, dr = px - bx1, bx2 - px
                    dt, db = py - by1, by2 - py
                    m = min(dl, dr, dt, db)
                    toward = False
                    if m == dl:
                        toward = vx < 0
                    elif m == dr:
                        toward = vx > 0
                    elif m == dt:
                        toward = vy < 0
                    else:
                        toward = vy > 0
                    if toward and m < min_d:
                        min_d = m
                        if m == dl: best_snap = (bx1, py)
                        elif m == dr: best_snap = (bx2, py)
                        elif m == dt: best_snap = (px, by1)
                        else: best_snap = (px, by2)
                else:
                    # Point is outside box.
                    # 1. Directional snap:
                    # If pipe run is heading horizontally towards vertical edge
                    if abs(vx) >= 1.2 * abs(vy) and by1 - snap_radius_px * 0.5 <= py <= by2 + snap_radius_px * 0.5:
                        if vx > 0 and 0 <= bx1 - px <= snap_radius_px:
                            d = bx1 - px
                            if d < min_d:
                                min_d = d
                                best_snap = (bx1, min(by2, max(by1, py)))
                        elif vx < 0 and 0 <= px - bx2 <= snap_radius_px:
                            d = px - bx2
                            if d < min_d:
                                min_d = d
                                best_snap = (bx2, min(by2, max(by1, py)))

                    # If pipe run is heading vertically towards horizontal edge
                    elif abs(vy) >= 1.2 * abs(vx) and bx1 - snap_radius_px * 0.5 <= px <= bx2 + snap_radius_px * 0.5:
                        if vy > 0 and 0 <= by1 - py <= snap_radius_px:
                            d = by1 - py
                            if d < min_d:
                                min_d = d
                                best_snap = (min(bx2, max(bx1, px)), by1)
                        elif vy < 0 and 0 <= py - by2 <= snap_radius_px:
                            d = py - by2
                            if d < min_d:
                                min_d = d
                                best_snap = (min(bx2, max(bx1, px)), by2)

                    # 2. Geometric fallback snap to nearest point on perimeter (only if does not severely warp orientation)
                    d_geom = math.hypot(px - cx, py - cy)
                    if d_geom < min_d:
                        warp_ok = True
                        if abs(vx) >= 1.2 * abs(vy) and abs(py - cy) > 8:
                            warp_ok = False
                        elif abs(vy) >= 1.2 * abs(vx) and abs(px - cx) > 8:
                            warp_ok = False
                        if warp_ok:
                            min_d = d_geom
                            best_snap = (cx, cy)

            if best_snap is not None and min_d <= snap_radius_px:
                pts[end_idx] = [int(round(best_snap[0])), int(round(best_snap[1]))]
                modified = True

        if modified:
            clean_pts = [(int(p[0]), int(p[1])) for p in pts]
            if hasattr(r, "points"):
                out.append(PipeRun(
                    points=clean_pts,
                    axis=getattr(r, "axis", "poly"),
                    pid=getattr(r, "pid", ""),
                    fluid=getattr(r, "fluid", ""),
                    underline=getattr(r, "underline", False),
                    color=getattr(r, "color", "#2563EB"),
                    equipment_outline=bool(getattr(r, "equipment_outline", False)),
                ))
            else:
                new_r = dict(r)
                new_r["points"] = clean_pts
                new_r["x1"] = min(p[0] for p in clean_pts)
                new_r["y1"] = min(p[1] for p in clean_pts)
                new_r["x2"] = max(p[0] for p in clean_pts)
                new_r["y2"] = max(p[1] for p in clean_pts)
                new_r["color"] = r.get("color", "#2563EB")
                out.append(new_r)
        else:
            out.append(r)

    return out


def snap_t_junctions(runs, near_px=16, min_angle_deg=75.0, max_angle_deg=105.0):
    """T-JUNCTION ORTHOGONAL SNAP (pre-merge continuity fix).

    Skeleton graph segmentation often leaves a branch pipe ending a few pixels SHORT of the
    main run it feeds into (a T-junction), because the junction node cluster is dilated and
    the branch edge is cropped at the cluster boundary. The result is a visible 5-15px gap at
    every T-junction.

    This pass walks every polyline endpoint (dead-end, i.e. an endpoint that is not shared with
    another run) and, when it lies within `near_px` of the BODY of another run AND the axis of
    approach is roughly perpendicular (default 75-105 degrees), projects the endpoint exactly
    onto the nearest point of that run's body. Endpoints whose approach is nearly collinear
    (a straight continuation / butt joint) are left untouched so we never fold a line onto
    itself.

    Conservative by design:
      * only dead-end endpoints are considered (no touching an endpoint that already meets a run),
      * the projected foot must fall strictly INSIDE the target segment (not at a vertex), so a
        simple extension along the branch axis is preferred and no spurious diagonal is created,
      * runs are never merged here - only the endpoint coordinate is moved.
    """
    if not runs or len(runs) < 2:
        return runs

    def _pts(r):
        return r.points if hasattr(r, "points") else r.get("points", [])

    def _endpoints():
        """Map rounded endpoint coordinate -> count, to detect shared (non-dead-end) vertices."""
        cnt = {}
        for r in runs:
            pts = _pts(r)
            if len(pts) < 2:
                continue
            for p in (pts[0], pts[-1]):
                key = (int(round(p[0])), int(round(p[1])))
                cnt[key] = cnt.get(key, 0) + 1
        return cnt

    counts = _endpoints()
    lo, hi = math.cos(math.radians(max_angle_deg)), math.cos(math.radians(min_angle_deg))

    if hasattr(runs[0], "points"):
        new_runs = [PipeRun(points=[(int(p[0]), int(p[1])) for p in _pts(r)],
                            axis=getattr(r, "axis", "poly"), pid=getattr(r, "pid", ""),
                            fluid=getattr(r, "fluid", ""), underline=getattr(r, "underline", False),
                            color=getattr(r, "color", "#2563EB"),
                            equipment_outline=bool(getattr(r, "equipment_outline", False))) for r in runs]
    else:
        new_runs = [dict(r) for r in runs]

    for i, r in enumerate(new_runs):
        pts = [list(p) for p in _pts(r)]
        if len(pts) < 2:
            continue
        for end_idx in (0, -1):
            px, py = float(pts[end_idx][0]), float(pts[end_idx][1])
            key = (int(round(px)), int(round(py)))
            if counts.get(key, 0) > 1:
                continue  # shared vertex -> already connected
            nb = pts[1] if end_idx == 0 else pts[-2]
            vx, vy = px - float(nb[0]), py - float(nb[1])
            vlen = math.hypot(vx, vy)
            if vlen < 1e-6:
                continue
            vx, vy = vx / vlen, vy / vlen

            best = None
            best_d = float("inf")
            for j, other in enumerate(new_runs):
                if j == i:
                    continue
                opts = _pts(other)
                for k in range(len(opts) - 1):
                    ax, ay = float(opts[k][0]), float(opts[k][1])
                    bx, by = float(opts[k + 1][0]), float(opts[k + 1][1])
                    dx, dy = bx - ax, by - ay
                    seg_len_sq = dx * dx + dy * dy
                    if seg_len_sq < 1e-6:
                        continue
                    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
                    if t <= 0.02 or t >= 0.98:
                        continue  # foot at a vertex -> an endpoint joint, not a T
                    fx, fy = ax + t * dx, ay + t * dy
                    d = math.hypot(px - fx, py - fy)
                    if d < 1e-6 or d > near_px or d >= best_d:
                        continue
                    # approach must be roughly perpendicular to the target segment
                    slen = math.sqrt(seg_len_sq)
                    cos_ang = abs(vx * (dx / slen) + vy * (dy / slen))
                    if lo <= cos_ang <= hi:
                        best_d = d
                        best = (fx, fy)
            if best is not None:
                pts[end_idx] = [int(round(best[0])), int(round(best[1]))]

        clean = [(int(p[0]), int(p[1])) for p in pts]
        if hasattr(r, "points"):
            new_runs[i] = PipeRun(points=clean, axis=getattr(r, "axis", "poly"),
                                  pid=getattr(r, "pid", ""), fluid=getattr(r, "fluid", ""),
                                  underline=getattr(r, "underline", False),
                                  color=getattr(r, "color", "#2563EB"),
                                  equipment_outline=bool(getattr(r, "equipment_outline", False)))
        else:
            nr = dict(r)
            nr["points"] = clean
            nr["x1"] = min(p[0] for p in clean)
            nr["y1"] = min(p[1] for p in clean)
            nr["x2"] = max(p[0] for p in clean)
            nr["y2"] = max(p[1] for p in clean)
            nr["color"] = r.get("color", "#2563EB")
            new_runs[i] = nr

    return new_runs

def stitch_region_runs(new_runs, existing_runs, bbox, snap_px=18):
    """BOX TRACE STITCHING (merge jalur ROI baru ke run eksisting).

    Dipanggil setelah `/trace-region` men-trace isi kotak seleksi. Alih-alih selalu menambah
    run independen (yang membuat pipa terduplikasi di DB/sidebar), coba sambungkan jalur baru
    ke pipa yang sudah ada:

      * Cari endpoint run eksisting yang berada DI DALAM atau <= `snap_px` dari bbox seleksi.
      * 1-to-1 (baru menyentuh TEPAT satu ujung run eksisting) -> PANJANGKAN run itu
        (prefix/suffix titik baru), tanpa run baru.
      * 1-to-2 (jalur baru menjembatani ujung Pipa M dan ujung Pipa F secara linear) ->
        GABUNG M + S + F jadi satu polyline, hapus F (hindari duplikasi).
      * AMBIGU (>2 kandidat, percabangan T-junction, arah tak kolinear) -> simpan jalur baru
        sebagai run independen, TAPI snap ujungnya ke titik terdekat run eksisting.

    `bbox` = (x1, y1, x2, y2) koordinat global seleksi (SEBELUM padding) untuk mencari kandidat.
    `new_runs`/`existing_runs` = list of dict (koordinat global) dengan key 'points'.

    Mengembalikan `(updated_existing, remaining_new, consumed_ids)`: existing_runs yang mungkin
    sudah diperpanjang, new_runs yang belum terserap, dan id run baru yang sudah di-merge
    (untuk dibuang dari daftar yang akan disimpan).
    """
    if not new_runs:
        return existing_runs, [], []

    bx1, by1, bx2, by2 = [float(v) for v in bbox]

    def _pts(r):
        return [tuple(map(float, p)) for p in r.get("points", [])]

    def _in_or_near(pt):
        px, py = pt
        return (bx1 - snap_px <= px <= bx2 + snap_px) and (by1 - snap_px <= py <= by2 + snap_px)

    def _dir(a, b):
        vx, vy = b[0] - a[0], b[1] - a[1]
        l = math.hypot(vx, vy)
        return (vx / l, vy / l) if l > 1e-6 else (0.0, 0.0)

    def _cos(u, v):
        return max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))

    existing = [dict(r) for r in existing_runs]
    remaining_new = []
    consumed_ids = []

    for nr in new_runs:
        npts = [list(map(int, p)) for p in nr.get("points", [])]
        if len(npts) < 2:
            continue
        n_a, n_b = npts[0], npts[-1]

        # Candidate existing endpoints touching the selection bbox.
        cands = []  # (run_index, which_end('a'|'b'), dist_to_box_mid, tangent_dir)
        for ri, er in enumerate(existing):
            epts = _pts(er)
            if len(epts) < 2:
                continue
            for which, end, nb in (("a", epts[0], epts[1]), ("b", epts[-1], epts[-2])):
                if _in_or_near(end):
                    # Determine which NEW endpoint is nearest to this existing end.
                    d_new_a = math.hypot(end[0] - n_a[0], end[1] - n_a[1])
                    d_new_b = math.hypot(end[0] - n_b[0], end[1] - n_b[1])
                    cands.append({
                        "ri": ri, "which": which,
                        "end": (int(round(end[0])), int(round(end[1]))),
                        "d": min(d_new_a, d_new_b),
                        "new_end": "a" if d_new_a <= d_new_b else "b",
                        "inward": _dir(end, nb),  # tangent pointing INTO the existing run
                    })

        # Prefer the closest existing ends; de-duplicate by run index (keep nearest end).
        by_run = {}
        for c in cands:
            k = c["ri"]
            if k not in by_run or c["d"] < by_run[k]["d"]:
                by_run[k] = c
        matching = sorted(by_run.values(), key=lambda c: c["d"])

        if len(matching) == 1:
            # --- 1-to-1: extend the single existing run. ---
            m = matching[0]
            ext = [tuple(p) for p in npts]
            # Orient the new path so ext[0] is the point that coincides with the matched
            # existing end (m["end"]); the path then leaves the joint outward.
            if m["new_end"] == "b":
                ext = ext[::-1]
            base = list(_pts(existing[m["ri"]]))
            if m["which"] == "a":
                merged_pts = [[int(p[0]), int(p[1])] for p in (ext[::-1] + base)]
            else:
                merged_pts = [[int(p[0]), int(p[1])] for p in (base + ext)]
            _apply_points(existing[m["ri"]], merged_pts)
            if nr.get("label") and not existing[m["ri"]].get("label"):
                existing[m["ri"]]["label"] = nr["label"]
                if nr.get("pid"):
                    existing[m["ri"]]["pid"] = nr["pid"]
                if nr.get("fluid"):
                    existing[m["ri"]]["fluid"] = nr["fluid"]
            consumed_ids.append(nr.get("id"))
        elif len(matching) == 2:
            # --- 1-to-2: bridge M (a) and F (b). Check collinearity sanity. ---
            m, f = matching[0], matching[1]
            if m["ri"] == f["ri"]:
                remaining_new.append(nr)
                continue
            # Orient new path so its start meets M and its end meets F.
            ext = [tuple(p) for p in npts]
            if m["new_end"] == "b" and f["new_end"] == "a":
                pass
            elif m["new_end"] == "a" and f["new_end"] == "b":
                ext = ext[::-1]
            else:
                # Both existing ends nearest to the SAME new endpoint -> not a clean 1-to-1 bridge.
                remaining_new.append(_snap_new_endpoints(nr, matching))
                continue

            base_m = list(_pts(existing[m["ri"]]))
            base_f = list(_pts(existing[f["ri"]]))
            mid = [[int(p[0]), int(p[1])] for p in ext]
            if m["which"] == "a":
                base_m = base_m[::-1]
            if f["which"] == "b":
                base_f = base_f[::-1]
            merged_pts = [[int(p[0]), int(p[1])] for p in base_m] + mid + [[int(p[0]), int(p[1])] for p in base_f]
            _apply_points(existing[m["ri"]], merged_pts)
            if not existing[m["ri"]].get("label"):
                lbl = existing[f["ri"]].get("label") or nr.get("label")
                if lbl:
                    existing[m["ri"]]["label"] = lbl
                    existing[m["ri"]]["pid"] = existing[f["ri"]].get("pid") or nr.get("pid") or lbl
                    existing[m["ri"]]["fluid"] = existing[f["ri"]].get("fluid") or nr.get("fluid", "")
            consumed_ids.append(nr.get("id"))
            consumed_ids.append(existing[f["ri"]].get("id"))
            existing[f["ri"]]["_drop"] = True
        else:
            # --- Ambiguous (T-junction / many ways): keep independent, snap endpoints. ---
            remaining_new.append(_snap_new_endpoints(nr, matching))

    existing = [r for r in existing if not r.get("_drop")]
    consumed = [cid for cid in consumed_ids if cid]
    return existing, remaining_new, consumed


def _apply_points(run, clean_pts):
    if not clean_pts:
        run["points"] = []
        return
    dedup = [clean_pts[0]]
    for p in clean_pts[1:]:
        if p[0] != dedup[-1][0] or p[1] != dedup[-1][1]:
            dedup.append(p)
    run["points"] = dedup
    run["x1"] = min(p[0] for p in dedup)
    run["y1"] = min(p[1] for p in dedup)
    run["x2"] = max(p[0] for p in dedup)
    run["y2"] = max(p[1] for p in dedup)


def _snap_new_endpoints(nr, matching):
    """Paksa ujung run baru snap menempel ke titik terdekat run eksisting (mode ambigu)."""
    out = dict(nr)
    pts = [list(map(int, p)) for p in nr.get("points", [])]
    if len(pts) < 2 or not matching:
        return out
    for end_idx in (0, -1):
        px, py = pts[end_idx]
        best = None
        best_d = float("inf")
        for m in matching:
            ex, ey = m["end"]
            d = math.hypot(px - ex, py - ey)
            if d < best_d:
                best_d = d
                best = (ex, ey)
        if best is not None:
            pts[end_idx] = [int(best[0]), int(best[1])]
    out["points"] = pts
    out["x1"] = min(p[0] for p in pts)
    out["y1"] = min(p[1] for p in pts)
    out["x2"] = max(p[0] for p in pts)
    out["y2"] = max(p[1] for p in pts)
    return out


def split_poly_run(
    runs: list,
    run_idx: int,
    split_x: float,
    split_y: float,
    piping_ids: list = None,
):
    """
    Pecah runs[run_idx] pada koordinat (split_x, split_y) menjadi dua PipeRun terpisah (run_a dan run_b).
    Mempertahankan kesinambungan koordinat, atribut warna, dan me-reindex piping_ids yang terdampak. (Phase B.5)
    """
    if run_idx < 0 or run_idx >= len(runs):
        raise IndexError(f"Run index {run_idx} out of range (0 to {len(runs)-1})")

    target = runs[run_idx]
    if hasattr(target, "points"):
        pts = [list(p) for p in target.points]
    else:
        pts = [list(p) for p in target.get("points", [])]

    if len(pts) < 2:
        raise ValueError("Cannot split run with fewer than 2 points")

    # Temukan segmen terdekat ke (split_x, split_y)
    best_seg_idx = 0
    best_proj = (float(pts[0][0]), float(pts[0][1]))
    min_dist = float("inf")
    px, py = float(split_x), float(split_y)

    for i in range(len(pts) - 1):
        x0, y0 = float(pts[i][0]), float(pts[i][1])
        x1, y1 = float(pts[i + 1][0]), float(pts[i + 1][1])
        dx, dy = x1 - x0, y1 - y0
        seg_len_sq = dx * dx + dy * dy

        if seg_len_sq <= 1e-6:
            t = 0.0
            proj_x, proj_y = x0, y0
        else:
            t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / seg_len_sq))
            proj_x = x0 + t * dx
            proj_y = y0 + t * dy

        d = math.hypot(px - proj_x, py - proj_y)
        if d < min_dist:
            min_dist = d
            best_seg_idx = i
            best_proj = (proj_x, proj_y)

    split_pt = [int(round(best_proj[0])), int(round(best_proj[1]))]

    # Bentuk points untuk run_a (start -> split_pt)
    pts_a = [list(p) for p in pts[:best_seg_idx + 1]]
    if math.hypot(pts_a[-1][0] - split_pt[0], pts_a[-1][1] - split_pt[1]) > 1.5:
        pts_a.append(split_pt)

    # Bentuk points untuk run_b (split_pt -> end)
    pts_b = [list(p) for p in pts[best_seg_idx + 1:]]
    if not pts_b or math.hypot(pts_b[0][0] - split_pt[0], pts_b[0][1] - split_pt[1]) > 1.5:
        pts_b = [split_pt] + pts_b

    # Pastikan kedua segmen memiliki minimal 2 titik
    if len(pts_a) < 2:
        if len(pts_b) > 2:
            pts_a.append(pts_b[1])
        else:
            # Garis sangat pendek, bagi titik tengah
            mid_x = int(round((pts[0][0] + pts[-1][0]) / 2.0))
            mid_y = int(round((pts[0][1] + pts[-1][1]) / 2.0))
            pts_a = [pts[0], [mid_x, mid_y]]
            pts_b = [[mid_x, mid_y], pts[-1]]

    if len(pts_b) < 2:
        if len(pts_a) > 2:
            pts_b = [pts_a[-2]] + pts_b
        else:
            mid_x = int(round((pts[0][0] + pts[-1][0]) / 2.0))
            mid_y = int(round((pts[0][1] + pts[-1][1]) / 2.0))
            pts_a = [pts[0], [mid_x, mid_y]]
            pts_b = [[mid_x, mid_y], pts[-1]]

    base_id = getattr(target, "id", "") if hasattr(target, "id") else (target.get("id", "") if isinstance(target, dict) else "")
    if not base_id:
        base_id = f"run-{run_idx}"
    id_a = f"{base_id}-a"
    id_b = f"{base_id}-b"

    def _make_run(base_run, new_pts, run_id):
        clean_pts = [(int(p[0]), int(p[1])) for p in new_pts]
        if hasattr(base_run, "points"):
            return PipeRun(
                points=clean_pts,
                axis=getattr(base_run, "axis", "poly"),
                pid=getattr(base_run, "pid", ""),
                fluid=getattr(base_run, "fluid", ""),
                underline=getattr(base_run, "underline", False),
                color=getattr(base_run, "color", "#2563EB"),
                id=run_id,
                label=getattr(base_run, "label", ""),
                manual=True,
                equipment_outline=bool(getattr(base_run, "equipment_outline", False)),
            )
        else:
            r = dict(base_run)
            r["id"] = run_id
            r["points"] = [[int(p[0]), int(p[1])] for p in clean_pts]
            r["x1"] = min(p[0] for p in clean_pts)
            r["y1"] = min(p[1] for p in clean_pts)
            r["x2"] = max(p[0] for p in clean_pts)
            r["y2"] = max(p[1] for p in clean_pts)
            r["color"] = base_run.get("color", "#2563EB")
            r["label"] = base_run.get("label", "")
            r["manual"] = True
            return r

    run_a = _make_run(target, pts_a, id_a)
    run_b = _make_run(target, pts_b, id_b)

    new_runs = list(runs)
    new_runs[run_idx] = run_a
    new_run_idx = run_idx + 1
    new_runs.insert(new_run_idx, run_b)

    # Re-index piping_ids
    new_pids = None
    if piping_ids is not None:
        new_pids = []
        a_cx = sum(p[0] for p in pts_a) / len(pts_a)
        a_cy = sum(p[1] for p in pts_a) / len(pts_a)
        b_cx = sum(p[0] for p in pts_b) / len(pts_b)
        b_cy = sum(p[1] for p in pts_b) / len(pts_b)

        for p in piping_ids:
            p_rec = dict(p)
            r_idx = p_rec.get("run_idx", -1)
            if r_idx == run_idx:
                lx = (p_rec.get("x1", 0) + p_rec.get("x2", 0)) / 2.0
                ly = (p_rec.get("y1", 0) + p_rec.get("y2", 0)) / 2.0
                da = math.hypot(lx - a_cx, ly - a_cy)
                db = math.hypot(lx - b_cx, ly - b_cy)
                p_rec["run_idx"] = run_idx if da <= db else new_run_idx
            elif r_idx > run_idx:
                p_rec["run_idx"] = r_idx + 1

            extra = p_rec.get("extra_runs", [])
            if extra:
                p_rec["extra_runs"] = [er + 1 if er > run_idx else er for er in extra]

            new_pids.append(p_rec)

    return new_runs, new_pids, run_a, run_b, new_run_idx


def suppress_text_artifacts(binary, detections=None, dpi=350, tokens=None,
                           max_side_pt=9.3, max_area_pt2=25.5, max_aspect=3.5,
                           min_area_px=15, protect_boxes=None):
    """Pra-skeletisasi: hitamkan (blackout) pulau piksel yang secara geometri menyerupai
    GLYPH TEKS / coretan kecil yang lolos dari masking OCR.

    Latar belakang: OCR hanya mengembalikan string utuh yang terdeteksinya; satu karakter
    yang gagal dikenali (mis. pecahan ukuran '3/4', kata 'BY INSTR', atau coretan huruf)
    tetap menjadi piksel putih yang lalu di-skeletonize menjadi garis liar (garbage trace).
    Filter ini bekerja pada citra biner (sebelum skeletisasi/morphological skeleton)
    memakai analisis komponen terhubung (`connectedComponentsWithStats`) dan melenyapkan
    pulau dengan profil geometri karakter tipikal P&ID:

      * sisi terpanjang <= 9.3pt (<= 45px @ 350dpi) -> bukan pipa/header equipment,
      * area 15..25.5pt^2 (<= ~600px @ 350dpi) -> pulau glyph kecil, bukan pejalan panjang,
      * aspect ratio tidak memanjang ekstrem (max_side/min_side < 3.5) -> pipa lurus selalu
        JAUH lebih memanjang (ratusan px panjang vs 2-3px tebal => AR > 10).

    Pengaman pipa cabang nyata (nipel pendek, vent, drain, stub tegak lurus):
      * ambang aspect ratio 3.5 MENJAGA stub tipis (w=2,h=14 -> AR 7) tetap hidup,
      * komponen yang LEBIH BESAR dari ambang tidak disentuh (pipa ber-elbow/bercabang
        menyatu dengan jaringan panjang, jadi bagian dari komponen besar),
      * `protect_boxes` (mis. bbox equipment/nozzle) menjaga isi area sensitif,
      * ambang skala mengikuti DPI.

    Mengembalikan citra biner yang sudah dibersihkan (in-place copy).
    """
    if binary is None:
        return binary
    S = dpi / 350.0
    max_side = max(6, int((max_side_pt / 72.0) * dpi))
    max_area = max(12, int((max_area_pt2 / (72.0 ** 2)) * (dpi ** 2)))
    min_area = max(2, int(min_area_px * S * S))

    out = binary.copy()
    ink = (out > 0).astype(np.uint8)
    if cv2.countNonZero(ink) == 0:
        return out

    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    protect = [(int(b[0]), int(b[1]), int(b[2]), int(b[3])) for b in (protect_boxes or [])]

    drop_ids = []
    for i in range(1, n):
        x, y, w, h, area = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                            int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]),
                            int(stats[i, cv2.CC_STAT_AREA]))
        if area < min_area:
            drop_ids.append(i)          # bintik noise mikro
            continue
        if area > max_area or max(w, h) > max_side:
            continue
        ar = max(w, h) / max(1, min(w, h))
        if ar >= max_aspect:
            continue                    # pipa/stub tipis -> jangan buang
        if protect:
            cx, cy = x + w / 2.0, y + h / 2.0
            if any(bx0 <= cx <= bx1 and by0 <= cy <= by1 for bx0, by0, bx1, by1 in protect):
                continue
        drop_ids.append(i)

    if drop_ids:
        drop_lut = np.zeros(n, dtype=bool)
        drop_lut[drop_ids] = True
        out[drop_lut[labels]] = 0

    out[out > 0] = 255
    return out


def suppress_floating_stubs(segs, detections=None, page_wh=None, dpi=350,
                            max_len_px=28, near_margin_pt=10):
    """Filter pasca-tracing (opsional): buang segmen yang SANGAT PENDEK, melayang
    sendirian TANPA menempel ke equipment/valve/instrument mana pun, DAN bergaya
    diagonal (coretan huruf / sisa teks yang lolos).

    Konservatif by design - untuk menjaga cabang nyata (vent/drain/stub tegak lurus):
      * hanya segmen ber-axis 'd' (diagonal) yang dibuang; pipa H/V pendek tetap hidup,
      * panjang <= max_len_px,
      * TIDAK ada ujung/segmen dalam radius near_margin dari bbox deteksi (valve/equip/
        instrument) -> kalau nempel ke simbol, kemungkinan besar itu elemen nyata,
      * kalau `detections` kosong (mis. ROI re-scan), fungsi ini TIDAK membuang apa pun.
    """
    if not segs or not detections:
        return segs
    S = dpi / 350.0
    max_len = max_len_px * S
    near = near_margin_pt * dpi / 72.0
    boxes = [(float(d.get("x1", 0)), float(d.get("y1", 0)),
              float(d.get("x2", 0)), float(d.get("y2", 0))) for d in detections]

    def near_any(px, py):
        for bx0, by0, bx1, by1 in boxes:
            if (bx0 - near) <= px <= (bx1 + near) and (by0 - near) <= py <= (by1 + near):
                return True
        return False

    out = []
    for s in segs:
        if getattr(s, "equipment_outline", False):
            out.append(s)
            continue
        if getattr(s, "axis", "") != "d" or s.length > max_len:
            out.append(s)
            continue
        pts = list(s.points)
        # sample midpoints too, so a run crossing a box is protected
        samples = pts + [((pts[i][0] + pts[i + 1][0]) / 2.0, (pts[i][1] + pts[i + 1][1]) / 2.0)
                         for i in range(len(pts) - 1)]
        if any(near_any(px, py) for px, py in samples):
            out.append(s)
            continue
        # isolated diagonal short stroke -> garbage
        continue
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
        if getattr(s, "equipment_outline", False):
            out.append(s)
            continue
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
        if getattr(s, "equipment_outline", False):
            out.append(s)
            continue
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


def equipment_outline_protect_mask(binary, detections, dpi=350, eq_margin_px=None,
                                   min_span_frac=0.40, dilate_px=2, return_tight=False):
    """Bangun mask guratan TEPI alat (outline tabung/vessel) di dalam kotak deteksi
    `equipment`, supaya langkah 'blackout interior equipment' TIDAK menghapus kontur alat.

    Masalah: masking interior kotak equipment men-nolkan SELURUH isi kotak. Bila kotak
    deteksi meleset (offset) atau vessel digambar sebagai tabung berdinding tipis
    (dome elips + dua dinding), kontur alat itu ikut lenyap -> vessel tak pernah ter-trace.

    Pendekatan (validated pada 605-V-221-B):
      Vessel outline (dome + dinding kiri/kanan + bottom) adalah SATU komponen ink yang
      membentang di KEDUA sumbu box (bbox >= min_span_frac dari lebar & tinggi box).
      Komponen lain di dalam box (panel X, tabel level, teks) bbox-nya kecil di salah
      satu sumbu -> TIDAK dilindungi (tetap dihitamkan). Lindungi FULL STROKE komponen
      outline itu (dilasi tipis), bukan contour-band, karena contour-band bisa terpeleset
      ke perimeter blob gabungan (mis. tabel yang menyatu).

    Return: uint8 mask (255 = lindungi). No-op (semua nol) bila detections kosong.

    Bila `return_tight=True`: kembalikan tuple (mask, tight_boxes) di mana `tight_boxes`
    adalah daftar bbox (x1,y1,x2,y2) yang di-TIGHTEN ke bbox komponen kontur yang
    dilindungi (union per kotak deteksi). Box YOLO sering lebih besar dari alat
    (vessel 605-V-221-B: kotak (1362,846,1921,1657) vs alat asli x≈1393..1663) —
    memakai tight box untuk blackout interior & suppress_equipment_interior
    menghindari clipping pipa nozzle asli yang berada DI LUAR alat tapi DI DALAM
    kotak deteksi (N6A/N6B/N4).
    """
    h, w = binary.shape[:2]
    protect = np.zeros((h, w), dtype=np.uint8)
    eqs = [d for d in (detections or []) if d.get("coarse") == "equipment"]
    if not eqs:
        return (protect, []) if return_tight else protect

    tight_boxes = []
    dil_k = np.ones((2 * max(1, dilate_px) + 1, 2 * max(1, dilate_px) + 1), np.uint8)
    ink = (binary > 0).astype(np.uint8) * 255
    for d in eqs:
        # Kotak deteksi bisa meleset (offset): vessel 605-V-221-B keluar ~95px di kiri
        # dari box equip_big. Perluas area pencarian komponen kontur ke luar kotak
        # (12% tiap sisi) supaya dinding/dome yang tergeser tetap tercakup mask.
        bx1 = int(d.get("x1", 0)); by1 = int(d.get("y1", 0))
        bx2 = int(d.get("x2", 0)); by2 = int(d.get("y2", 0))
        pw = max(1, bx2 - bx1); ph = max(1, by2 - by1)
        mx = int(0.12 * pw); my = int(0.12 * ph)
        ex1 = max(0, bx1 - mx)
        ey1 = max(0, by1 - my)
        ex2 = min(w, bx2 + mx)
        ey2 = min(h, by2 + my)
        if ex2 <= ex1 or ey2 <= ey1:
            continue
        roi = ink[ey1:ey2, ex1:ex2]
        if roi.size == 0:
            continue
        box_w, box_h = ex2 - ex1, ey2 - ey1

        n_lbl, lbl, stats, _ = cv2.connectedComponentsWithStats(roi, 8)
        keep = np.zeros_like(roi)
        ux1, uy1, ux2, uy2 = None, None, None, None
        for ci in range(1, n_lbl):
            cw = stats[ci, cv2.CC_STAT_WIDTH]
            ch = stats[ci, cv2.CC_STAT_HEIGHT]
            area = stats[ci, cv2.CC_STAT_AREA]
            # Kontur alat = komponen besar yang membentang di KEDUA sumbu area,
            # ATAU lengkungan kubah elips (lebar horizontal tapi dangkal vertikal),
            # ATAU dinding silinder vertikal (tinggi vertikal tapi tipis horizontal).
            is_spanning = (cw >= min_span_frac * box_w and ch >= min_span_frac * box_h)
            is_dome_arc = (cw >= 0.40 * box_w and ch >= 0.08 * box_h and area >= 50)
            is_wall_line = (ch >= 0.40 * box_h and cw >= 0.04 * box_w and area >= 50)
            if is_spanning or is_dome_arc or is_wall_line:
                keep[lbl == ci] = 255
                cx1 = stats[ci, cv2.CC_STAT_LEFT]
                cy1 = stats[ci, cv2.CC_STAT_TOP]
                cx2 = cx1 + cw
                cy2 = cy1 + ch
                ux1 = cx1 if ux1 is None else min(ux1, cx1)
                uy1 = cy1 if uy1 is None else min(uy1, cy1)
                ux2 = cx2 if ux2 is None else max(ux2, cx2)
                uy2 = cy2 if uy2 is None else max(uy2, cy2)
        if ux1 is not None:
            # Union bbox komponen outline, dibatasi agar tidak keluar kotak deteksi
            # secara berlebihan (komponen bisa tersambung ke pipa nozzle di luar alat).
            tx1 = max(bx1 - int(0.12 * pw), ex1 + ux1)
            ty1 = max(by1 - int(0.12 * ph), ey1 + uy1)
            tx2 = min(bx2 + int(0.12 * pw), ex1 + ux2)
            ty2 = min(by2 + int(0.12 * ph), ey1 + uy2)
            tight_boxes.append((int(tx1), int(ty1), int(tx2), int(ty2)))
        else:
            tight_boxes.append((bx1, by1, bx2, by2))
        if not keep.any():
            continue
        keep = cv2.dilate(keep, dil_k)
        # Hanya piksel ink nyata yang dilindungi (dilasi tidak menambah piksel hantu).
        keep = cv2.bitwise_and(keep, cv2.dilate(roi, np.ones((3, 3), np.uint8)))
        protect[ey1:ey2, ex1:ex2] = cv2.bitwise_or(protect[ey1:ey2, ex1:ex2], keep)
    return (protect, tight_boxes) if return_tight else protect

def suppress_drawing_margins(segs, page_wh, margin_ratio=0.038, guard_px=15):
    """Buang segmen yang berada di atau sangat dekat dengan margin perimeter kertas luar.
    Garis tepi bingkai gambar (drawing frame border), tick koordinat tepi, dan garis batas
    kertas terluar adalah artifak drafting lembar gambar, BUKAN pipa proses.

    `guard_px` = pita pengaman ABSOLUT (default 15px) di tepi terluar citra: apa pun yang
    seluruhnya berada di dalam pita ini (garis bingkai biru/grid tepi/tick) dibuang tanpa
    memandang rasio, sehingga frame border tak pernah ikut ter-trace walau kertas sangat besar.
    """
    if not segs:
        return []
    W, H = page_wh
    mx = max(int(W * margin_ratio), int(guard_px))
    my = max(int(H * margin_ratio), int(guard_px))
    out = []
    for s in segs:
        if getattr(s, "equipment_outline", False):
            out.append(s)
            continue
        xs = [p[0] for p in s.points]
        ys = [p[1] for p in s.points]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

        # 0. Guard band absolut: segmen SELURUHNYA di dalam pita 15px tepi citra -> frame border.
        if y_max <= guard_px or y_min >= H - guard_px or x_max <= guard_px or x_min >= W - guard_px:
            continue

        # 1. Terletak seluruhnya di dalam pita margin tepi kertas
        if y_max <= my or y_min >= H - my or x_max <= mx or x_min >= W - mx:
            continue

        # 2. Garis panjang yang membentang di dekat perimeter (bingkai tepi gambar)
        if (y_max <= int(my * 1.8) or y_min >= H - int(my * 1.8)) and (x_max - x_min) >= 0.35 * W:
            continue
        if (x_max <= int(mx * 1.8) or x_min >= W - int(mx * 1.8)) and (y_max - y_min) >= 0.35 * H:
            continue

        out.append(s)
    return out


def suppress_page_frame(segs, page_wh, band_ratio=0.06, span_ratio=0.70, min_perimeter_frac=0.9):
    """Buang BINGKAI GAMBAR (drawing frame) yang ke-trace sebagai satu polyline panjang.

    `suppress_drawing_margins` hanya membuang segmen yang SELURUHNYA berada di dalam pita
    tepi. Tapi bingkai gambar khas ter-{chain} menjadi satu polyline yang menelusuri TIGA
    atau EMPAT sisi kertas (kanan->atas->kiri): vertexnya menyentuh dua dimensi (x_max besar,
    y_max besar) sehingga lolos filter margin. Ciri pembeda dari pipa proses sejati:

      * Rentang bbox-nya besar (>= span_ratio dari lebar ATAU tinggi halaman), DAN
      * HAMPIR SEMUA vertex-nya menempel perimeter (tiap titik berada dalam `band_ratio`
        dari salah satu dari 4 tepi halaman). Toleransi `min_perimeter_frac` (default 0.9)
        diberikan karena bingkai kadang menyerap satu-dua spur kecil (artefak tepi) yang
        menyelipkan vertex interior — itu TIDAK boleh menggagalkan deteksi., DAN
      * jumlah vertex >= 3 (pipa lurus panjang hanya 2 titik: ujung-ujungnya 'menempel'
        tepi kiri/kanan halaman namun TIDAK keduanya; frame minimal punya 3 titik sudut).

    Pipa proses yang panjang horizontal punya vertex Tengah di interior halaman -> fraksi
    perimeter kecil -> tetap aman.
    """
    if not segs:
        return []
    W, H = page_wh
    bx = max(8.0, band_ratio * W)
    by = max(8.0, band_ratio * H)
    span_w = span_ratio * W
    span_h = span_ratio * H
    out = []
    for s in segs:
        if getattr(s, "equipment_outline", False):
            out.append(s)
            continue
        pts = s.points
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        if (x_max - x_min) < span_w and (y_max - y_min) < span_h:
            out.append(s)                       # terlalu kecil untuk jadi frame
            continue
        if len(pts) < 3:
            out.append(s)                       # pipa lurus 2 titik -> bukan frame
            continue
        # Fraksi vertex yang menempel salah satu tepi halaman (dalam pita bx/by).
        on_perimeter = 0
        for x, y in pts:
            if (x <= bx) or (x >= W - bx) or (y <= by) or (y >= H - by):
                on_perimeter += 1
        if (on_perimeter / len(pts)) >= min_perimeter_frac:
            continue                            # bingkai gambar -> buang
        out.append(s)
    return out


def suppress_revision_clouds(segs):
    """Buang polyline yang membentuk awan revisi (scalloped revision clouds) atau loop tertutup.
    Pipa proses adalah garis ortogonal (lurus / siku), sedangkan awan revisi berbentuk lengkungan bergelombang."""
    if not segs:
        return []
    out = []
    for s in segs:
        if getattr(s, "equipment_outline", False):
            out.append(s)
            continue
        pts = s.points
        if len(pts) < 5:
            out.append(s)
            continue

        p0, p1 = pts[0], pts[-1]
        disp = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5
        total_len = s.length

        # 1. Closed/semi-closed loop dengan perimeter panjang
        if disp < 60 and total_len > 120:
            continue

        # 2. Tortuosity tinggi (bergelombang rapat / zigzag berkelok)
        if disp > 0 and (total_len / disp) > 2.8:
            continue

        # 3. Proporsi segmen non-ortogonal yang dominan
        non_ortho = 0
        for (xa, ya), (xb, yb) in zip(pts[:-1], pts[1:]):
            dx, dy = abs(xb - xa), abs(yb - ya)
            if dx > 3 and dy > 3 and min(dx, dy) / max(dx, dy) > 0.35:
                non_ortho += 1
        if len(pts) >= 7 and (non_ortho / (len(pts) - 1)) > 0.50:
            continue

        out.append(s)
    return out


def suppress_diagonal_artifacts(segs, page_wh, max_diag_len=300):
    """Buang garis diagonal artifak drafting (bukan pipa proses).
    Pipa proses dalam standar P&ID selalu ortogonal (horizontal/vertikal).
    Garis miring/diagonal yang membentang sangat panjang (> 300px atau > 0.08 dari dimensi lembar)
    atau berada di area margin/title block adalah artifak noise/drawing frame/border."""
    if not segs:
        return []
    W, H = page_wh
    if min(W, H) < 800:
        return segs

    max_len = min(max_diag_len, 0.10 * max(W, H))
    out = []
    for s in segs:
        if getattr(s, "equipment_outline", False):
            out.append(s)
            continue
        pts = s.points
        p0, p1 = pts[0], pts[-1]
        dx = abs(p1[0] - p0[0])
        dy = abs(p1[1] - p0[1])
        is_diagonal = (s.axis == "d") or (dx > 50 and dy > 50 and min(dx, dy) / max(dx, dy) > 0.3)
        if is_diagonal:
            if s.length > max_len or dx > 0.08 * W or dy > 0.08 * H:
                continue
            if min(p0[1], p1[1]) >= 0.85 * H:
                continue
            if min(p0[0], p1[0]) <= 0.05 * W and max(p0[0], p1[0]) >= 0.20 * W:
                continue
        out.append(s)
    return out


def bridge_collinear_headers(segs, max_gap_px=45, tol_px=4, block_boxes=None):
    """Sambungkan segmen pipa kolinear yang terpotong celah kecil (gap di sekitar label/nozzle/header).
    Menjaga kontinuitas pipa panjang seperti HP/LP Production Headers.

    `block_boxes`: daftar bbox yang TIDAK boleh dilewati saat menyambung (mis. bubble
    instrumen — pipa harus putus di instrumen, bukan dijembatani).
    """
    if not segs or len(segs) < 2:
        return segs

    horiz_groups = defaultdict(list)
    vert_groups = defaultdict(list)
    others = []

    def _is_eq(r):
        if hasattr(r, "equipment_outline"):
            return bool(r.equipment_outline)
        return bool(r.get("equipment_outline", False))

    for s in segs:
        pts = s.points if hasattr(s, "points") else s.get("points", [])
        if len(pts) != 2:
            others.append(s)
            continue
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        is_h = abs(y1 - y2) <= tol_px
        is_v = abs(x1 - x2) <= tol_px
        eq_tag = _is_eq(s)
        if is_h and not is_v:
            y_mid = int(round((y1 + y2) / 2.0))
            key = (y_mid // (tol_px + 1), eq_tag)
            horiz_groups[key].append((min(x1, x2), max(x1, x2), y_mid, s))
        elif is_v and not is_h:
            x_mid = int(round((x1 + x2) / 2.0))
            key = (x_mid // (tol_px + 1), eq_tag)
            vert_groups[key].append((min(y1, y2), max(y1, y2), x_mid, s))
        else:
            others.append(s)

    out = list(others)

    def _mk(a, b, axis, base_s, const):
        col = getattr(base_s, "color", "#2563EB")
        lbl = getattr(base_s, "label", "")
        eqo = _is_eq(base_s)
        pts = [(a, const), (b, const)] if axis == "h" else [(const, a), (const, b)]
        return PipeRun(pts, axis=axis, color=col, label=lbl, equipment_outline=eqo)

    # Process horizontal groups
    for key, items in horiz_groups.items():
        items.sort(key=lambda it: it[0])
        curr_min, curr_max, curr_y, base_s = items[0]

        for next_min, next_max, next_y, s in items[1:]:
            if next_min - curr_max <= max_gap_px and not _seg_crosses_boxes(
                    (curr_max, curr_y), (next_min, next_y), block_boxes):
                curr_max = max(curr_max, next_max)
                curr_y = int(round((curr_y + next_y) / 2.0))
            else:
                out.append(_mk(curr_min, curr_max, "h", base_s, curr_y))
                curr_min, curr_max, curr_y, base_s = next_min, next_max, next_y, s
        out.append(_mk(curr_min, curr_max, "h", base_s, curr_y))

    # Process vertical groups
    for key, items in vert_groups.items():
        items.sort(key=lambda it: it[0])
        curr_min, curr_max, curr_x, base_s = items[0]

        for next_min, next_max, next_x, s in items[1:]:
            if next_min - curr_max <= max_gap_px and not _seg_crosses_boxes(
                    (curr_x, curr_max), (next_x, next_min), block_boxes):
                curr_max = max(curr_max, next_max)
                curr_x = int(round((curr_x + next_x) / 2.0))
            else:
                out.append(_mk(curr_min, curr_max, "v", base_s, curr_x))
                curr_min, curr_max, curr_x, base_s = next_min, next_max, next_x, s
        out.append(_mk(curr_min, curr_max, "v", base_s, curr_x))

    return out

def chain_collinear_segments(runs, max_gap_px=15, tol_px=6, angle_tol_deg=12.0,
                             branch_tol_px=10, block_boxes=None):
    """Rantai segmen pipa yang SEGARIS (collinear) dan berjarak sangat dekat
    (gap <= max_gap_px, default 15px) menjadi SATU PipeRun utuh — TAPI hanya bila
    tidak ada percabangan pipa lain di antara keduanya.

    Berbeda dari bridge_collinear_headers (yang hanya menyatukan segmen H/V 2-titik
    dengan celah lebar), fungsi ini:
      * bekerja pada polyline (run H/V/belokan) via TANGENT UJUNG, bukan axis kaku;
      * celah sengaja kecil (<= 15px) -> hanya menambal retakan sisa skeletonisasi;
      * menolak merge bila ada run KETIGA yang menyentuh titik pertemuan (cabang T)
        supaya header bercabang tidak dilipat jadi satu garis.

    Guard anti-cabang: titik tengah celah (junction) diperiksa; bila ujung run lain
    berada dalam radius branch_tol_px dari junction itu, merge dibatalkan.

    Parameters
    ----------
    runs : list[PipeRun]
    max_gap_px : float  celah maksimum antar ujung kolinear yang boleh disatukan.
    tol_px : float      offset lateral maksimum agar dua ujung dianggap segaris.
    angle_tol_deg : float  deviasi arah tangent maksimum (derajat).
    branch_tol_px : float  radius deteksi percabangan di titik sambung.

    Returns
    -------
    list[PipeRun]  daftar run (sebagian sudah digabung), terurut stabil.
    """
    if not runs or len(runs) < 2:
        return runs

    import math as _math

    def _pts(r):
        return r.points if hasattr(r, "points") else r.get("points", [])

    def _ends(r):
        p = _pts(r)
        return (tuple(p[0]), tuple(p[-1])) if p else (None, None)

    def _tangent_at(r, which):
        """Arah KELUAR dari ujung `which` (0 = titik pertama, -1 = titik terakhir).
        Vektor menunjuk KELUAR dari badan run (dari tetangga dalam -> ujung)."""
        p = _pts(r)
        if len(p) < 2:
            return None
        if which == 0:
            # outward at the FIRST vertex points from p[1] towards p[0]
            ax, ay = p[0][0] - p[1][0], p[0][1] - p[1][1]
            ux, uy = p[0][0], p[0][1]
        else:
            # outward at the LAST vertex points from p[-2] towards p[-1]
            ax, ay = p[-1][0] - p[-2][0], p[-1][1] - p[-2][1]
            ux, uy = p[-1][0], p[-1][1]
        L = _math.hypot(ax, ay)
        if L < 1e-6:
            return None
        return (ax / L, ay / L, ux, uy)

    cos_min = _math.cos(_math.radians(angle_tol_deg))

    # Work on a mutable copy; merged results are appended and originals marked consumed.
    work = list(runs)
    consumed = set()
    chains = []  # list of (list_of_run_idx) built in discovery order

    def _is_eq(r):
        if hasattr(r, "equipment_outline"):
            return bool(r.equipment_outline)
        return bool(r.get("equipment_outline", False))

    n = len(work)
    _cg_cell = max(64.0, float(max_gap_px))
    end_grid = defaultdict(list)
    for idx, r in enumerate(work):
        pts = _pts(r)
        if len(pts) >= 2:
            for pt in (pts[0], pts[-1]):
                gx = int(pt[0] // _cg_cell)
                gy = int(pt[1] // _cg_cell)
                end_grid[(gx, gy)].append((idx, pt[0], pt[1]))

    for i in range(n):
        if i in consumed:
            continue
        # Try to grow a chain from run i by repeatedly attaching a collinear neighbour
        # at either end.
        chain = [i]
        consumed.add(i)
        changed = True
        while changed:
            changed = False
            # endpoints of the current chain (first and last run's outward ends)
            head_idx, tail_idx = chain[0], chain[-1]
            head_end = _tangent_at(work[head_idx], 0)
            tail_end = _tangent_at(work[tail_idx], -1)

            # Query candidate js near head_end and tail_end (O(1) candidates via spatial grid)
            cand_j = set()
            for chain_end in (head_end, tail_end):
                if chain_end is None:
                    continue
                cx = int(chain_end[2] // _cg_cell)
                cy = int(chain_end[3] // _cg_cell)
                for dgx in (-1, 0, 1):
                    for dgy in (-1, 0, 1):
                        bucket = end_grid.get((cx + dgx, cy + dgy))
                        if bucket:
                            for item in bucket:
                                if item[0] not in consumed:
                                    cand_j.add(item[0])

            for j in sorted(cand_j):
                if j in consumed:
                    continue
                rj = work[j]
                # Jangan campur run outline alat dengan pipa dalam satu rantai: kontur
                # alat punya bridging tersendiri (bridge_equipment_outline_fragments).
                if _is_eq(work[chain[0]]) != _is_eq(rj):
                    continue
                pj = _pts(rj)
                if len(pj) < 2:
                    continue

                def _fits(chain_end, rj_which):
                    """Can rj end `rj_which` attach to the chain end `chain_end`?
                    Returns the junction midpoint if the two outward tangents are
                    anti-parallel, the gap is small, they are laterally collinear,
                    and no third run branches at the junction."""
                    if chain_end is None:
                        return None
                    rj_t = _tangent_at(rj, rj_which)
                    if rj_t is None:
                        return None
                    cx_, cy_ = chain_end[2], chain_end[3]
                    ex_, ey_ = rj_t[2], rj_t[3]
                    if _math.hypot(cx_ - ex_, cy_ - ey_) > max_gap_px:
                        return None
                    # Jangan jembatani celah yang melewati bubble instrumen.
                    if _seg_crosses_boxes((cx_, cy_), (ex_, ey_), block_boxes):
                        return None
                    # outward tangents must point toward each other (anti-parallel)
                    if chain_end[0] * rj_t[0] + chain_end[1] * rj_t[1] > -cos_min:
                        return None
                    # lateral offset from the chain's end line
                    px, py = ex_ - cx_, ey_ - cy_
                    perp = abs(px * chain_end[1] - py * chain_end[0])
                    if perp > tol_px:
                        return None
                    mx, my = (cx_ + ex_) / 2.0, (cy_ + ey_) / 2.0
                    if _has_branch(work, consumed, j, mx, my, branch_tol_px, end_grid=end_grid, cell_size=_cg_cell):
                        return None
                    return (mx, my)

                # --- attach rj before the chain HEAD ---
                attached = False
                for rj_which in (0, -1):
                    if _fits(head_end, rj_which) is not None:
                        chain.insert(0, j)
                        consumed.add(j)
                        attached = True
                        break
                if attached:
                    changed = True
                    break
                # --- attach rj after the chain TAIL ---
                for rj_which in (0, -1):
                    if _fits(tail_end, rj_which) is not None:
                        chain.append(j)
                        consumed.add(j)
                        attached = True
                        break
                if attached:
                    changed = True
                    break
        chains.append(chain)

    # Build output: keep un-chained runs as-is; merge chains of length >= 2.
    chain_of = {}
    for ci, chain in enumerate(chains):
        for idx in chain:
            chain_of[idx] = ci
    out = []
    emitted = set()
    for idx, r in enumerate(work):
        if idx in emitted:
            continue
        ci = chain_of.get(idx)
        if ci is None:
            out.append(r)
            continue
        chain = chains[ci]
        for c in chain:
            emitted.add(c)
        out.append(_merge_chain(work, chain))
    return out


def _merge_chain(work, chain):
    """Satukan run dalam `chain` (indeks) menjadi satu PipeRun dengan urutan geometris.
    Ujung-ujung yang berhadapan dibiarkan apa adanya (celah dihilangkan dengan menyambung
    titik ujung), titik tengah dipertahankan sehingga belokan tetap utuh."""
    def _pts(r):
        return r.points if hasattr(r, "points") else r.get("points", [])

    # A single-run chain is not a merge at all — return it verbatim (preserving its
    # original axis, e.g. diagonal 'd') so downstream axis checks stay valid.
    if len(chain) == 1:
        return work[chain[0]]

    base = work[chain[0]]
    pts = [tuple(p) for p in _pts(base)]
    for j in chain[1:]:
        q = [tuple(p) for p in _pts(work[j])]
        if not q:
            continue
        # orient q so its first point is nearest to the current tail
        tail = pts[-1]
        d_fwd = (q[0][0] - tail[0]) ** 2 + (q[0][1] - tail[1]) ** 2
        d_rev = (q[-1][0] - tail[0]) ** 2 + (q[-1][1] - tail[1]) ** 2
        if d_rev < d_fwd:
            q = q[::-1]
        # skip the duplicate/overlapping join point, if any
        if abs(q[0][0] - pts[-1][0]) <= 2 and abs(q[0][1] - pts[-1][1]) <= 2:
            q = q[1:]
        pts.extend(q)

    r = base
    color = getattr(r, "color", "#2563EB")
    label = getattr(r, "label", "")
    eq_outline = bool(getattr(r, "equipment_outline", False))
    # The merged result is a real polyline; only keep a simple H/V label when it is
    # exactly straight, otherwise promote to 'poly' (never mislabel a diagonal as H/V).
    if len(pts) == 2:
        dx, dy = abs(pts[0][0] - pts[1][0]), abs(pts[0][1] - pts[1][1])
        axis = "h" if dy <= max(1, dx * 0.2) else "v" if dx <= max(1, dy * 0.2) else "d"
    else:
        axis = "poly"
    return PipeRun(pts, axis=axis, color=color, label=label, equipment_outline=eq_outline)


def _has_branch(work, consumed, exclude_j, mx, my, tol_px, end_grid=None, cell_size=64.0):
    """True bila ada ujung run lain (selain yang sedang disambung) yang berada dalam
    radius tol_px dari titik sambung (mx,my) -> menandakan percabangan T. Merge dibatalkan
    supaya header bercabang tidak dilipat jadi satu garis lurus.
    Mendukung spatial grid lookup O(1) bila end_grid disediakan."""
    r2 = tol_px * tol_px
    if end_grid is not None:
        gx = int(mx // cell_size)
        gy = int(my // cell_size)
        for dgx in (-1, 0, 1):
            for dgy in (-1, 0, 1):
                bucket = end_grid.get((gx + dgx, gy + dgy))
                if not bucket:
                    continue
                for (k, ex, ey) in bucket:
                    if k in consumed or k == exclude_j:
                        continue
                    if (ex - mx) ** 2 + (ey - my) ** 2 <= r2:
                        return True
        return False
    for k, rk in enumerate(work):
        if k in consumed or k == exclude_j:
            continue
        p = rk.points if hasattr(rk, "points") else rk.get("points", [])
        if len(p) < 2:
            continue
        for ex, ey in (p[0], p[-1]):
            if (ex - mx) ** 2 + (ey - my) ** 2 <= r2:
                return True
    return False


def tag_equipment_outlines(runs, detections, page_wh=None, min_inside_frac=0.85,
                           protect_mask=None, min_on_mask_frac=0.5):
    """Tandai run yang merupakan KONTUR alat (vessel/tangki tabung) sebagai `equipment_outline`.

    Dua jalur deteksi (OR):

    A. EVIDENCE-BASED (utama, bila `protect_mask` diberi): run yang >= `min_on_mask_frac`
       panjangnya berada di atas piksel mask outline alat (hasil
       `equipment_outline_protect_mask`) -> PASTI guratan kontur alat. Fragmen kecil pun
       boleh (nanti di-bridge/di-chain).

    B. SHAPE+POSITION (fallback, bila mask tak tersedia): run dengan >= 3 vertex yang
       berbentuk kontur (loop hampir tertutup ATAU bbox 'tabung-like') dan >= 85% titiknya
       di dalam kotak equipment. Garis lurus 2-titik tidak pernah ditandai.

    Men-set `equipment_outline=True` pada PipeRun; run tetap ada di daftar (user bisa
    label/split sendiri) tapi frontend/backend menandainya berbeda dari pipa.
    """
    eqs = [d for d in (detections or []) if d.get("coarse") == "equipment"]
    if not runs or not eqs:
        return runs

    def _on_mask(r):
        if protect_mask is None:
            return False
        pts = r.points if hasattr(r, "points") else r.get("points", [])
        if len(pts) < 2:
            return False
        tot, on = 0.0, 0.0
        for a, b in zip(pts, pts[1:]):
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            tot += d
            if d <= 1e-6:
                continue
            steps = max(2, int(d / 3.0))
            hits = 0
            for k in range(steps + 1):
                x = int(round(a[0] + (b[0] - a[0]) * k / steps))
                y = int(round(a[1] + (b[1] - a[1]) * k / steps))
                if 0 <= y < protect_mask.shape[0] and 0 <= x < protect_mask.shape[1] \
                        and protect_mask[y, x] > 0:
                    hits += 1
            on += d * (hits / (steps + 1))
        return tot > 1e-6 and (on / tot) >= min_on_mask_frac

    boxes = [(float(d.get("x1", 0)), float(d.get("y1", 0)),
              float(d.get("x2", 0)), float(d.get("y2", 0))) for d in eqs]
    # margin kecil saja: titik harus benar-benar di dalam alat.
    pad = 3.0
    result = []
    for r in runs:
        pts = r.points if hasattr(r, "points") else r.get("points", [])
        if len(pts) < 2:
            result.append(r)
            continue

        is_outline = _on_mask(r)

        if not is_outline and protect_mask is None:
            # Fallback shape+position heuristic (no mask available).
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bb_w = max(xs) - min(xs)
            bb_h = max(ys) - min(ys)
            L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
            gap = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
            if len(pts) > 2:
                thin_line = (min(bb_w, bb_h) <= max(6.0, 0.06 * max(bb_w, bb_h))) and len(pts) <= 4
                is_closed = (L > 1e-6 and gap <= 0.15 * L and len(pts) >= 5)
                tube_like = (bb_w >= 24 and bb_h >= 24 and len(pts) >= 3
                             and min(bb_w, bb_h) >= 0.18 * max(bb_w, bb_h))
                if not thin_line and (is_closed or tube_like):
                    best_frac = 0.0
                    for bx1, by1, bx2, by2 in boxes:
                        inside = 0
                        for x, y in pts:
                            if bx1 - pad <= x <= bx2 + pad and by1 - pad <= y <= by2 + pad:
                                inside += 1
                        best_frac = max(best_frac, inside / len(pts))
                    if best_frac >= min_inside_frac:
                        is_outline = True

        if is_outline:
            if hasattr(r, "equipment_outline"):
                r.equipment_outline = True
                result.append(r)
            else:
                nr = dict(r)
                nr["equipment_outline"] = True
                result.append(nr)
        else:
            result.append(r)
    return result

def bridge_piecemeal_gaps(runs, max_gap_px=48, tol_px=8, angle_tol_deg=35.0,
                          short_len_px=40.0, branch_tol_px=8, block_boxes=None):
    """AGRESIF: rantai fragmen pipa pecahan (piecemeal) menjadi satu run utuh.

    Skeletonisasi sering memecah SATU pipa fisik menjadi banyak fragmen pendek (1-2px /
    puluhan px) yang saling berdekatan tapi tidak tersambung graf. Fragmen pendek
    (<= short_len_px) sering gugur karena `min_length`, sehingga pipa panjang HILANG dari
    hasil trace — jauh lebih mahal (user harus menarik ulang) daripada salah sambung yang
    bisa dikoreksi manual.

    Beda dari `chain_collinear_segments` (celah <= 15px, toleransi sudut 12°): di sini
    celah lebih besar (default 48px) dan toleransi sudut lebih longgar (35°), HANYA
    disambung bila salah satu ujung run pendek (fragment). Guard anti-cabang tetap aktif
    supaya T-junction tidak dilipat.
    """
    if not runs or len(runs) < 2:
        return runs

    def _pts(r):
        return r.points if hasattr(r, "points") else r.get("points", [])

    def _len(r):
        p = _pts(r)
        return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(p, p[1:]))

    def _outward(r, which):
        p = _pts(r)
        if len(p) < 2:
            return None
        if which == 0:
            ax, ay = p[0][0] - p[1][0], p[0][1] - p[1][1]
            return (ax, ay, p[0][0], p[0][1])
        ax, ay = p[-1][0] - p[-2][0], p[-1][1] - p[-2][1]
        return (ax, ay, p[-1][0], p[-1][1])

    def _u(v):
        L = math.hypot(v[0], v[1])
        return None if L < 1e-6 else (v[0] / L, v[1] / L)

    cos_min = math.cos(math.radians(angle_tol_deg))
    work = list(runs)
    consumed = set()
    chains = []
    n = len(work)
    _pg_cell = max(64.0, float(max_gap_px))
    end_grid = defaultdict(list)
    for idx, r in enumerate(work):
        pts = _pts(r)
        if len(pts) >= 2:
            for pt in (pts[0], pts[-1]):
                gx = int(pt[0] // _pg_cell)
                gy = int(pt[1] // _pg_cell)
                end_grid[(gx, gy)].append((idx, pt[0], pt[1]))

    def _is_eq(r):
        if hasattr(r, "equipment_outline"):
            return bool(r.equipment_outline)
        return bool(r.get("equipment_outline", False))

    for i in range(n):
        if i in consumed:
            continue
        chain = [i]
        consumed.add(i)
        changed = True
        while changed:
            changed = False
            head = _outward(work[chain[0]], 0)
            tail = _outward(work[chain[-1]], -1)

            # Query candidate js near head and tail (O(1) lookup via spatial grid)
            cand_j = set()
            for chain_end in (head, tail):
                if chain_end is None:
                    continue
                cx = int(chain_end[2] // _pg_cell)
                cy = int(chain_end[3] // _pg_cell)
                for dgx in (-1, 0, 1):
                    for dgy in (-1, 0, 1):
                        bucket = end_grid.get((cx + dgx, cy + dgy))
                        if bucket:
                            for item in bucket:
                                if item[0] not in consumed:
                                    cand_j.add(item[0])

            for j in sorted(cand_j):
                if j in consumed:
                    continue
                rj = work[j]
                # Jangan campur fragmen outline alat dengan pipa: vessel outline punya
                # jembatan khusus (bridge_equipment_outline_fragments). Menyambungnya di
                # sini membuat polyline bolak-balik (outline↔nozzle↔outline).
                if _is_eq(work[chain[0]]) != _is_eq(rj):
                    continue
                pj = _pts(rj)
                if len(pj) < 2:
                    continue
                for chain_end, at_head in ((head, True), (tail, False)):
                    if chain_end is None:
                        continue
                    cu = _u(chain_end)
                    if cu is None:
                        continue
                    attached = False
                    for rj_which in (0, -1):
                        rj_e = _outward(rj, rj_which)
                        if rj_e is None:
                            continue
                        rju = _u(rj_e)
                        if rju is None:
                            continue
                        # gap
                        gap = math.hypot(chain_end[2] - rj_e[2], chain_end[3] - rj_e[3])
                        if gap > max_gap_px:
                            continue
                        # Jangan jembatani celah yang melewati bubble instrumen.
                        if _seg_crosses_boxes((chain_end[2], chain_end[3]),
                                              (rj_e[2], rj_e[3]), block_boxes):
                            continue
                        # at least one side must be a short fragment
                        if _len(work[chain[0]]) > short_len_px and _len(rj) > short_len_px:
                            continue
                        # anti-parallel tangents
                        if cu[0] * rju[0] + cu[1] * rju[1] > -cos_min:
                            continue
                        # lateral collinearity
                        px, py = rj_e[2] - chain_end[2], rj_e[3] - chain_end[3]
                        if abs(px * cu[1] - py * cu[0]) > tol_px:
                            continue
                        mx, my = (chain_end[2] + rj_e[2]) / 2.0, (chain_end[3] + rj_e[3]) / 2.0
                        if _has_branch(work, consumed, j, mx, my, branch_tol_px, end_grid=end_grid, cell_size=_pg_cell):
                            continue
                        if at_head:
                            chain.insert(0, j)
                        else:
                            chain.append(j)
                        consumed.add(j)
                        attached = True
                        changed = True
                        break
                    if attached:
                        break
                if changed:
                    break
        chains.append(chain)

    chain_of = {}
    for ci, chain in enumerate(chains):
        for idx in chain:
            chain_of[idx] = ci
    out = []
    emitted = set()
    for idx, r in enumerate(work):
        if idx in emitted:
            continue
        ci = chain_of.get(idx)
        if ci is None:
            out.append(r)
            continue
        chain = chains[ci]
        for c in chain:
            emitted.add(c)
        out.append(_merge_chain(work, chain))
    return out

def bridge_equipment_outline_fragments(runs, detections=None, max_gap_px=90):
    """Sambung fragmen outline alat (equipment_outline=True) menjadi SATU polyline tertutup.

    Kontur vessel (dome + dinding + bottom + nozzle) sering terpecah skeletonisasi menjadi
    beberapa fragmen; user minta bentuk akhirnya satu polyline tertutup supaya tinggal di-split
    sendiri. Berbeda dari `bridge_piecemeal_gaps`, di sini celah LEBIH BESAR diizinkan karena
    SEMUA fragmen sudah pasti bagian kontur alat yang sama (bukti: berada di atas protect_mask).
    Sambung greedy: pasangan fragmen outline dengan ujung terdekat selalu disatukan.

    PENTING: hanya fragmen dalam KOTAK EQUIPMENT YANG SAMA yang boleh disambung. Tanpa
    batasan ini, fragmen vessel bisa ter-bridge ke fragmen alat lain yang jauh (false merge).

    Hanya menyentuh run `equipment_outline=True`; run pipa tidak berubah.
    """
    if not runs or len(runs) < 2:
        return runs

    def _is_eq(r):
        if hasattr(r, "equipment_outline"):
            return bool(r.equipment_outline)
        return bool(r.get("equipment_outline", False))

    def _pts(r):
        return r.points if hasattr(r, "points") else r.get("points", [])

    # Kelompokkan fragmen outline per kotak equipment (berdasarkan centroid). Fragmen
    # yang tidak masuk kotak mana pun -> kelompok sendiri (tidak disambung lintas box).
    boxes = []
    for d in (detections or []):
        if d.get("coarse") == "equipment":
            boxes.append((float(d.get("x1", 0)), float(d.get("y1", 0)),
                          float(d.get("x2", 0)), float(d.get("y2", 0))))

    def _box_of(pts):
        if not boxes:
            return 0
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        for bi, (bx1, by1, bx2, by2) in enumerate(boxes):
            pad = 0.12 * max(bx2 - bx1, by2 - by1)
            if bx1 - pad <= cx <= bx2 + pad and by1 - pad <= cy <= by2 + pad:
                return bi + 1
        return 0

    # Work list per group: {group_key: [[points, original_index], ...]}
    groups = defaultdict(list)
    for i, r in enumerate(runs):
        if _is_eq(r):
            p = _pts(r)
            if len(p) >= 2:
                pl = [(float(x), float(y)) for x, y in p]
                groups[_box_of(pl)].append([pl, i])

    merged_by_first = {}
    drop_idx = set()
    for gkey, work in groups.items():
        if len(work) < 2:
            if work:
                merged_by_first[work[0][1]] = work[0][0]
            continue
        changed = True
        while changed:
            changed = False
            best = None
            for a in range(len(work)):
                for b in range(a + 1, len(work)):
                    pa, pb = work[a][0], work[b][0]
                    for oa in (0, 1):
                        for ob in (0, 1):
                            qa = pa[0] if oa == 0 else pa[-1]
                            qb = pb[0] if ob == 0 else pb[-1]
                            d = math.hypot(qa[0] - qb[0], qa[1] - qb[1])
                            if d <= max_gap_px and (best is None or d < best[0]):
                                best = (d, a, b, oa, ob)
            if best is None:
                break
            _, a, b, oa, ob = best
            pa, pb = list(work[a][0]), list(work[b][0])
            # `oa`/`ob` = ujung yang dipilih untuk disambung (0 = titik awal, 1 = titik akhir).
            # Agar ujung terpilih menjadi TAIL dari pa dan HEAD dari pb (pa + pb),
            # pa dibalik bila ujung terpilih adalah titik AWAL, dan pb dibalik bila titik AKHIR.
            if oa == 0:
                pa = pa[::-1]
            if ob == 1:
                pb = pb[::-1]
            merged = pa + pb
            cleaned = [merged[0]]
            for q in merged[1:]:
                if abs(q[0] - cleaned[-1][0]) <= 2 and abs(q[1] - cleaned[-1][1]) <= 2:
                    continue
                cleaned.append(q)
            work[a][0] = cleaned
            work.pop(b)
            changed = True
        # Setiap entri `work` yang tersisa adalah satu chain final (hasil merge). Simpan
        # chain pada indeks fragmen pertamanya; fragmen lain yang sudah di-merge ke dalam
        # chain ini otomatis hilang dari `work` sehingga akan di-drop oleh sapuan di bawah.
        # JANGAN drop work[1:] — mereka adalah chain terpisah yang gagal merge (gap > max_gap)
        # dan HARUS tetap ada (bug: vessel outline hilang total karena ini).
        for pts, idx in work:
            merged_by_first[idx] = pts

    # Fragmen outline yang tidak menjadi 'first' chain mana pun = sudah di-merge ke chain
    # lain -> drop. Fragmen yang tetap berdiri sendiri tetap dipertahankan.
    for i, r in enumerate(runs):
        if _is_eq(r) and i not in merged_by_first:
            drop_idx.add(i)

    out = []
    for i, r in enumerate(runs):
        if i in drop_idx:
            continue
        if i in merged_by_first:
            pts = merged_by_first[i]
            color = getattr(r, "color", "#2563EB") if hasattr(r, "color") else r.get("color", "#2563EB")
            if hasattr(r, "points"):
                out.append(PipeRun([(int(x), int(y)) for x, y in pts], axis="poly",
                                   color=color, equipment_outline=True))
            else:
                nr = dict(r)
                nr["points"] = [[int(x), int(y)] for x, y in pts]
                nr["axis"] = "poly"
                nr["equipment_outline"] = True
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                nr["x1"], nr["y1"], nr["x2"], nr["y2"] = min(xs), min(ys), max(xs), max(ys)
                out.append(nr)
            continue
        out.append(r)
    return out

def bridge_inline_valve_gaps(runs, detections=None, max_gap_px=115, tol_px=10,
                            angle_tol_deg=20.0, containment_margin_px=12, block_boxes=None):
    """Sambungkan pipa lurus yang terpotong oleh katup inline (valve) atau celah kecil.
    Menghubungkan dua PipeRun terpisah melewati gap katup menjadi satu polyline bersambung.

    Dua mode sambung:
      1. CONTAINMENT (paling andal): bila kedua ujung pipa yang berhadapan sama-sama menyentuh /
         berada di dalam bounding box valve yang SAMA, sambungkan tanpa memandang deviasi sudut
         (valve besar/bulbous sering memutus pipa dengan gap diagonal).
      2. KOLINEAR: bila kedua segmen searah H/V dan deviasi sudut <= `angle_tol_deg`, toleransi
         posisi diperlonggar (`tol_px`) dan gap maksimum dinaikkan (`max_gap_px`).
    """
    if not runs or len(runs) < 2:
        return runs

    valves = [d for d in (detections or []) if d.get("coarse") == "valve"]
    if not valves:
        return runs

    def _is_eq(r):
        if hasattr(r, "equipment_outline"):
            return bool(r.equipment_outline)
        return bool(r.get("equipment_outline", False))

    valve_boxes = [(float(v["x1"]), float(v["y1"]), float(v["x2"]), float(v["y2"])) for v in valves]

    _vg_cell = max(128.0, float(max_gap_px))
    valve_grid = defaultdict(list)
    for idx, (vx1, vy1, vx2, vy2) in enumerate(valve_boxes):
        min_gx = int((vx1 - max_gap_px) // _vg_cell)
        max_gx = int((vx2 + max_gap_px) // _vg_cell)
        min_gy = int((vy1 - max_gap_px) // _vg_cell)
        max_gy = int((vy2 + max_gap_px) // _vg_cell)
        for gx in range(min_gx, max_gx + 1):
            for gy in range(min_gy, max_gy + 1):
                valve_grid[(gx, gy)].append(idx)

    def near_any_valve(pt):
        px, py = pt
        gx = int(px // _vg_cell)
        gy = int(py // _vg_cell)
        cand = valve_grid.get((gx, gy))
        if not cand:
            return False
        return any(
            (valve_boxes[idx][0] - max_gap_px <= px <= valve_boxes[idx][2] + max_gap_px) and
            (valve_boxes[idx][1] - max_gap_px <= py <= valve_boxes[idx][3] + max_gap_px)
            for idx in cand
        )

    def valve_containing(pt):
        """Kembalikan index bbox valve yang memuat `pt` (dengan margin), atau None."""
        px, py = pt
        gx = int(px // _vg_cell)
        gy = int(py // _vg_cell)
        cand = valve_grid.get((gx, gy))
        if not cand:
            return None
        for idx in cand:
            vx1, vy1, vx2, vy2 = valve_boxes[idx]
            if (vx1 - containment_margin_px <= px <= vx2 + containment_margin_px) and \
               (vy1 - containment_margin_px <= py <= vy2 + containment_margin_px):
                return idx
        return None

    cos_tol = math.cos(math.radians(angle_tol_deg))

    out = list(runs)
    merged = True
    while merged:
        merged = False
        n = len(out)
        # Spatial Grid untuk endpoint kandidat run
        end_grid = defaultdict(set)
        for idx, r in enumerate(out):
            pts = r.points if hasattr(r, "points") else r.get("points", [])
            if len(pts) >= 2:
                for pt in (pts[0], pts[-1]):
                    gx = int(pt[0] // _vg_cell)
                    gy = int(pt[1] // _vg_cell)
                    end_grid[(gx, gy)].add(idx)

        for i in range(n):
            if merged:
                break
            r1 = out[i]
            pts1 = r1.points if hasattr(r1, "points") else r1.get("points", [])
            if len(pts1) < 2 or not (near_any_valve(pts1[0]) or near_any_valve(pts1[-1])):
                continue

            # Ambil hanya kandidat j yang berada di sel sekitar endpoint r1
            cand_j = set()
            for pt in (pts1[0], pts1[-1]):
                gx = int(pt[0] // _vg_cell)
                gy = int(pt[1] // _vg_cell)
                for dgx in (-1, 0, 1):
                    for dgy in (-1, 0, 1):
                        cand_j.update(end_grid.get((gx + dgx, gy + dgy), ()))

            for j in sorted(cand_j):
                if j <= i or j >= n:
                    continue
                if merged:
                    break
                r2 = out[j]
                pts2 = r2.points if hasattr(r2, "points") else r2.get("points", [])
                if len(pts2) < 2:
                    continue
                # Jangan campur run outline alat dengan pipa: kontur alat punya bridging
                # tersendiri. Tanpa guard ini, fragmen outline yang menempel di ujung pipa
                # (mis. skirt vessel dekat valve N3) terserap ke run pipa dan KEHILANGAN
                # flag equipment_outline (akar spaghetti run-32).
                if _is_eq(r1) != _is_eq(r2):
                    continue

                pairs = [
                    (pts1[-1], pts2[0], False, False, pts1[-2], pts2[1]),   # r1 -> r2
                    (pts2[-1], pts1[0], True, False, pts2[-2], pts1[1]),    # r2 -> r1
                    (pts1[0], pts2[0], False, True, pts1[1], pts2[1]),      # rev(r1) -> r2
                    (pts1[-1], pts2[-1], False, True, pts1[-2], pts2[-2]),  # r1 -> rev(r2)
                ]

                for pA, pB, swap, rev2, prevA, nextB in pairs:
                    dx = abs(pA[0] - pB[0])
                    dy = abs(pA[1] - pB[1])
                    dist = math.hypot(dx, dy)

                    if not (0 < dist <= max_gap_px):
                        continue

                    # Jangan jembatani celah yang melewati bubble instrumen.
                    if _seg_crosses_boxes(pA, pB, block_boxes):
                        continue

                    # Tangent check: both incoming segment (prevA -> pA) and outgoing (pB -> nextB) must be collinear
                    vA = (pA[0] - prevA[0], pA[1] - prevA[1])
                    vB = (nextB[0] - pB[0], nextB[1] - pB[1])
                    lenA = math.hypot(*vA)
                    lenB = math.hypot(*vB)

                    is_hA = abs(vA[0]) >= 1.5 * abs(vA[1])
                    is_hB = abs(vB[0]) >= 1.5 * abs(vB[1])
                    is_vA = abs(vA[1]) >= 1.5 * abs(vA[0])
                    is_vB = abs(vB[1]) >= 1.5 * abs(vB[0])

                    is_h = is_hA and is_hB and dy <= tol_px and dx > 0
                    is_v = is_vA and is_vB and dx <= tol_px and dy > 0

                    # Loosened collinearity: angle between the two tangents <= angle_tol_deg
                    collinear = False
                    if lenA > 1e-6 and lenB > 1e-6 and (is_hA == is_hB and is_vA == is_vB):
                        cos_ang = abs((vA[0] * vB[0] + vA[1] * vB[1]) / (lenA * lenB))
                        collinear = cos_ang >= cos_tol
                        if collinear:
                            # Lateral-offset guard: tangents searah saja TIDAK cukup — dua
                            # pipa paralel yang bergeser puluhan px (dinding vessel x=1394
                            # vs panel-X x=1450) tidak boleh disambung; tanpa guard ini
                            # terbentuk polyline zigzag bolak-balik.
                            if lenA >= lenB:
                                ux, uy = vA[0] / lenA, vA[1] / lenA
                            else:
                                ux, uy = vB[0] / lenB, vB[1] / lenB
                            lateral = abs((pB[0] - pA[0]) * uy - (pB[1] - pA[1]) * ux)
                            if lateral > tol_px:
                                collinear = False

                    # Containment: both facing endpoints sit inside the SAME valve bbox
                    contained = False
                    va, vb = valve_containing(pA), valve_containing(pB)
                    if va is not None and va == vb:
                        contained = True
                        # Directional continuity: walau di dalam valve yang sama, kedua ujung
                        # harus saling berhadapan SEPANJANG arah pipa. Tanpa cek ini, baris
                        # pipa paralel yang kebetulan sama-sama menyentuh bbox valve besar
                        # (N8A/N8B/N7B di vessel) terlipat jadi satu run zigzag.
                        conn = (pB[0] - pA[0], pB[1] - pA[1])
                        cl = math.hypot(conn[0], conn[1])
                        if cl > 1e-6 and lenA > 1e-6 and lenB > 1e-6:
                            ux, uy = conn[0] / cl, conn[1] / cl
                            cosA = (ux * vA[0] + uy * vA[1]) / lenA
                            cosB = (ux * vB[0] + uy * vB[1]) / lenB
                            if cosA < math.cos(math.radians(40)) or cosB < math.cos(math.radians(40)):
                                contained = False

                    if is_h or is_v or collinear or contained:
                        first_pts = list(pts2 if swap else pts1)
                        second_pts = list(pts1 if swap else pts2)

                        if pA == (pts1[0] if not swap else pts2[0]):
                            first_pts = first_pts[::-1]
                        if rev2:
                            second_pts = second_pts[::-1]

                        combined_pts = first_pts + second_pts
                        clean_pts = [(int(p[0]), int(p[1])) for p in combined_pts]

                        base_r = r1 if not swap else r2
                        axis = getattr(base_r, "axis", "poly") if hasattr(base_r, "axis") else base_r.get("axis", "poly")
                        color = getattr(base_r, "color", "#2563EB") if hasattr(base_r, "color") else base_r.get("color", "#2563EB")
                        label = getattr(base_r, "label", "") if hasattr(base_r, "label") else base_r.get("label", "")
                        eqo = bool(getattr(base_r, "equipment_outline", False)) if hasattr(base_r, "equipment_outline") \
                            else bool(base_r.get("equipment_outline", False))

                        merged_run = PipeRun(points=clean_pts, axis=axis, color=color, label=label,
                                             equipment_outline=eqo)

                        out.pop(j)
                        out.pop(i)
                        out.append(merged_run)
                        merged = True
                        break

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

    # Drafting suppressions & header continuity
    segs = suppress_drawing_margins(segs, page_wh=(img_bgr.shape[1], img_bgr.shape[0]))
    segs = suppress_page_frame(segs, page_wh=(img_bgr.shape[1], img_bgr.shape[0]))
    segs = suppress_revision_clouds(segs)
    segs = suppress_diagonal_artifacts(segs, page_wh=(img_bgr.shape[1], img_bgr.shape[0]))
    segs = bridge_collinear_headers(segs)

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
