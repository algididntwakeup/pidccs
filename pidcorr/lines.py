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
import os
import threading
import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from collections import defaultdict, OrderedDict
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
    line_style: str = "solid"                  # Display/export line style
    id: str = ""                              # Unique run identifier (e.g. run-0)
    label: str = ""                           # Custom tag or line label
    manual: bool = False                      # True if manually created/edited by engineer
    marked: bool = False                      # True after engineer opts into marking
    group_id: str = ""                         # Legacy circuit group membership
    system_group_id: str = ""                  # Manual corrosion system membership
    circuit_group_id: str = ""                 # Manual corrosion circuit membership
    equipment_outline: bool = False           # True if run is equipment outline (default False)

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
        if key == "line_style": return getattr(self, "line_style", "solid")
        if key == "marked": return getattr(self, "marked", False)
        if key == "group_id": return getattr(self, "group_id", "")
        if key == "system_group_id": return getattr(self, "system_group_id", "")
        if key == "circuit_group_id": return getattr(self, "circuit_group_id", "")
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


def split_runs_at_t_junctions(runs, tol_px: float = 2.0, min_piece_px: float = 6.0):
    """Split straight H/V runs where a perpendicular branch terminates.

    A through-run can remain continuous for topology, but the marking editor needs
    independent legs ending at a T so users can opt into each leg separately.
    A perpendicular line crossing the main line has no endpoint at the crossing
    and therefore does not cause a split.
    """
    if not runs or len(runs) < 2:
        return runs

    def axis_of(run):
        return run.axis if hasattr(run, "axis") else run.get("axis", "poly")

    def points_of(run):
        return run.points if hasattr(run, "points") else run.get("points", [])

    cuts_by_run = defaultdict(list)
    branch_endpoints = {"h": [], "v": []}
    for run_idx, run in enumerate(runs):
        axis = axis_of(run)
        points = points_of(run)
        if axis not in ("h", "v") or len(points) < 2:
            continue
        for endpoint, far_endpoint in ((points[0], points[-1]),
                                       (points[-1], points[0])):
            off = endpoint[1] if axis == "h" else endpoint[0]
            along = endpoint[0] if axis == "h" else endpoint[1]
            far_off = far_endpoint[1] if axis == "h" else far_endpoint[0]
            branch_endpoints[axis].append((off, along, far_off))
    for axis in branch_endpoints:
        branch_endpoints[axis].sort(key=lambda item: item[0])
    endpoint_offsets = {
        axis: [item[0] for item in items]
        for axis, items in branch_endpoints.items()
    }

    for main_idx, main in enumerate(runs):
        main_axis = axis_of(main)
        points = points_of(main)
        if main_axis not in ("h", "v") or len(points) < 2:
            continue
        (x0, y0), (x1, y1) = points[0], points[-1]
        cross = (y0 + y1) / 2 if main_axis == "h" else (x0 + x1) / 2
        lo, hi = sorted((x0, x1) if main_axis == "h" else (y0, y1))
        branch_axis = "v" if main_axis == "h" else "h"
        candidates = branch_endpoints[branch_axis]
        offsets = endpoint_offsets[branch_axis]
        first = bisect_left(offsets, cross - tol_px)
        last = bisect_right(offsets, cross + tol_px)
        for off, along, far_off in candidates[first:last]:
            if (lo + min_piece_px <= along <= hi - min_piece_px
                    and abs(far_off - cross) > tol_px):
                cuts_by_run[main_idx].append(float(along))

    output = []
    for run_idx, run in enumerate(runs):
        points = points_of(run)
        axis = axis_of(run)
        cuts = cuts_by_run.get(run_idx, [])
        if not cuts:
            output.append(run)
            continue
        (x0, y0), (x1, y1) = points[0], points[-1]
        lo, hi = sorted((x0, x1) if axis == "h" else (y0, y1))
        unique_cuts = []
        for cut in sorted(cuts):
            if lo + min_piece_px <= cut <= hi - min_piece_px and (
                not unique_cuts or cut - unique_cuts[-1] > tol_px
            ):
                unique_cuts.append(cut)
        boundaries = [lo, *unique_cuts, hi]
        for start, end in zip(boundaries, boundaries[1:]):
            if end - start < min_piece_px:
                continue
            piece_points = ([(start, (y0 + y1) / 2), (end, (y0 + y1) / 2)]
                            if axis == "h"
                            else [((x0 + x1) / 2, start), ((x0 + x1) / 2, end)])
            if isinstance(run, PipeRun):
                output.append(PipeRun(
                    points=piece_points, axis=axis, pid=run.pid, fluid=run.fluid,
                    underline=run.underline, color=run.color, line_style=run.line_style,
                    label=run.label, manual=run.manual, marked=run.marked,
                    group_id=run.group_id, system_group_id=run.system_group_id,
                    circuit_group_id=run.circuit_group_id,
                    equipment_outline=run.equipment_outline,
                ))
            else:
                piece = dict(run)
                piece["points"] = piece_points
                piece["axis"] = axis
                output.append(piece)
    return output


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
MAX_EQUIP_AREA_FRAC = 0.95


def suppress_equipment_interior(segs, detections, dpi=350, margin_pt=3, page_wh=None):
    """Equipment TIDAK di-trace jadi pipa SAMA SEKALI (permintaan penulis): tiap segmen H/V
    di-CLIP di batas bbox equipment — bagian DI DALAM equipment dibuang, bagian di LUAR
    (pipa asli menuju nozzle) dipertahankan. Beda dari versi lama yg cuma buang segmen yg
    KEDUA ujungnya di dalam (pipa masuk equipment jadi masih ke-trace ke dalam).
    Efek: pipa berhenti di tepi equipment. Bila sebuah piping ID kehilangan pipanya karena
    ini -> otomatis jadi 'pid_none' di panel Review (user yang memutuskan).

    POLYLINE-AWARE: clipping dievaluasi PER SEGMEN (`zip(pts, pts[1:])`), bukan dari
    titik ujung polyline. Versi lama memakai `points[0]` dan `points[-1]` sehingga
    sebuah polyline bengkok (header horizontal yang menikung turun) diklasifikasikan
    "horizontal" berdasarkan selisih ujungnya, lalu dipotong di `y` rata-rata —
    koordinat yang tidak dilewati pipa sama sekali. Akibatnya header panjang +
    turunan vertikalnya hilang total (terukur: 2 pipa utama lenyap di lembar
    referensi, ink coverage 1.00 -> 0.00 pada kedua pipa).
    """
    eqs = [d for d in (detections or []) if d.get("coarse") == "equipment"]
    if not eqs:
        return segs
    m = margin_pt * dpi / 72.0
    boxes = [(d["x1"] + m, d["y1"] + m, d["x2"] - m, d["y2"] - m) for d in eqs]
    boxes = [b for b in boxes if b[0] < b[2] and b[1] < b[3]]
    keep = 12 * dpi / 72.0
    out = []
    for s in segs:
        pts = s.points
        if len(pts) < 2:
            continue
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if abs(bx - ax) >= abs(by - ay):            # segmen horizontal
                y = (ay + by) / 2.0; lo, hi = sorted((ax, bx))
                ins = [(max(bx0, lo), min(bx1, hi)) for bx0, by0, bx1, by1 in boxes
                       if by0 <= y <= by1 and max(bx0, lo) < min(bx1, hi)]
                if not ins:
                    out.append(PipeRun([(ax, ay), (bx, by)], s.axis))
                else:
                    for a, b in _subtract_intervals(lo, hi, ins, keep):
                        out.append(PipeRun([(a, y), (b, y)], s.axis))
            else:                                       # segmen vertikal
                x = (ax + bx) / 2.0; lo, hi = sorted((ay, by))
                ins = [(max(by0, lo), min(by1, hi)) for bx0, by0, bx1, by1 in boxes
                       if bx0 <= x <= bx1 and max(by0, lo) < min(by1, hi)]
                if not ins:
                    out.append(PipeRun([(ax, ay), (bx, by)], s.axis))
                else:
                    for a, b in _subtract_intervals(lo, hi, ins, keep):
                        out.append(PipeRun([(x, a), (x, b)], s.axis))
    return out


def snap_endpoints_to_equipment(runs, detections, snap_radius_pt=14, dpi=350):
    """Proyeksikan ujung garis pipa (endpoints) yang berakhir dekat perimeter
    bounding box equipment agar menempel persis ke dinding alat (nozzle connection),
    bukan mengambang di luar kotak. (Phase B.5)"""
    if not runs or not detections:
        return runs
    eqs = [d for d in detections if d.get("coarse") == "equipment"]
    if not eqs:
        return runs

    snap_radius_px = snap_radius_pt * dpi / 72.0
    boxes = [(float(d["x1"]), float(d["y1"]), float(d["x2"]), float(d["y2"])) for d in eqs]

    out = []
    for r in runs:
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

            best_snap = None
            min_d = float("inf")

            for bx1, by1, bx2, by2 in boxes:
                cx = max(bx1, min(px, bx2))
                cy = max(by1, min(py, by2))

                # If endpoint is slightly inside the box
                if bx1 <= px <= bx2 and by1 <= py <= by2:
                    dl, dr = px - bx1, bx2 - px
                    dt, db = py - by1, by2 - py
                    m = min(dl, dr, dt, db)
                    if m < min_d:
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

    def _make_run(base_run, new_pts, suffix=""):
        clean_pts = [(int(p[0]), int(p[1])) for p in new_pts]
        base_id = getattr(base_run, "id", "") if hasattr(base_run, "points") else base_run.get("id", "")
        run_id = f"{base_id}-{suffix}" if base_id and suffix else base_id
        base_label = getattr(base_run, "label", getattr(base_run, "pid", "")) if hasattr(base_run, "points") else base_run.get("label", base_run.get("pid", ""))
        color = getattr(base_run, "color", "#2563EB") if hasattr(base_run, "points") else base_run.get("color", "#2563EB")
        if hasattr(base_run, "points"):
            return PipeRun(
                points=clean_pts,
                axis=getattr(base_run, "axis", "poly"),
                pid=getattr(base_run, "pid", ""),
                fluid=getattr(base_run, "fluid", ""),
                underline=getattr(base_run, "underline", False),
                color=color,
                line_style=getattr(base_run, "line_style", "solid"),
                id=run_id,
                label=base_label or "",
                manual=True,
                marked=getattr(base_run, "marked", False),
                group_id=getattr(base_run, "group_id", ""),
                system_group_id=getattr(base_run, "system_group_id", ""),
                circuit_group_id=getattr(base_run, "circuit_group_id", ""),
            )
        else:
            r = dict(base_run)
            r["points"] = [[int(p[0]), int(p[1])] for p in clean_pts]
            r["x1"] = min(p[0] for p in clean_pts)
            r["y1"] = min(p[1] for p in clean_pts)
            r["x2"] = max(p[0] for p in clean_pts)
            r["y2"] = max(p[1] for p in clean_pts)
            r["color"] = color
            r["id"] = run_id
            r["label"] = base_label or ""
            r["manual"] = True
            return r

    run_a = _make_run(target, pts_a, "a")
    run_b = _make_run(target, pts_b, "b")

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
                           max_side_pt=10.0, max_area_pt2=40.0, max_aspect=4.0,
                           min_area_px=8, protect_boxes=None):
    """Pra-skeletisasi: hitamkan (blackout) pulau piksel yang secara geometri menyerupai
    GLYPH TEKS / coretan kecil yang lolos dari masking OCR.

    Latar belakang: OCR hanya mengembalikan string utuh yang terdeteksinya; satu karakter
    yang gagal dikenali (mis. pecahan ukuran '3/4', kata 'BY INSTR', atau coretan huruf)
    tetap menjadi piksel putih yang lalu di-skeletonize menjadi garis liar (garbage trace).
    Filter ini bekerja pada citra biner (sebelum skeletisasi/morphological skeleton)
    memakai analisis komponen terhubung (`connectedComponentsWithStats`) dan melenyapkan
    pulau dengan profil geometri karakter tipikal P&ID:

      * ukuran sisi pendek (<= 10pt @ 350dpi, i.e. ~48-50px pada 200dpi) -> bukan pipa,
      * area piksel kecil (8..40pt^2) -> bukan header/perimeter equipment,
      * aspect ratio tidak memanjang ekstrem (max_side/min_side < 4.0) -> pipa lurus selalu
        JAUH lebih memanjang (ratusan px panjang vs 2-3px tebal => AR > 10).

    Pengaman pipa cabang nyata (nipel pendek, vent, drain, stub tegak lurus):
      * ambang aspect ratio 4.0 MENJAGA stub tipis (w=2,h=12 -> AR 6) tetap hidup,
      * komponen yang LEBIH BESAR dari ambang tidak disentuh (pipa ber-elbow/bercabang
        menyatu dengan jaringan panjang, jadi bagian dari komponen besar),
      * `protect_boxes` (mis. bbox equipment/nozzle) menjaga isi area sensitif,
      * ambang skala mengikuti DPI.

    Mengembalikan citra biner yang sudah dibersihkan (in-place copy).
    """
    if binary is None:
        return binary
    max_side = max(6, int((max_side_pt / 72.0) * dpi))
    max_area = max(12, int((max_area_pt2 / (72.0 ** 2)) * (dpi ** 2)))

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
        if area < min_area_px:
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
        drop_mask = np.isin(labels, drop_ids)
        out[drop_mask] = 0

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


def suppress_box_outlines(segs, boxes, dpi=350, band_pt=7, detections=None):
    """Buang segmen yang BERIMPIT tepi kotak (dari detect_boxes) — garis outline kotak,
    BUKAN pipa.

    POLYLINE-AWARE + GUARD (bug yang diperbaiki 2026-09-24):
    Versi lama menilai sebuah run dari DUA TITIK UJUNG saja
    (``points[0]`` dan ``points[-1]``). Untuk polyline bengkok itu salah dua kali:
      1. run yang menikung bisa tampak "sejajar tepi kotak" padahal badan
         polyline-nya sama sekali bukan garis kotak;
      2. begitu satu sisi polyline dinilai menempel tepi, SELURUH run dibuang —
         termasuk sisi lain yang pipa asli.
    Terukur pada lembar referensi: pipa ``605-2"-GR-CSA-077`` (277 px) dan pipa 2"
    dengan vlinderklep + cabang 3/4" (240 px) lenyap total (coverage 1.00 -> 0.00).

    Sekarang penilaian dilakukan PER SEGMEN (``zip(pts, pts[1:])``) dan hanya
    segmen yang benar-benar berimpit tepi kotak yang dibuang; sisa polyline
    dipertahankan sebagai run terpisah.

    Tambahan GUARD: segmen yang MENYENTUH simbol terdeteksi (valve/instrument/
    equipment) TIDAK PERNAH dibuang — pipa yang tersambung ke vlinderklep tidak
    mungkin garis outline kotak. Tanpa guard ini, pipa yang kebetulan sejajar tepi
    kotak detektor ikut terbuang.
    """
    if not boxes:
        return segs
    b = band_pt * dpi / 72.0

    # Kotak pelindung dari simbol terdeteksi (valve/instrument/equipment).
    guards = []
    for d in (detections or []):
        if d.get("coarse") in ("equipment", "valve", "instrument"):
            gx0, gy0 = float(d.get("x1", 0)), float(d.get("y1", 0))
            gx1, gy1 = float(d.get("x2", 0)), float(d.get("y2", 0))
            if gx1 > gx0 and gy1 > gy0:
                guards.append((gx0 - b, gy0 - b, gx1 + b, gy1 + b))

    def _touches_guard(ax, ay, bx, by):
        sx0, sy0 = min(ax, bx), min(ay, by)
        sx1, sy1 = max(ax, bx), max(ay, by)
        for gx0, gy0, gx1, gy1 in guards:
            if sx1 >= gx0 and sx0 <= gx1 and sy1 >= gy0 and sy0 <= gy1:
                return True
        return False

    def _on_box_edge(ax, ay, bx, by):
        """Apakah segmen tunggal ini berimpit tepi salah satu kotak?"""
        horiz = abs(by - ay) <= b
        vert = abs(bx - ax) <= b
        if not (horiz or vert):
            return False
        for bx0, by0, bx1, by1 in boxes:
            if horiz:
                ym = (ay + by) / 2
                if ((abs(ym - by0) <= b or abs(ym - by1) <= b)
                        and min(ax, bx) >= bx0 - b and max(ax, bx) <= bx1 + b):
                    return True
            if vert:
                xm = (ax + bx) / 2
                if ((abs(xm - bx0) <= b or abs(xm - bx1) <= b)
                        and min(ay, by) >= by0 - b and max(ay, by) <= by1 + b):
                    return True
        return False

    out = []
    for s in segs:
        pts = [tuple(p) for p in s.points]
        if len(pts) < 2:
            continue
        # Cari bagian (segmen) yang TIDAK berimpit tepi kotak, lalu rakit ulang
        # menjadi polyline. Pipa yang menembus kotak akan tetap utuh; outline
        # kotak yang benar-benar sejajar tepi tetap terbuang.
        keep_runs = []
        cur = [pts[0]]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if _on_box_edge(ax, ay, bx, by) and not _touches_guard(ax, ay, bx, by):
                if len(cur) >= 2:
                    keep_runs.append(cur)
                cur = [(bx, by)]
            else:
                if cur[-1] != (ax, ay):
                    cur.append((ax, ay))
                cur.append((bx, by))
        if len(cur) >= 2:
            keep_runs.append(cur)

        for kp in keep_runs:
            if len(kp) < 2:
                continue
            L = sum(math.hypot(b2[0] - a2[0], b2[1] - a2[1]) for a2, b2 in zip(kp, kp[1:]))
            if L < 1.0:
                continue
            dx = abs(kp[-1][0] - kp[0][0])
            dy = abs(kp[-1][1] - kp[0][1])
            axis = "h" if dx >= 3 * dy else ("v" if dy >= 3 * dx else "d")
            out.append(PipeRun(kp, axis))
    return out


def suppress_drawing_margins(segs, page_wh, margin_ratio=0.038):
    """Buang segmen yang berada di atau sangat dekat dengan margin perimeter kertas luar.
    Garis tepi bingkai gambar (drawing frame border), tick koordinat tepi, dan garis batas
    kertas terluar adalah artifak drafting lembar gambar, BUKAN pipa proses."""
    if not segs:
        return []
    W, H = page_wh
    mx = int(W * margin_ratio)
    my = int(H * margin_ratio)
    out = []
    for s in segs:
        xs = [p[0] for p in s.points]
        ys = [p[1] for p in s.points]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

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


def is_furniture_geometry(x0: float, y0: float, x1: float, y1: float,
                          length: float, W: float, H: float) -> bool:
    """5 ATURAN EMAS geometris (Phase 1) — klasifikasi clutter drafting vs pipa proses.

    Referensi: 5 formula rasio teruji yang membuang ~23.5% clutter non-pipa (border
    kertas, title block, tabel NOTES, tick koordinat) tanpa merusak pipa utama.

    KOORDINAT: origin citra = KIRI-ATAS (y bertambah ke bawah, sesuai `img_bgr` OpenCV
    dan seluruh pipeline `pidcorr`). Karena itu "title block BAWAH" (15.1% dari dasar
    lembar) = `cy > 0.849*H`, dan "border ATAS" (2.6% dari tepi atas) = `cy < 0.026*H`.

    Args:
        x0, y0, x1, y1: bounding box / titik ujung segmen (px citra).
        length: panjang busur polyline (px).
        W, H: dimensi lembar (px).

    Returns:
        True bila segmen tergolong furniture/drafting clutter, bukan pipa proses.
    """
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    long_span_limit = 0.80 * max(W, H)

    # 1. Garis tepi terluar / frame span (panjang > 80% sisi terpanjang kertas)
    if length > long_span_limit:
        return True
    # 2. Title block & tabel catatan BAWAH (15.1% dari dasar lembar)
    if cy > 0.849 * H:
        return True
    # 3. Garis border ATAS (2.6% dari tepi atas)
    if cy < 0.026 * H:
        return True
    # 4. Garis grid tick KIRI (2.4% dari tepi kiri)
    if cx < 0.024 * W:
        return True
    # 5. Garis grid tick KANAN (2.3% dari tepi kanan)
    if cx > 0.977 * W:
        return True

    return False


def suppress_furniture_geometry(segs, page_wh, detections=None, furniture=None,
                                label_boxes=None, band_frac=0.151, header_len_frac=0.10,
                                protect_margin_pt=10, label_near_pt=20, dpi=350):
    """Post-filter Phase 1: buang segmen yang lolos sebagai furniture geometris
    (`is_furniture_geometry`) — border kertas, title block, tabel NOTES, tick koordinat.

    Band 15.1% diterapkan SIMETRIS (atas & bawah) karena `is_furniture_geometry`
    memakai origin KIRI-ATAS: title block di dasar lembar = `cy > 0.849*H`, sedangkan
    tabel NOTES/TAG di puncak lembar = `cy < 0.151*H`. Keduanya adalah drafting
    furniture yang tidak boleh lolos ke pipeline tagging.

    GUARD anti-"pipa utama hilang" — aturan band/edge TIDAK berlaku untuk kandidat
    pipa asli, yaitu segmen yang memenuhi salah satu:
      * MENEMBUS bbox simbol terdeteksi (equipment/valve/instrument) dengan irisan
        luas nyata (pipa masuk/keluar simbol, bukan sekadar berimpit tepi tabel), ATAU
      * berupa HEADER (panjang busur > `header_len_frac` * sisi terpanjang), ATAU
      * punya LABEL piping-ID di dekatnya (`label_boxes`) yang berada DI LUAR region
        furniture — label di dalam title block/NOTES adalah isi tabel, bukan penanda
        pipa, sehingga tidak melindungi.
    Aturan span (1) tetap tanpa kecuali: bingkai gambar raksasa selalu dibuang.

    Args:
        label_boxes: bbox teks piping-ID [(x0,y0,x1,y1), ...] untuk proteksi pipa berlabel.
    """
    if not segs:
        return []
    W, H = page_wh
    long_span_limit = 0.80 * max(W, H)
    header_len = header_len_frac * max(W, H)
    m = protect_margin_pt * dpi / 72.0
    near = 2.0 * dpi / 72.0
    label_near = label_near_pt * dpi / 72.0
    boxes = []
    for d in (detections or []):
        if d.get("coarse") in ("equipment", "valve", "instrument"):
            boxes.append((d.get("x1", 0), d.get("y1", 0), d.get("x2", 0), d.get("y2", 0)))
    furn = [tuple(f) for f in (furniture or [])]
    # Label piping-ID yang berada di LUAR furniture saja yang melindungi pipa.
    labels = []
    for lb in (label_boxes or []):
        if isinstance(lb, dict):
            lx0, ly0, lx1, ly1 = lb.get("x1", 0), lb.get("y1", 0), lb.get("x2", 0), lb.get("y2", 0)
        elif isinstance(lb, (tuple, list)):
            lx0, ly0, lx1, ly1 = lb[0], lb[1], lb[2], lb[3]
        else:
            lx0, ly0 = getattr(lb, "x1", 0), getattr(lb, "y1", 0)
            lx1, ly1 = getattr(lb, "x2", 0), getattr(lb, "y2", 0)
        inside_furn = any(lx0 >= fx0 - m and lx1 <= fx1 + m and ly0 >= fy0 - m and ly1 <= fy1 + m
                          for fx0, fy0, fx1, fy1 in furn)
        if not inside_furn:
            labels.append((lx0, ly0, lx1, ly1))

    def _label_protects(x0, y0, x1, y1):
        for lx0, ly0, lx1, ly1 in labels:
            x_ov = min(x1, lx1) - max(x0, lx0) > -label_near
            y_ov = min(y1, ly1) - max(y0, ly0) > -label_near
            if x_ov and (0 <= y0 - ly1 <= label_near or 0 <= ly0 - y1 <= label_near):
                return True                      # label di atas/bawah (pipa horizontal)
            if y_ov and (0 <= x0 - lx1 <= label_near or 0 <= lx0 - x1 <= label_near):
                return True                      # label di samping (pipa vertikal)
        return False

    out = []
    for s in segs:
        pts = s.points
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        length = s.length if hasattr(s, "length") else sum(
            ((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5 for a, b in zip(pts, pts[1:]))
        cy = (pts[0][1] + pts[-1][1]) / 2.0

        # Span (bingkai gambar) selalu dibuang, tanpa kecuali.
        if length > long_span_limit:
            continue

        # Aturan 6 (pelengkap 5 aturan emas): potongan TEKENRAAM yang menyusur tepi
        # dalam ~3.2% lembar dengan panjang > 10% sisi. Aturan #1 (span > 80%) hanya
        # menangkap frame UTUH; frame yang terpotong menjadi beberapa run lolos.
        # Dievaluasi PER SEGMEN (edge) karena satu run bisa berupa polyline yang
        # memuat potongan frame + potongan lain. Audit 100 lembar: 0 run ber-label
        # piping ID terkena aturan ini.
        edge_frac = 0.032
        is_frame = False
        for (ax, ay), (bx, by) in s.segments():
            if abs(ax - bx) <= abs(ay - by):          # edge vertikal
                if ((ax < edge_frac * W and bx < edge_frac * W)
                        or (ax > (1 - edge_frac) * W and bx > (1 - edge_frac) * W)) \
                        and abs(ay - by) > 0.10 * H:
                    is_frame = True
                    break
            else:                                      # edge horizontal
                if ((ay < edge_frac * H and by < edge_frac * H)
                        or (ay > (1 - edge_frac) * H and by > (1 - edge_frac) * H)) \
                        and abs(ax - bx) > 0.10 * W:
                    is_frame = True
                    break
        if is_frame:
            continue

        # Containment furniture: segmen yang SELURUHNYA berada dalam region furniture
        # terdeteksi (title block / NOTES / tabel) + pad kecil = garis rooster tabel.
        # Karena furniture sudah di-blackout pra-skeletisasi, run seperti ini hanya
        # bisa muncul dari box detector yang sedikit terlalu kecil (mis. tabel title
        # block yang memanjang ~40px di atas box) -> tetap clutter, bukan pipa.
        # Pipa yang MENEMBUS furniture tidak seluruhnya terkandung -> tidak kena.
        furn_pad = 60.0 * dpi / 350.0
        if any(x0 >= fx0 - furn_pad and x1 <= fx1 + furn_pad
               and y0 >= fy0 - furn_pad and y1 <= fy1 + furn_pad
               for fx0, fy0, fx1, fy1 in furn):
            continue

        flagged = is_furniture_geometry(
            pts[0][0], pts[0][1], pts[-1][0], pts[-1][1], length, W, H
        )
        # Band 15.1% SIMETRIS: `is_furniture_geometry` memakai origin KIRI-ATAS sehingga
        # aturan #2 (cy > 0.849*H) menangkap title block di DASAR lembar. Tabel
        # NOTES/TAG di PUNCAK lembar (kasus pada lembar target) butuh cerminannya
        # (cy < 0.151*H) — lihat catatan koordinat di docstring.
        if not flagged and cy < band_frac * H:
            flagged = True
        if not flagged:
            out.append(s)
            continue

        # Guard: pipa asli menembus simbol terdeteksi, berupa header, atau berlabel.
        # Untuk garis 1-D (axis-aligned) irisan-bbox selalu nol pada satu sumbu, jadi
        # pakai uji "segmen masuk ke dalam box": proyeksi irisan pada KEDUA sumbu harus
        # punya panjang nyata. Garis tabel yang hanya BERIMPIT dengan tepi box memberi
        # irisan ~0 pada satu sumbu -> tetap dibuang; pipa yang masuk ke simbol lolos.
        crosses = False
        for bx0, by0, bx1, by1 in boxes:
            ix = min(x1, bx1) - max(x0, bx0)
            iy = min(y1, by1) - max(y0, by0)
            if ix >= -near and iy >= -near and (ix > near or iy > near):
                # salah satu sumbu harus benar-benar berada di dalam box (bukan tepi)
                if (x0 > bx0 + near and x1 < bx1 - near) or \
                   (y0 > by0 + near and y1 < by1 - near) or \
                   (ix > near and iy > near):
                    crosses = True
                    break
        in_furniture = any(x0 >= fx0 - m and x1 <= fx1 + m and y0 >= fy0 - m and y1 <= fy1 + m
                           for fx0, fy0, fx1, fy1 in furn)
        if (crosses and not in_furniture) or length > header_len or _label_protects(x0, y0, x1, y1):
            out.append(s)
            continue

        # sisanya: clutter drafting (garis tabel/border/tick pendek) -> buang
    return out


def suppress_revision_clouds(segs):
    """Buang polyline yang membentuk awan revisi (scalloped revision clouds) atau loop tertutup.
    Pipa proses adalah garis ortogonal (lurus / siku), sedangkan awan revisi berbentuk lengkungan bergelombang."""
    if not segs:
        return []
    out = []
    for s in segs:
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
    atau berada di area margin/title block adalah artifak noise/drawing frame/border.

    POLYLINE-AWARE: diagonalitas dievaluasi PER SEGMEN (`zip(pts, pts[1:])`). Versi lama
    menghitung `dx`/`dy` dari titik ujung polyline, sehingga polyline ORTOGONAL yang
    menikung (header horizontal lalu turun vertikal) terlihat "diagonal" dengan rasio
    0.65 > 0.3 dan langsung dibuang karena rentangnya lebar. Terukur pada lembar
    referensi: 2 pipa utama lenyap di tahap ini setelah clipping diperbaiki.

    Polyline dibuang hanya bila ADA segmen yang benar-benar diagonal DAN melanggar
    batas; polyline ortogonal murni selalu lolos berapa pun panjang totalnya.
    """
    if not segs:
        return []
    W, H = page_wh
    if min(W, H) < 800:
        return segs

    max_len = min(max_diag_len, 0.10 * max(W, H))
    out = []
    for s in segs:
        pts = s.points
        if len(pts) < 2:
            continue
        keep_run = True
        for p0, p1 in zip(pts, pts[1:]):
            dx = abs(p1[0] - p0[0])
            dy = abs(p1[1] - p0[1])
            if not (dx > 50 and dy > 50 and min(dx, dy) / max(dx, dy) > 0.3):
                continue                                # segmen ini ortogonal -> aman
            seg_len = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5
            if seg_len > max_len or dx > 0.08 * W or dy > 0.08 * H:
                keep_run = False
                break
            if min(p0[1], p1[1]) >= 0.85 * H:
                keep_run = False
                break
            if min(p0[0], p1[0]) <= 0.05 * W and max(p0[0], p1[0]) >= 0.20 * W:
                keep_run = False
                break
        if keep_run:
            out.append(s)
    return out


def bridge_collinear_headers(segs, max_gap_px=45, tol_px=4, block_boxes=None):
    """Sambungkan segmen pipa kolinear yang terpotong celah kecil (gap di sekitar label/nozzle/header).
    Menjaga kontinuitas pipa panjang seperti HP/LP Production Headers."""
    if not segs or len(segs) < 2:
        return segs

    horiz_groups = defaultdict(list)
    vert_groups = defaultdict(list)
    others = []

    for s in segs:
        pts = s.points if hasattr(s, "points") else s.get("points", [])
        if len(pts) != 2:
            others.append(s)
            continue
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        is_h = abs(y1 - y2) <= tol_px
        is_v = abs(x1 - x2) <= tol_px
        if is_h and not is_v:
            y_mid = int(round((y1 + y2) / 2.0))
            key = y_mid // (tol_px + 1)
            horiz_groups[key].append((min(x1, x2), max(x1, x2), y_mid, s))
        elif is_v and not is_h:
            x_mid = int(round((x1 + x2) / 2.0))
            key = x_mid // (tol_px + 1)
            vert_groups[key].append((min(y1, y2), max(y1, y2), x_mid, s))
        else:
            others.append(s)

    out = list(others)

    block_boxes = block_boxes or []

    def gap_is_blocked(orientation, cross, gap_start, gap_end):
        for x1, y1, x2, y2 in block_boxes:
            if orientation == "h" and y1 <= cross <= y2 and gap_start < x2 and gap_end > x1:
                return True
            if orientation == "v" and x1 <= cross <= x2 and gap_start < y2 and gap_end > y1:
                return True
        return False

    # Process horizontal groups
    for key, items in horiz_groups.items():
        items.sort(key=lambda it: it[0])
        curr_min, curr_max, curr_y, base_s = items[0]
        color = getattr(base_s, "color", "#2563EB")
        label = getattr(base_s, "label", "")

        for next_min, next_max, next_y, s in items[1:]:
            if next_min - curr_max <= max_gap_px and not gap_is_blocked("h", curr_y, curr_max, next_min):
                curr_max = max(curr_max, next_max)
                curr_y = int(round((curr_y + next_y) / 2.0))
            else:
                out.append(PipeRun([(curr_min, curr_y), (curr_max, curr_y)], axis="h", color=color, label=label))
                curr_min, curr_max, curr_y, base_s = next_min, next_max, next_y, s
                color = getattr(base_s, "color", "#2563EB")
                label = getattr(base_s, "label", "")
        out.append(PipeRun([(curr_min, curr_y), (curr_max, curr_y)], axis="h", color=color, label=label))

    # Process vertical groups
    for key, items in vert_groups.items():
        items.sort(key=lambda it: it[0])
        curr_min, curr_max, curr_x, base_s = items[0]
        color = getattr(base_s, "color", "#2563EB")
        label = getattr(base_s, "label", "")

        for next_min, next_max, next_x, s in items[1:]:
            if next_min - curr_max <= max_gap_px and not gap_is_blocked("v", curr_x, curr_max, next_min):
                curr_max = max(curr_max, next_max)
                curr_x = int(round((curr_x + next_x) / 2.0))
            else:
                out.append(PipeRun([(curr_x, curr_min), (curr_x, curr_max)], axis="v", color=color, label=label))
                curr_min, curr_max, curr_x, base_s = next_min, next_max, next_x, s
                color = getattr(base_s, "color", "#2563EB")
                label = getattr(base_s, "label", "")
        out.append(PipeRun([(curr_x, curr_min), (curr_x, curr_max)], axis="v", color=color, label=label))

    return out


def suppress_low_ink_diagonals(segs, img_bgr=None, min_ink_frac=0.5, min_len_px=40,
                               tol_px=3, samples=32, junction_tol_px=25.0):
    """Buang run yang mayoritas jalurnya TIDAK ada tinta di gambar (artefak skeleton).

    Skeletonisasi kadang menyambung dua titik terpisah menjadi garis lurus palsu:
    sebuah run terukur di lembar referensi membentang 1017 px dari `[89,1045]` ke
    `[357,64]` padahal hanya **20%** titik sampelnya yang menyentuh tinta — sisanya
    melintasi kertas kosong. Garis seperti ini bukan pipa, bukan pula bagian gambar,
    dan lolos dari semua filter geometri karena panjangnya masuk akal.

    PENGAMAN (penting): potongan SIKU pada pipa bengkok juga punya tinta rendah
    (terukur 15%) karena skeleton memangkas sudutnya. Potongan seperti itu JANGAN
    dibuang — ia menyambung ke run lain di kedua ujungnya. Karena itu run yang
    salah satu ujungnya berimpit dengan ujung run lain (<= `junction_tol_px`)
    selalu dipertahankan; hanya diagonal yang benar-benar MENYENDIRI yang dibuang.

    Hanya segmen DIAGONAL yang diperiksa: pipa ortogonal pendek yang terpotong
    masking teks/valve tetap dipertahankan (dan memang sering benar-benar ada).

    Args:
        img_bgr: citra sumber; bila None -> no-op (filter dilewati).
        min_ink_frac: fraksi minimum titik sampel yang harus menyentuh tinta.
        min_len_px: hanya segmen lebih panjang dari ini yang diperiksa.
        tol_px: radius toleransi pencarian tinta di sekitar titik sampel.
        junction_tol_px: jarak ujung untuk menganggap run menyambung ke run lain.
    """
    if not segs or img_bgr is None:
        return segs
    import cv2
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    ink = gray < 128
    H, W = ink.shape

    def _ink_fraction(p0, p1):
        hits = tot = 0
        for t in np.linspace(0.05, 0.95, samples):
            x = int(round(p0[0] + (p1[0] - p0[0]) * t))
            y = int(round(p0[1] + (p1[1] - p0[1]) * t))
            if not (0 <= x < W and 0 <= y < H):
                continue
            tot += 1
            if ink[max(0, y - tol_px):y + tol_px + 1,
                    max(0, x - tol_px):x + tol_px + 1].any():
                hits += 1
        return hits / tot if tot else 1.0

    def _dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    # Index endpoints once. Comparing every diagonal against every run is quadratic
    # on large sheets, even when the distance check has an AABB fast reject.
    cell_size = max(float(junction_tol_px), 1.0)
    endpoint_grid = defaultdict(list)
    ends = []
    for idx, s in enumerate(segs):
        pts = s.points
        pair = (tuple(pts[0]), tuple(pts[-1])) if len(pts) >= 2 else None
        ends.append(pair)
        if pair is not None:
            for pt in pair:
                cell = (math.floor(pt[0] / cell_size), math.floor(pt[1] / cell_size))
                endpoint_grid[cell].append((idx, pt))

    def _connects(idx):
        """True bila salah satu ujung run ini berimpit dengan ujung run LAIN."""
        pair = ends[idx]
        if pair is None:
            return False
        for pt in pair:
            cx, cy = math.floor(pt[0] / cell_size), math.floor(pt[1] / cell_size)
            for gx in range(cx - 1, cx + 2):
                for gy in range(cy - 1, cy + 2):
                    for j, other in endpoint_grid.get((gx, gy), ()):
                        if j != idx and _dist(pt, other) <= junction_tol_px:
                            return True
        return False

    out = []
    for idx, s in enumerate(segs):
        pts = s.points
        if len(pts) < 2:
            continue
        keep = True
        for p0, p1 in zip(pts, pts[1:]):
            dx = abs(p1[0] - p0[0]); dy = abs(p1[1] - p0[1])
            seg_len = (dx * dx + dy * dy) ** 0.5
            if seg_len < min_len_px:
                continue
            if not (dx > 30 and dy > 30 and min(dx, dy) / max(dx, dy) > 0.25):
                continue                            # ortogonal -> bukan kandidat
            if _ink_fraction(p0, p1) < min_ink_frac and not _connects(idx):
                keep = False
                break
        if keep:
            out.append(s)
    return out


def bridge_polyline_elbows(runs, max_gap_px=60.0, max_len_px=140.0,
                           max_turn_deg=75.0, block_boxes=None):
    """Sambung potongan SIKU 45° kembali ke polyline pipa (rekonstruksi bengkokan).

    Saat pipa menikung, skeletonisasi memecahnya menjadi tiga run: horizontal,
    potongan diagonal pendek di sudut, lalu vertikal. Contoh terukur pada lembar
    referensi — header `[320,472]→[1738,472]`, siku `[1738,472]→[1813,515]`, dan
    turunan `[1813,515]→[1813,1450]`. Ketiganya satu pipa fisik; tanpa disambung,
    user melihat tiga "pipa" dan klik-ID hanya menyala sebagian.

    Syarat penggabungan (KETAT, supaya pipa berbeda tidak saling menelan):
      1. salah satu run harus PENDEK (<= `max_len_px`) — kandidat potongan siku;
      2. ujung-ujungnya berimpit (<= `max_gap_px`);
      3. sambungannya membentuk bengkokan wajar: sudut antara arah-datang dan
         arah-lanjut <= `max_turn_deg`. Ini mencegah dua run yang kebetulan
         ujungnya berdekatan tetapi arahnya berlawanan/tegak tak wajar ikut
         tergabung menjadi polyline zig-zag.

    Titik hasil gabungan diurutkan mengikuti arah perjalanan, bukan sekadar
    disambung apa adanya.

    Returns:
        list[PipeRun] dengan polyline gabungan.
    """
    if not runs or len(runs) < 2:
        return runs
    block_boxes = block_boxes or []

    def _pts(r):
        return [tuple(p) for p in (r.points if hasattr(r, "points") else r.get("points", []))]

    def _dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    def _ang(u, v):
        """Sudut antara dua vektor (derajat)."""
        nu = (u[0] ** 2 + u[1] ** 2) ** 0.5
        nv = (v[0] ** 2 + v[1] ** 2) ** 0.5
        if nu < 1e-9 or nv < 1e-9:
            return 0.0
        cos = max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (nu * nv)))
        return math.degrees(math.acos(cos))

    items = []
    for r in runs:
        pts = _pts(r)
        if len(pts) < 2:
            continue
        length = sum(_dist(a, b) for a, b in zip(pts, pts[1:]))
        items.append({"pts": pts, "len": length, "axis": getattr(r, "axis", "poly")})

    def _join(pa, pb, ai, bi):
        """Gabung dua polyline pada ujung yang dipilih; kembalikan titik urut.

        Hasilnya harus selalu berupa jalur menerus dari ujung-luar a -> titik
        sambung -> ujung-luar b:
          * ai == -1, bi == 0  : a sudah menuju sambung, b berangkat dari sambung
                                 -> pa + pb
          * ai == -1, bi == -1 : keduanya menuju sambung; b harus dibalik dulu
                                 -> pa + reversed(pb)
          * ai == 0,  bi == 0  : keduanya berangkat dari sambung; a harus dibalik
                                 -> reversed(pa) + pb
          * ai == 0,  bi == -1 : a dibalik agar menuju sambung, b sudah menuju
                                 sambung lalu dibalik agar berangkat darinya
                                 -> reversed(pa) + reversed(pb)
        """
        if ai == -1 and bi == 0:
            return pa + pb
        if ai == -1 and bi == -1:
            return pa + list(reversed(pb))
        if ai == 0 and bi == 0:
            return list(reversed(pa)) + pb
        return list(reversed(pa)) + list(reversed(pb))     # ai == 0 and bi == -1

    cell = max(1.0, float(max_gap_px))
    def _cell(p): return (int(p[0] // cell), int(p[1] // cell))
    def _grid():
        g = defaultdict(list)
        for k, it in enumerate(items):
            if it is None: continue
            for ep in (it["pts"][0], it["pts"][-1]): g[_cell(ep)].append(k)
        return g
    def _cands(k, n, g):
        it = items[k]
        if it is None: return ()
        out = set()
        for ep in (it["pts"][0], it["pts"][-1]):
            cx, cy = _cell(ep)
            for dx in (-1,0,1):
                for dy in (-1,0,1): out.update(g.get((cx+dx,cy+dy), ()))
        return sorted(j for j in out if j != k and j < n)

    merged = True
    while merged:
        merged = False
        n = len(items)
        grid = _grid()
        for i in range(n):
            if merged or i >= len(items):
                break
            for j in _cands(i, n, grid):
                if i == j or i >= len(items) or j >= len(items):
                    continue
                a, b = items[i], items[j]
                # Aturan penggabungan:
                #   * dua pipa PANJANG yang belum pernah bergabung -> TOLAK
                #     (itu dua pipa berbeda, bukan satu bengkokan);
                #   * sisanya boleh, asalkan ujung yang disambung belum pernah
                #     dipakai (`used_ends`) sehingga tidak terbentuk zig-zag;
                #   * sudut sambungan harus wajar (dicek di bawah).
                # Item hasil gabungan (`grew`) tetap boleh menyerap potongan
                # berikutnya — inilah yang membuat rantai
                # header -> siku -> vertikal tersambung utuh.
                a_open = a.get("grew") or a["len"] <= max_len_px
                b_open = b.get("grew") or b["len"] <= max_len_px
                if not (a_open or b_open):
                    continue
                pa, pb = a["pts"], b["pts"]
                for ai, ap in ((0, pa[0]), (-1, pa[-1])):
                    for bi, bp in ((0, pb[0]), (-1, pb[-1])):
                        if _dist(ap, bp) > max_gap_px:
                            continue
                        if any(min(ap[0], bp[0]) <= x2 and max(ap[0], bp[0]) >= x1
                               and min(ap[1], bp[1]) <= y2 and max(ap[1], bp[1]) >= y1
                               for x1, y1, x2, y2 in block_boxes):
                            continue
                        # arah datang (menuju titik sambung pada a) dan arah
                        # lanjut (meninggalkan titik sambung pada b)
                        if ai == -1:
                            va = (pa[-1][0] - pa[-2][0], pa[-1][1] - pa[-2][1])
                        else:
                            va = (pa[0][0] - pa[1][0], pa[0][1] - pa[1][1])
                        if bi == 0:
                            vb = (pb[1][0] - pb[0][0], pb[1][1] - pb[0][1])
                        else:
                            vb = (pb[-2][0] - pb[-1][0], pb[-2][1] - pb[-1][1])
                        turn = _ang(va, vb)
                        if turn > max_turn_deg:
                            continue                    # bengkokan tak wajar -> tolak
                        new_pts = _join(pa, pb, ai, bi)
                        dedup = [new_pts[0]]
                        for p in new_pts[1:]:
                            if _dist(p, dedup[-1]) > 1e-6:
                                dedup.append(p)
                        # Rapikan hanya "ekor" di UJUNG: bila titik terakhir kembali
                        # ke titik sebelumnya (A->B->A), buang titik terakhir itu.
                        # Pembersihan di TENGAH dilarang — titik tengah yang berimpit
                        # adalah sambungan siku yang sah (header -> siku -> vertikal).
                        while len(dedup) >= 3 and _dist(dedup[-1], dedup[-3]) <= 1e-6:
                            dedup.pop(-1)
                            dedup.pop(-1)
                        if len(dedup) < 2:
                            continue
                        new_len = sum(_dist(x, y) for x, y in zip(dedup, dedup[1:]))
                        axis = a["axis"] if a["axis"] == b["axis"] else "poly"
                        items[i] = {"pts": dedup, "len": new_len, "axis": axis,
                                    "grew": True}
                        items.pop(j)
                        merged = True
                        grid = _grid()
                        break
                    if merged:
                        break

    out = []
    for it in items:
        out.append(PipeRun([tuple(p) for p in it["pts"]], it["axis"]))
    return out


def bridge_inline_valve_gaps(runs, detections=None, max_gap_px=75, tol_px=6, block_boxes=None):
    """Sambungkan pipa lurus yang terpotong oleh katup inline (valve) atau celah kecil.
    `block_boxes` mengecualikan gap valve/instrument tertentu yang harus tetap terbuka."""
    if not runs or len(runs) < 2:
        return runs

    valves = [d for d in (detections or []) if d.get("coarse") in ("valve", "instrument")]
    if not valves:
        return runs

    valve_boxes = [(float(v["x1"]), float(v["y1"]), float(v["x2"]), float(v["y2"])) for v in valves]
    block_boxes = block_boxes or []

    def near_any_valve(pt):
        px, py = pt
        return any(
            (vx1 - max_gap_px <= px <= vx2 + max_gap_px) and (vy1 - max_gap_px <= py <= vy2 + max_gap_px)
            for vx1, vy1, vx2, vy2 in valve_boxes
        )

    out = list(runs)
    merged = True
    while merged:
        merged = False
        n = len(out)
        for i in range(n):
            if merged:
                break
            r1 = out[i]
            pts1 = r1.points if hasattr(r1, "points") else r1.get("points", [])
            if len(pts1) < 2 or not (near_any_valve(pts1[0]) or near_any_valve(pts1[-1])):
                continue

            for j in range(i + 1, n):
                if merged:
                    break
                r2 = out[j]
                pts2 = r2.points if hasattr(r2, "points") else r2.get("points", [])
                if len(pts2) < 2:
                    continue

                # Fast AABB reject: skip pairs that are spatially too far apart
                # on BOTH axes. Avoids expensive hypot/pairs computation for
                # distant runs, reducing practical O(N^2) to near-linear.
                if (abs(pts1[0][0] - pts2[0][0]) > max_gap_px * 2
                        and abs(pts1[-1][0] - pts2[0][0]) > max_gap_px * 2
                        and abs(pts1[0][0] - pts2[-1][0]) > max_gap_px * 2
                        and abs(pts1[-1][0] - pts2[-1][0]) > max_gap_px * 2
                        and abs(pts1[0][1] - pts2[0][1]) > max_gap_px * 2
                        and abs(pts1[-1][1] - pts2[0][1]) > max_gap_px * 2
                        and abs(pts1[0][1] - pts2[-1][1]) > max_gap_px * 2
                        and abs(pts1[-1][1] - pts2[-1][1]) > max_gap_px * 2):
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

                    # A caller can explicitly preserve gaps across valve or
                    # instrument bodies even when their endpoints are collinear.
                    if any(min(pA[0], pB[0]) <= bx2 and max(pA[0], pB[0]) >= bx1
                           and min(pA[1], pB[1]) <= by2 and max(pA[1], pB[1]) >= by1
                           for bx1, by1, bx2, by2 in block_boxes):
                        continue

                    # Tangent check: both incoming segment (prevA -> pA) and outgoing (pB -> nextB) must be collinear
                    vA = (pA[0] - prevA[0], pA[1] - prevA[1])
                    vB = (nextB[0] - pB[0], nextB[1] - pB[1])

                    is_hA = abs(vA[0]) >= 1.5 * abs(vA[1])
                    is_hB = abs(vB[0]) >= 1.5 * abs(vB[1])
                    is_vA = abs(vA[1]) >= 1.5 * abs(vA[0])
                    is_vB = abs(vB[1]) >= 1.5 * abs(vB[0])

                    is_h = is_hA and is_hB and dy <= tol_px and dx > 0
                    is_v = is_vA and is_vB and dx <= tol_px and dy > 0

                    has_valve_between = False
                    if valve_boxes and (is_hA == is_hB and is_vA == is_vB):
                        mid_x = (pA[0] + pB[0]) / 2.0
                        mid_y = (pA[1] + pB[1]) / 2.0
                        for vx1, vy1, vx2, vy2 in valve_boxes:
                            if (vx1 - 15 <= mid_x <= vx2 + 15) and (vy1 - 15 <= mid_y <= vy2 + 15):
                                has_valve_between = True
                                break

                    if is_h or is_v or has_valve_between:
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

                        merged_run = PipeRun(points=clean_pts, axis=axis, color=color, label=label)

                        out.pop(j)
                        out.pop(i)
                        out.append(merged_run)
                        merged = True
                        break

    return out


# ----------------------------------------- 3b) SMART BOX-TRACE STITCHING ----------
def _run_to_dict(r):
    """Normalisasi PipeRun | dict -> dict plain (agar stitching bisa memodifikasi titik)."""
    if isinstance(r, dict):
        return dict(r)
    return {
        "id": getattr(r, "id", ""),
        "points": [list(p) for p in getattr(r, "points", [])],
        "axis": getattr(r, "axis", "poly"),
        "pid": getattr(r, "pid", ""),
        "fluid": getattr(r, "fluid", ""),
        "label": getattr(r, "label", getattr(r, "pid", "")),
        "color": getattr(r, "color", "#2563EB"),
        "underline": getattr(r, "underline", False),
        "manual": getattr(r, "manual", False),
        "equipment_outline": getattr(r, "equipment_outline", False),
    }

def _poly_dist(pt, pts):
    """Jarak terdekat titik `pt` ke polyline `pts` (proyeksi ter-clamp per segmen)."""
    px, py = float(pt[0]), float(pt[1])
    if len(pts) < 2:
        return float("inf")
    best = float("inf")
    for i in range(len(pts) - 1):
        ax, ay = float(pts[i][0]), float(pts[i][1])
        bx, by = float(pts[i + 1][0]), float(pts[i + 1][1])
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 <= 1e-9:
            d = math.hypot(px - ax, py - ay)
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
            d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if d < best:
            best = d
    return best

def _apply_points(run, clean_pts):
    """Set points run + rekalkulasi bbox; buang vertex duplikat berurutan."""
    if not clean_pts:
        run["points"] = []
        return
    dedup = [[int(round(clean_pts[0][0])), int(round(clean_pts[0][1]))]]
    for p in clean_pts[1:]:
        q = [int(round(p[0])), int(round(p[1]))]
        if q[0] != dedup[-1][0] or q[1] != dedup[-1][1]:
            dedup.append(q)
    run["points"] = dedup
    run["x1"] = min(p[0] for p in dedup)
    run["y1"] = min(p[1] for p in dedup)
    run["x2"] = max(p[0] for p in dedup)
    run["y2"] = max(p[1] for p in dedup)

def _snap_new_endpoints(nr, matching):
    """Mode ambigu: biarkan run baru independen, tapi snap ujungnya ke titik run eksisting terdekat."""
    out = dict(nr)
    pts = [[int(round(p[0])), int(round(p[1]))] for p in nr.get("points", [])]
    if len(pts) < 2 or not matching:
        return out
    for end_idx in (0, -1):
        px, py = pts[end_idx]
        best, best_d = None, float("inf")
        for m in matching:
            ex, ey = m["end"]
            d = math.hypot(px - ex, py - ey)
            if d < best_d:
                best_d, best = d, (ex, ey)
        if best is not None:
            pts[end_idx] = [int(best[0]), int(best[1])]
    _apply_points(out, pts)
    return out

def stitch_region_runs(existing_runs, new_runs, bounds_roi, snap_radius=18.0):
    """SMART BOX-TRACE STITCHING — sambung cerdas jalur ROI baru ke pipa eksisting.

    Dipanggil oleh `POST /trace-region` setelah crop ROI di-trace. Alih-alih selalu
    menambah run independen (yang membuat pipa terduplikasi di kanvas/sidebar), fungsi
    ini mengevaluasi TITIK UJUNG (endpoints) `new_runs` terhadap endpoint `existing_runs`
    yang berada di sekitar/di dalam `bounds_roi`:

      * SKENARIO A (1-to-1 / Extend): salah satu ujung jalur baru menyentuh HANYA SATU
        ujung pipa eksisting (jarak <= `snap_radius`) -> koordinat pipa eksisting
        DIPERPANJANG. Tidak ada ID/garis baru yang dibuat.
      * SKENARIO B (1-to-2 / Bridge): jalur baru menjembatani DUA ujung pipa eksisting
        yang terputus -> ketiganya digabung (Existing A + New + Existing B) menjadi satu
        polyline utuh. ID garis B yang diserap dihapus agar tidak duplikat.
      * SKENARIO C (New Run): jalur baru tidak menyentuh ujung pipa lama sama sekali ->
        dibiarkan menjadi `PipeRun` baru yang independen.
      * AMBIGU (>2 kandidat / percabangan T): run baru tetap independen, tapi ujungnya
        di-snap ke titik eksisting terdekat agar tidak menggantung.

    Args:
        existing_runs: list[dict|PipeRun] koordinat GLOBAL.
        new_runs: list[dict] hasil trace crop (koordinat GLOBAL).
        bounds_roi: (x1, y1, x2, y2) kotak seleksi global (SEBELUM padding crop).
        snap_radius: radius toleransi sentuh ujung, px (default 18.0).

    Returns:
        (updated_existing, remaining_new, consumed_ids)
        - updated_existing: existing runs (mungkin sudah diperpanjang / digabung, B terhapus)
        - remaining_new: new runs yang tidak terserap (menjadi run independen)
        - consumed_ids: id run yang sudah di-merge/diserap (new & existing-B) untuk dibuang
    """
    existing = [_run_to_dict(r) for r in (existing_runs or [])]
    if not new_runs:
        return existing, [], []

    bx1, by1, bx2, by2 = [float(v) for v in bounds_roi]
    remaining_new = []
    consumed_ids = []

    for nr in new_runs:
        npts = [[int(round(p[0])), int(round(p[1]))] for p in nr.get("points", [])]
        if len(npts) < 2:
            continue
        n_a, n_b = npts[0], npts[-1]

        # --- Cari endpoint existing yang berada di sekitar/dalam ROI dan benar-benar
        #     menyentuh jalur baru (jarak endpoint -> polyline baru <= snap_radius).
        matching = []
        for ri, er in enumerate(existing):
            if er.get("_drop"):
                continue
            epts = er.get("points", [])
            if len(epts) < 2:
                continue
            best = None
            for which, end in (("a", epts[0]), ("b", epts[-1])):
                ex, ey = float(end[0]), float(end[1])
                in_roi = (bx1 - snap_radius <= ex <= bx2 + snap_radius and
                          by1 - snap_radius <= ey <= by2 + snap_radius)
                if not in_roi:
                    continue
                d = _poly_dist((ex, ey), npts)
                if d > snap_radius:
                    continue
                if best is None or d < best["d"]:
                    d_new_a = math.hypot(ex - n_a[0], ey - n_a[1])
                    d_new_b = math.hypot(ex - n_b[0], ey - n_b[1])
                    best = {
                        "ri": ri, "which": which, "end": (int(round(ex)), int(round(ey))),
                        "d": d, "new_end": "a" if d_new_a <= d_new_b else "b",
                    }
            if best is not None:
                matching.append(best)
        matching.sort(key=lambda c: c["d"])

        if len(matching) == 1:
            # ---------------- SKENARIO A: 1-to-1 -> perpanjang pipa eksisting ----------
            m = matching[0]
            ext = [list(p) for p in npts]
            # Orientasi: ext[0] harus berimpit dgn ujung existing yang cocok.
            if m["new_end"] == "b":
                ext = ext[::-1]
            base = [[int(round(p[0])), int(round(p[1]))] for p in existing[m["ri"]].get("points", [])]
            merged = (ext[::-1] + base) if m["which"] == "a" else (base + ext)
            _apply_points(existing[m["ri"]], merged)
            # Warisi label/pid/fluid dari jalur baru bila pipa eksisting belum punya.
            if nr.get("label") and not existing[m["ri"]].get("label"):
                existing[m["ri"]]["label"] = nr["label"]
                if nr.get("pid"):
                    existing[m["ri"]]["pid"] = nr["pid"]
                if nr.get("fluid"):
                    existing[m["ri"]]["fluid"] = nr["fluid"]
            consumed_ids.append(nr.get("id"))

        elif len(matching) == 2:
            # ---------------- SKENARIO B: 1-to-2 -> jembatani dua pipa terputus --------
            m, f = matching[0], matching[1]
            if m["ri"] == f["ri"]:
                remaining_new.append(_snap_new_endpoints(nr, matching))
                continue
            ext = [list(p) for p in npts]
            # Orient the new path so ext[0] meets M and ext[-1] meets F:
            #   m["new_end"] == "b"  -> n_b is nearest M's end, so reverse (ext[0] = n_b).
            #   m["new_end"] == "a"  -> n_a is nearest M's end, so keep as-is.
            if m["new_end"] == "b" and f["new_end"] == "a":
                ext = ext[::-1]
            elif m["new_end"] == "a" and f["new_end"] == "b":
                pass
            else:
                # Kedua ujung existing terdekat ke ujung baru yang SAMA -> bukan bridge bersih.
                remaining_new.append(_snap_new_endpoints(nr, matching))
                continue

            base_m = [[int(round(p[0])), int(round(p[1]))] for p in existing[m["ri"]].get("points", [])]
            base_f = [[int(round(p[0])), int(round(p[1]))] for p in existing[f["ri"]].get("points", [])]
            if m["which"] == "a":
                base_m = base_m[::-1]
            if f["which"] == "b":
                base_f = base_f[::-1]
            merged = base_m + ext + base_f
            _apply_points(existing[m["ri"]], merged)
            # Warisi label dari pipa F / jalur baru bila pipa M belum punya.
            if not existing[m["ri"]].get("label"):
                lbl = existing[f["ri"]].get("label") or nr.get("label")
                if lbl:
                    existing[m["ri"]]["label"] = lbl
                    existing[m["ri"]]["pid"] = (existing[f["ri"]].get("pid") or
                                                nr.get("pid") or lbl)
                    existing[m["ri"]]["fluid"] = (existing[f["ri"]].get("fluid") or
                                                  nr.get("fluid", ""))
            consumed_ids.append(nr.get("id"))
            consumed_ids.append(existing[f["ri"]].get("id"))
            existing[f["ri"]]["_drop"] = True

        else:
            # ---------------- SKENARIO C (atau ambigu): run baru independen -------------
            if len(matching) == 0:
                remaining_new.append(nr)
            else:
                remaining_new.append(_snap_new_endpoints(nr, matching))

    existing = [r for r in existing if not r.get("_drop")]
    for r in existing:
        r.pop("_drop", None)
    consumed = [cid for cid in consumed_ids if cid]
    return existing, remaining_new, consumed

# --------------------------------------------------- Magic Wand (HITL) -------------
# Indeks geometri vektor PDF untuk fitur 1-click trace: satu klik pada garis pipa
# -> ambil PipeRun terdekat. Indeks dibangun dari SELURUH geometri vektor
# (`keep_furniture=True`) karena user memilih garisnya sendiri; dibangun di daemon
# thread saat sheet dibuka (`GET /result`) supaya klik pertama tidak menunggu
# parse pdfplumber (terukur 2.3-6.2 s/lembar) — klik hanya menunggu bila cache
# benar-benar dingin.
_WAND_INDEX_CACHE = OrderedDict()      # key -> list[PipeRun]
_WAND_INDEX_LOCK = threading.Lock()
_WAND_INDEX_INFLIGHT = set()           # key yang sedang dibangun daemon thread
_WAND_INDEX_MAX = 4                    # LRU: cukup untuk sheet yang sedang dibuka
_WAND_INDEX_TIMEOUT_S = 60.0           # batas tunggu prewarm sebelum build sinkron
# Magic Wand memakai PyMuPDF, BUKAN pdfplumber: geometrinya identik (terukur 181 vs
# 182 run pada lembar yang sama, uji tetangga terdekat median 0.0 pt) tetapi
# pdfplumber butuh 10.3 s/lembar sedangkan PyMuPDF 0.97 s. Klik adalah jalur
# interaktif — 10 s tidak bisa diterima; deteksi pipeline tetap memakai
# pdfplumber (kualitas & keamanan /Rotate) lewat env VECTOR_ENGINE.
_WAND_ENGINE = "pymupdf"

def _wand_index_key(pdf_path, dpi, rot, page_index, engine):
    st = os.stat(pdf_path)
    return (os.path.abspath(pdf_path), st.st_mtime_ns, st.st_size,
            int(dpi), int(rot) % 360, int(page_index),
            (engine or _WAND_ENGINE).lower())

def build_vector_index(pdf_path, dpi=350, rot=0, page_index=0, engine=None):
    """Indeks geometri vektor penuh (tanpa filter furniture) — sinkron."""
    # Import LOKAL wajib: vector_tracer mengimpor ..lines (circular di level modul).
    from .implementations.vector_tracer import extract_vector_runs
    return extract_vector_runs(pdf_path, dpi=dpi, rot=rot, page_index=page_index,
                               engine=engine or _WAND_ENGINE, keep_furniture=True)

def _wand_index_store(key, index):
    with _WAND_INDEX_LOCK:
        _WAND_INDEX_CACHE[key] = index
        _WAND_INDEX_CACHE.move_to_end(key)
        while len(_WAND_INDEX_CACHE) > _WAND_INDEX_MAX:
            _WAND_INDEX_CACHE.popitem(last=False)

def prewarm_vector_index(pdf_path, dpi=350, rot=0, page_index=0, engine=None):
    """Bangun indeks di daemon thread (idempoten). Tidak pernah melempar.

    Terukur pada lembar 5790x4094: selesai ~1 s setelah sheet dibuka, sehingga klik
    pertama sudah hangat. `engine=None` -> `_WAND_ENGINE` (PyMuPDF).
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return
    try:
        key = _wand_index_key(pdf_path, dpi, rot, page_index, engine)
    except OSError:
        return
    with _WAND_INDEX_LOCK:
        if key in _WAND_INDEX_CACHE or key in _WAND_INDEX_INFLIGHT:
            return
        _WAND_INDEX_INFLIGHT.add(key)

    def _worker():
        try:
            _wand_index_store(key, build_vector_index(pdf_path, dpi=dpi, rot=rot,
                                                      page_index=page_index, engine=engine))
        except Exception:
            pass
        finally:
            with _WAND_INDEX_LOCK:
                _WAND_INDEX_INFLIGHT.discard(key)

    threading.Thread(target=_worker, daemon=True, name="wand-index-prewarm").start()

def get_vector_index(pdf_path, dpi=350, rot=0, page_index=0, engine=None):
    """Indeks dari cache; bila dingin, tunggu prewarm (maks 60 s) lalu build sinkron."""
    key = _wand_index_key(pdf_path, dpi, rot, page_index, engine)
    deadline = time.time() + _WAND_INDEX_TIMEOUT_S
    while True:
        with _WAND_INDEX_LOCK:
            idx = _WAND_INDEX_CACHE.get(key)
            if idx is not None:
                return idx
            inflight = key in _WAND_INDEX_INFLIGHT
        if not inflight or time.time() >= deadline:
            break
        time.sleep(0.05)
    index = build_vector_index(pdf_path, dpi=dpi, rot=rot, page_index=page_index,
                               engine=engine)
    _wand_index_store(key, index)
    return index

def nearest_run_at(index, x, y, radius=15.0):
    """(run, jarak) terdekat dari (x, y) dalam radius; `(None, inf)` bila kosong.

    Toleransi 0.5 px untuk seri jarak -> menang run yang lebih panjang (pipa asli
    menang atas stub/tick pendek yang kebetulan menyentuh titik klik).
    """
    best, best_d = None, float("inf")
    for r in index:
        d = _poly_dist((x, y), r.points)
        if d > radius:
            continue
        if best is None or d < best_d - 0.5 or (d <= best_d + 0.5 and r.length > best.length):
            best, best_d = r, d
    return best, best_d

def find_duplicate_run(runs, cand, tol_px=4.0):
    """Indeks run di `runs` (dict result_json) yang sudah mencakup `cand` — atau None.

    Duplikat = kedua UJUNG kandidat jatuh dalam `tol_px` dari polyline eksisting.
    """
    if cand is None or len(cand.points) < 2:
        return None
    for i, r in enumerate(runs):
        pts = r.get("points") or []
        if len(pts) < 2:
            continue
        if (_poly_dist(cand.points[0], pts) <= tol_px and
                _poly_dist(cand.points[-1], pts) <= tol_px):
            return i
    return None

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
