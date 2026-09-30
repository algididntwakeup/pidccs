"""Vector-first line tracing (Phase 2) — ekstraksi geometri PDF vektor asli.

P&ID modern umumnya PDF VEKTOR keluaran AutoCAD/SmartPlant: garis pipa tersimpan
sebagai path dengan koordinat eksak. Binarisasi raster + skeletonisasi (Fase B/1)
tetap benar untuk hasil scan, tetapi untuk PDF vektor ia mahal (CPU) dan
menghasilkan garis bergerigi (tangga piksel). Modul ini membaca koordinat asli
sehingga:

  * tracing selesai dalam orde ratusan milidetik (tanpa binarisasi/morfologi),
  * garis 100% lurus (koordinat eksak, bukan aproksimasi piksel),
  * hasil langsung dalam ruang koordinat halaman yang sama dengan piping ID.

Tier halaman (`tier_of`) menentukan jalur: `A1`/`A2` = ada geometri vektor ->
ekstraksi vektor; `raster` = scan/gambar -> orchestrator otomatis jatuh ke
`SkeletonLineTracer` (hybrid fallback, lihat `pidcorr/orchestrator.py`).

Catatan koordinat: titik diambil dari `page.get_cdrawings()` (display space, origin
kiri-atas, sudah menerapkan flag /Rotate halaman) — identik dengan hasil render
`pypdfium2`/PyMuPDF yang dipakai seluruh pipeline. Konversi ke piksel cukup dikali
`dpi / 72.0`; rotasi manual (`rot`, kelipatan 90 searah jarum jam) yang diterapkan
`pipeline.rotate_bgr` pada gambar harus diterapkan juga ke titik vektor.
"""
from __future__ import annotations
import math
import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


from ..interfaces.perception import BaseLineTracer
from ..lines import (
    PipeRun,
    is_furniture_geometry,
    split_runs_at_t_junctions,
    suppress_furniture_geometry,
)

# --------------------------------------------------------------- konstanta -----
AXIS_TOL_PT = 1.2        # toleransi kolinieritas H/V (pt): |y0-y1| <= tol
MERGE_GAP_PT = 42.0      # celah maksimum yang disambung saat merge interval (pt)
MIN_RUN_PT = 10.0        # panjang minimum run hasil merge (pt)
VECTOR_TIER_MIN_PATHS = 50   # ambang jumlah path agar halaman disebut "vektor"
CLOSE_TOL_PT = 1.0       # jarak ujung-ke-ujung agar path disebut tertutup (pt)
STRAIGHT_TOL_DEG = 1.5   # deviasi maksimum agar ruas polyline disebut lurus

# Ambang recall teks-outline. Pada P&ID AutoCAD sebagian besar label digambar
# sebagai glyph OUTLINE (fill kosong), sehingga setiap huruf menjadi puluhan ruas
# pendek. Filter ukuran glyph membuangnya TANPA menyentuh pipa: pipa digambar
# dengan garis panjang, sedangkan goresan glyph < ~14 pt.
GLYPH_MAX_SEG_PT = 14.0      # ruas individual lebih pendek dari ini = kandidat glyph
GLYPH_MAX_RUN_PT = 26.0      # run hasil merge di bawah ini = sisa glyph/dash

Point = Tuple[float, float]
Segment = Tuple[Point, Point]


# ------------------------------------------------------------------ helpers ----
def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _closed(cv: Dict[str, Any], tol: float = CLOSE_TOL_PT) -> bool:
    """True bila path kembali ke titik awalnya (simbol/bubble/persegi)."""
    pts = cv.get("pts") or []
    if len(pts) < 3:
        return False
    return _dist(tuple(pts[0]), tuple(pts[-1])) <= tol


def _angle_dev_deg(a: Point, b: Point, c: Point) -> float:
    """Deviasi arah b->c dari arah a->b, dalam derajat (0 = kolinear)."""
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def _straight_segments(pts: Sequence[Sequence[float]],
                       tol_deg: float = STRAIGHT_TOL_DEG) -> List[Segment]:
    """Pecah polyline menjadi ruas-ruas LURUS maksimal.

    Busur/lengkung terpecah menjadi beberapa ruas pendek dengan arah berbeda dan
    tidak akan ikut ter-merge oleh interval merge (arahnya tidak kolinear),
    sedangkan polyline CAD (yang tersusun dari ruas lurus) kembali utuh.
    """
    if not pts or len(pts) < 2:
        return []
    out: List[Segment] = []
    start = (float(pts[0][0]), float(pts[0][1]))
    prev = start
    for i in range(1, len(pts)):
        cur = (float(pts[i][0]), float(pts[i][1]))
        if _dist(prev, cur) < 1e-9:
            continue
        if i >= 2 and _angle_dev_deg(start, prev, cur) > tol_deg:
            if _dist(start, prev) > 1e-9:
                out.append((start, prev))
            start = prev
        prev = cur
    if _dist(start, prev) > 1e-9:
        out.append((start, prev))
    return out


def _cubic_points(p0: Point, p1: Point, p2: Point, p3: Point,
                  n: int = 8) -> List[Point]:
    """Sampling kubik Bezier (item 'c') agar lengkung tetap terbaca sebagai geometri."""
    out: List[Point] = []
    for i in range(n + 1):
        t = i / n
        mt = 1.0 - t
        x = (mt ** 3) * p0[0] + 3 * (mt ** 2) * t * p1[0] + 3 * mt * (t ** 2) * p2[0] + (t ** 3) * p3[0]
        y = (mt ** 3) * p0[1] + 3 * (mt ** 2) * t * p1[1] + 3 * mt * (t ** 2) * p2[1] + (t ** 3) * p3[1]
        out.append((x, y))
    return out


# --------------------------------------------------- konversi path -> ruas -----
def _xy(v: Any) -> Point:
    """Normalisasi titik: tuple (get_cdrawings) maupun Point (get_drawings)."""
    if isinstance(v, (tuple, list)):
        return (float(v[0]), float(v[1]))
    return (float(v.x), float(v.y))


def drawing_segments(drawing: Dict[str, Any],
                     rotation_matrix: Any = None) -> List[Segment]:
    """Ruas lurus dari satu drawing PyMuPDF (`items`: l/c/re/qu).

    Mendukung dua varian API: `get_drawings()` (Point/Rect) dan
    `get_cdrawings()` (tuple mentah) — keduanya dipakai modul ini.

    PENTING: `page.get_cdrawings()` mengembalikan koordinat dalam ruang
    UN-ROTATED (mediabox), sedangkan pipeline merender halaman dengan /Rotate
    diterapkan. `rotation_matrix` halaman dikenakan ke setiap titik agar hasil
    vektor berada di ruang tampilan yang sama dengan citra raster.
    """
    out: List[Segment] = []
    for it in (drawing.get("items") or []):
        kind = it[0]
        if kind == "l":
            out.append((_xy(it[1]), _xy(it[2])))
        elif kind == "c":
            out.extend(_straight_segments(_cubic_points(
                _xy(it[1]), _xy(it[2]), _xy(it[3]), _xy(it[4]),
            )))
        elif kind == "re":
            r = it[1]
            if isinstance(r, (tuple, list)):
                x0, y0, x1, y1 = (float(r[0]), float(r[1]), float(r[2]), float(r[3]))
            else:
                x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
            out.extend([
                ((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0)),
            ])
        elif kind == "qu":
            q = it[1]
            if hasattr(q, "ul"):                    # Quad object (get_drawings)
                quad = [q.ul, q.ur, q.lr, q.ll, q.ul]
            else:                                   # tuple 4 titik (get_cdrawings)
                quad = [q[0], q[1], q[2], q[3], q[0]]
            out.extend(_straight_segments([_xy(p) for p in quad]))

    if rotation_matrix is not None:
        out = [(_apply_matrix(a, rotation_matrix), _apply_matrix(b, rotation_matrix))
               for a, b in out]
    return out


def _apply_matrix(p: Point, m: Any) -> Point:
    """Terapkan matriks afinitas PyMuPDF ke satu titik (tanpa dependency Point)."""
    a, b, c, d, e, f = m.a, m.b, m.c, m.d, m.e, m.f
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def _path_endpoints(drawing: Dict[str, Any]) -> Tuple[Optional[Point], Optional[Point]]:
    """Titik awal & akhir sebuah path, apa pun jenis item-nya."""
    items = drawing.get("items") or []
    first = last = None
    for it in items:
        kind = it[0]
        if kind == "l":
            if first is None:
                first = _xy(it[1])
            last = _xy(it[2])
        elif kind == "c":
            if first is None:
                first = _xy(it[1])
            last = _xy(it[4])
        elif kind == "re":
            r = it[1]
            if isinstance(r, (tuple, list)):
                x0, y0 = float(r[0]), float(r[1])
            else:
                x0, y0 = float(r.x0), float(r.y0)
            if first is None:
                first = (x0, y0)
            last = (x0, y0)                    # persegi selalu tertutup
        elif kind == "qu":
            q = it[1]
            pts = [q.ul, q.ur, q.lr, q.ll] if hasattr(q, "ul") else list(q)
            if first is None:
                first = _xy(pts[0])
            last = _xy(pts[-1])
    return first, last


def _is_closed_path(drawing: Dict[str, Any], tol: float = CLOSE_TOL_PT) -> bool:
    """True bila path tertutup (simbol/bubble/kotak) — BUKAN pipa.

    Pipa digambar sebagai polyline TERBUKA. Path tertutup pada P&ID adalah
    simbol instrumen (bubble), outline valve, kotak equipment, dan glyph huruf;
    semuanya harus dikecualikan dari ekstraksi garis pipa.
    """
    if drawing.get("closePath"):
        return True
    items = drawing.get("items") or []
    if len(items) == 1 and items[0][0] == "re":
        return True
    first, last = _path_endpoints(drawing)
    if first is None or last is None:
        return False
    return _dist(first, last) <= tol


def _is_stroked(drawing: Dict[str, Any]) -> bool:
    """Path digambar sebagai GARIS (bukan isian/simbol terisi)."""
    if not drawing.get("color"):
        return False
    if drawing.get("fill") and drawing.get("type") == "f":
        return False
    return True


# ------------------------------------------------------------ tier halaman -----
def tier_of(page, drawings: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    """Classify a page for routing: 'A1'/'A2' vector heuristics or 'raster'.

    Fewer than 50 PyMuPDF drawing paths is classified as raster. At or above
    that threshold, A1 requires at least 50 drawing paths with a straight-line
    item; otherwise the page is A2. These labels are not page sizes or quality
    grades. Both vector tiers use identical extraction, with raster fallback
    when vector extraction yields no runs.

    `drawings` boleh diberikan agar `get_cdrawings()` tidak dipanggil ulang.
    """
    if drawings is None:
        drawings = page.get_cdrawings() or []
    if len(drawings) < VECTOR_TIER_MIN_PATHS:
        return "raster"
    n_lines = sum(1 for d in drawings
                  if any(it[0] == "l" for it in (d.get("items") or [])))
    return "A1" if n_lines >= VECTOR_TIER_MIN_PATHS else "A2"


def tier_of_pdf(pdf_path: str, page_index: int = 0) -> str:
    """`tier_of` untuk berkas PDF (dipakai orchestrator untuk memilih tracer)."""
    if not pdf_path or not os.path.exists(pdf_path) or not pdf_path.lower().endswith(".pdf"):
        return "raster"
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return "raster"
    try:
        with pymupdf.open(pdf_path) as doc:
            return tier_of(doc[page_index])
    except Exception:
        return "raster"


# ------------------------------------------------------------ ekstraksi --------
def page_segments(page, straight_tol_deg: float = STRAIGHT_TOL_DEG,
                  drawings: Optional[Sequence[Dict[str, Any]]] = None,
                  rotation_matrix: Any = None,
                  include_closed: bool = False) -> List[Segment]:
    """Seluruh ruas lurus kandidat pipa dari halaman: path bergaris TERBUKA.

    `rotation_matrix` = `page.rotation_matrix`; `get_cdrawings()` memberi
    koordinat un-rotated sehingga matriks ini wajib agar sejajar citra render.
    Path tertutup (bubble instrumen, outline valve, kotak equipment) dilewati
    kecuali `include_closed=True`.
    """
    if drawings is None:
        drawings = page.get_cdrawings() or []
    out: List[Segment] = []
    for d in drawings:
        if not _is_stroked(d):
            continue
        if not include_closed and _is_closed_path(d):
            continue
        out.extend(drawing_segments(d, rotation_matrix=rotation_matrix))
    return out


def _merge_intervals(items: Iterable[Tuple[float, float, float]],
                     gap: float, min_len: float) -> List[Tuple[float, float, float]]:
    """Bucket merge interval pada satu sumbu.

    `items` = (bucket_center, lo, hi). Interval yang berdekatan (celah <= `gap`)
    disambung menjadi satu run; hasil < `min_len` dibuang.
    """
    buckets: Dict[int, List[Tuple[float, float, float]]] = {}
    for mid, lo, hi in items:
        if hi < lo:
            lo, hi = hi, lo
        buckets.setdefault(int(round(mid / AXIS_TOL_PT)), []).append((mid, lo, hi))

    merged: List[Tuple[float, float, float]] = []
    for key in sorted(buckets):
        rows = sorted(buckets[key], key=lambda r: r[1])
        c_lo, c_hi = rows[0][1], rows[0][2]
        w_len = max(1e-9, rows[0][2] - rows[0][1])
        acc_mid, acc_len = rows[0][0] * w_len, w_len
        for mid, lo, hi in rows[1:]:
            if lo - c_hi <= gap:
                c_hi = max(c_hi, hi)
                w = max(1e-9, hi - lo)
                acc_mid += mid * w
                acc_len += w
            else:
                merged.append((acc_mid / acc_len, c_lo, c_hi))
                c_lo, c_hi = lo, hi
                acc_mid, acc_len = mid * w_len, w_len
        merged.append((acc_mid / acc_len, c_lo, c_hi))

    return [(mid, lo, hi) for mid, lo, hi in merged if hi - lo >= min_len]


def _to_runs(segs: Sequence[Segment], min_run_pt: float = MIN_RUN_PT,
             axis_tol: float = AXIS_TOL_PT, gap: float = MERGE_GAP_PT,
             min_seg_pt: float = 0.0) -> Tuple[List[Segment], List[str]]:
    """Merge segmen H & V (interval bucket) dan pertahankan segmen diagonal."""
    horiz, vert, diag = [], [], []
    for (x0, y0), (x1, y1) in segs:
        if min_seg_pt and _dist((x0, y0), (x1, y1)) < min_seg_pt:
            continue
        if abs(y0 - y1) <= axis_tol and abs(x1 - x0) > axis_tol:
            horiz.append((y0, min(x0, x1), max(x0, x1)))
        elif abs(x0 - x1) <= axis_tol and abs(y1 - y0) > axis_tol:
            vert.append((x0, min(y0, y1), max(y0, y1)))
        elif _dist((x0, y0), (x1, y1)) > 1e-9:
            diag.append(((x0, y0), (x1, y1)))

    runs: List[Segment] = []
    axes: List[str] = []
    for y, lo, hi in _merge_intervals(horiz, gap, min_run_pt):
        runs.append(((lo, y), (hi, y)))
        axes.append("h")
    for x, lo, hi in _merge_intervals(vert, gap, min_run_pt):
        runs.append(((x, lo), (x, hi)))
        axes.append("v")
    for a, b in diag:
        if _dist(a, b) >= max(min_run_pt, 2 * min_seg_pt):
            runs.append((a, b))
            axes.append("d")
    return runs, axes


def _inline_symbol_regions(segs: Sequence[Segment], tol_pt: float = 1.5):
    """Recognize compact open V-shaped valve bodies and return conservative masks.

    Vector CAD valves are often drawn as two open diagonal strokes, so the existing
    closed-path filter cannot recognize them. A compact pair sharing an apex with
    aligned tips is a stronger symbol cue than an isolated diagonal (for example,
    an ordinary pipe elbow).
    """
    diagonals = []
    for a, b in segs:
        if abs(a[0] - b[0]) > 1.5 and abs(a[1] - b[1]) > 1.5:
            diagonals.append((a, b))

    # Spatial hash keeps this pass near-linear on dense CAD sheets with thousands
    # of diagonal glyph and symbol strokes.
    from collections import defaultdict
    cell_size = max(tol_pt, 1.0)
    endpoint_cells = defaultdict(list)
    pairs = {}
    for index, (a, b) in enumerate(diagonals):
        for point, tip in ((a, b), (b, a)):
            cx, cy = int(math.floor(point[0] / cell_size)), int(math.floor(point[1] / cell_size))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other_index, other_point, other_tip in endpoint_cells.get((cx + dx, cy + dy), ()):
                        if other_index != index and _dist(point, other_point) <= tol_pt:
                            key = (min(index, other_index), max(index, other_index))
                            if key not in pairs:
                                pairs[key] = (((point[0] + other_point[0]) / 2,
                                               (point[1] + other_point[1]) / 2),
                                              (tip, other_tip) if index < other_index else (other_tip, tip))
            endpoint_cells[(cx, cy)].append((index, point, tip))

    regions = []
    for shared, tips in pairs.values():
        p, q = tips
        lo_x, hi_x = sorted((p[0], q[0]))
        lo_y, hi_y = sorted((p[1], q[1]))
        if 4.0 <= hi_x - lo_x <= 24.0 and abs(p[1] - q[1]) <= 2.5:
            if lo_x - 2.5 <= shared[0] <= hi_x + 2.5 and abs(shared[1] - (p[1] + q[1]) / 2) >= 2.5:
                regions.append(("h", lo_x - 1.5, min(p[1], q[1], shared[1]) - 1.5,
                                hi_x + 1.5, max(p[1], q[1], shared[1]) + 1.5))
        elif 4.0 <= hi_y - lo_y <= 24.0 and abs(p[0] - q[0]) <= 2.5:
            if lo_y - 2.5 <= shared[1] <= hi_y + 2.5 and abs(shared[0] - (p[0] + q[0]) / 2) >= 2.5:
                regions.append(("v", min(p[0], q[0], shared[0]) - 1.5, lo_y - 1.5,
                                max(p[0], q[0], shared[0]) + 1.5, hi_y + 1.5))

    # Some CAD valve symbols are drawn as two triangles meeting at one apex
    # (four short diagonal strokes), rather than as one open V. Recognize only a
    # compact four-way diagonal junction with two opposed direction pairs; this
    # avoids treating an ordinary elbow as a valve.
    vertices = []
    vertex_cells = defaultdict(list)
    cell_size = max(tol_pt, 1.0)
    for a, b in diagonals:
        for endpoint, tip in ((a, b), (b, a)):
            cx = int(math.floor(endpoint[0] / cell_size))
            cy = int(math.floor(endpoint[1] / cell_size))
            candidates = [
                vertex_id
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                for vertex_id in vertex_cells.get((cx + dx, cy + dy), ())
                if _dist(endpoint, vertices[vertex_id]["center"]) <= tol_pt
            ]
            if candidates:
                vertex = vertices[candidates[0]]
            else:
                vertex = {"center": endpoint, "tips": []}
                vertex_id = len(vertices)
                vertices.append(vertex)
                vertex_cells[(cx, cy)].append(vertex_id)
            vertex["tips"].append(tip)
    for vertex in vertices:
        tips = vertex["tips"]
        if len(tips) != 4:
            continue
        center = vertex["center"]
        vectors = []
        for tip in tips:
            dx, dy = tip[0] - center[0], tip[1] - center[1]
            length = math.hypot(dx, dy)
            if length <= 1e-9:
                break
            vectors.append((dx / length, dy / length))
        if len(vectors) != 4:
            continue
        pairings = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]
        opposed = any(
            vectors[a][0] * vectors[b][0] + vectors[a][1] * vectors[b][1] <= -0.45
            and vectors[c][0] * vectors[d][0] + vectors[c][1] * vectors[d][1] <= -0.45
            for (a, b), (c, d) in pairings
        )
        x_values = [center[0], *(tip[0] for tip in tips)]
        y_values = [center[1], *(tip[1] for tip in tips)]
        x1, x2 = min(x_values), max(x_values)
        y1, y2 = min(y_values), max(y_values)
        width, height = x2 - x1, y2 - y1
        if not opposed or not (4.0 <= width <= 30.0 and 4.0 <= height <= 30.0):
            continue
        touches_h = any(
            abs(a[1] - b[1]) <= tol_pt and min(a[0], b[0]) <= center[0] <= max(a[0], b[0])
            and abs(a[1] - center[1]) <= tol_pt for a, b in segs
        )
        touches_v = any(
            abs(a[0] - b[0]) <= tol_pt and min(a[1], b[1]) <= center[1] <= max(a[1], b[1])
            and abs(a[0] - center[0]) <= tol_pt for a, b in segs
        )
        orientation = "h" if touches_h or (not touches_v and width >= height) else "v"
        regions.append((orientation, x1 - 1.5, y1 - 1.5, x2 + 1.5, y2 + 1.5))
    # Duplicate pairs can describe the same valve when a PDF path is segmented.
    unique = []
    for region in regions:
        if not any(region[0] == prev[0] and max(region[1], prev[1]) <= min(region[3], prev[3])
                   and max(region[2], prev[2]) <= min(region[4], prev[4]) for prev in unique):
            unique.append(region)
    return unique


def _split_runs_at_symbol_regions(runs: Sequence[Segment], axes: Sequence[str], regions):
    """Keep pipe geometry on both sides of a compact inline valve, leaving a gap."""
    if not regions:
        return list(runs), list(axes)
    out_runs, out_axes = [], []
    for (a, b), axis in zip(runs, axes):
        if axis not in ("h", "v"):
            if any(region[0] in ("h", "v", "both") and min(a[0], b[0]) >= region[1] and max(a[0], b[0]) <= region[3]
                   and min(a[1], b[1]) >= region[2] and max(a[1], b[1]) <= region[4]
                   for region in regions):
                continue
            out_runs.append((a, b))
            out_axes.append(axis)
            continue

        lo, hi = (sorted((a[0], b[0])) if axis == "h" else sorted((a[1], b[1])))
        cross = (a[1] + b[1]) / 2 if axis == "h" else (a[0] + b[0]) / 2
        cuts = []
        for orient, x1, y1, x2, y2 in regions:
            if orient not in (axis, "both"):
                continue
            region_lo, region_hi = (x1, x2) if axis == "h" else (y1, y2)
            region_cross_lo, region_cross_hi = (y1, y2) if axis == "h" else (x1, x2)
            if region_cross_lo <= cross <= region_cross_hi and lo < region_hi and hi > region_lo:
                cuts.append((max(lo, region_lo), min(hi, region_hi)))
        pieces = []
        cursor = lo
        for cut_lo, cut_hi in sorted(cuts):
            if cut_lo > cursor:
                pieces.append((cursor, cut_lo))
            cursor = max(cursor, cut_hi)
        if cursor < hi:
            pieces.append((cursor, hi))
        for p0, p1 in pieces:
            if p1 - p0 < MIN_RUN_PT:
                continue
            if axis == "h":
                out_runs.append(((p0, cross), (p1, cross)))
            else:
                out_runs.append(((cross, p0), (cross, p1)))
            out_axes.append(axis)
    return out_runs, out_axes


def _rotate_symbol_regions(regions, rot: int, W_pt: float, H_pt: float, dpi: int):
    """Rotate PDF-space symbol boxes with the same transform as traced runs."""
    rot = int(rot) % 360
    if not rot or not regions:
        return list(regions or [])
    scale = dpi / 72.0
    W_px, H_px = W_pt * scale, H_pt * scale
    rotated = []
    for orientation, x1, y1, x2, y2 in regions:
        corners = [_rotate_point(x * scale, y * scale, rot, W_px, H_px)
                   for x, y in ((x1, y1), (x1, y2), (x2, y1), (x2, y2))]
        rx1 = min(p[0] for p in corners) / scale
        ry1 = min(p[1] for p in corners) / scale
        rx2 = max(p[0] for p in corners) / scale
        ry2 = max(p[1] for p in corners) / scale
        if orientation != "both" and rot in (90, 270):
            orientation = "v" if orientation == "h" else "h"
        rotated.append((orientation, rx1, ry1, rx2, ry2))
    return rotated


def _drop_glyph_noise(runs: Sequence[Segment], axes: Sequence[str],
                      max_run_pt: float = GLYPH_MAX_RUN_PT,
                      tol: float = 2.0) -> Tuple[List[Segment], List[str]]:
    """Buang run sisa glyph/teks-outline: pendek DAN tidak menyambung ke run lain.

    Run H/V sangat pendek (< `max_run_pt`) pada P&ID vektor hampir selalu berasal
    dari outline huruf/dimensi. Pipa asli yang benar-benar pendek (stub valve)
    tetap dipertahankan bila menyambung ke run lain di ujungnya — karena itu
    pengecekan dilakukan terhadap konektivitas ujung, bukan panjang semata.

    Ujung-ujung di-bucket ke grid spasial `tol` sehingga pencarian tetangga O(1)
    (versi loop-pasangan sebelumnya O(N²) dan mendominasi waktu eksekusi).
    """
    n = len(runs)
    if n == 0:
        return [], []

    cell = max(tol, 1e-6)

    def _key(p: Point) -> Tuple[int, int]:
        return (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)))

    # Grid: sel -> daftar indeks run yang punya ujung di sel itu.
    grid: Dict[Tuple[int, int], List[int]] = {}
    for i in range(n):
        for p in runs[i]:
            grid.setdefault(_key(p), []).append(i)

    def _has_neighbour(i: int) -> bool:
        a, b = runs[i]
        for p in (a, b):
            kx, ky = _key(p)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in grid.get((kx + dx, ky + dy), ()):
                        if j == i:
                            continue
                        c, d = runs[j]
                        if (_dist(p, c) <= tol or _dist(p, d) <= tol):
                            return True
        return False

    keep_runs, keep_axes = [], []
    for i, (a, b) in enumerate(runs):
        if _dist(a, b) >= max_run_pt or _has_neighbour(i):
            keep_runs.append((a, b))
            keep_axes.append(axes[i])
    return keep_runs, keep_axes


def _cluster_rows(rows: List[Tuple[float, float, float]],
                  max_row_gap_pt: float, min_rows: int,
                  min_row_overlap: float = 0.35) -> List[List[int]]:
    """Kelompokkan garis sejajar menjadi cluster tabel.

    `rows` = (posisi_sumbu_lintang, lo, hi, index). Dua baris masuk cluster yang
    sama bila (a) jarak posisinya <= `max_row_gap_pt` DAN (b) tumpang-tindih
    memanjangnya >= `min_row_overlap` dari KEDUA sisi (baris itu sendiri dan
    cluster tujuan).

    Syarat (b) dua sisi itu penting karena dua hal:

    * Lembar P&ID sering memuat DUA tabel berdampingan pada ketinggian sama
      (tabel data equipment di kiri, TAG/NO/REVISION di kanan). Tanpa cek
      overlap, keduanya tergabung menjadi satu region raksasa.
    * Garis TEPI/BORDER menggambar selebar lembar. Bila baris tabel boleh
      menempel padanya, seluruh tabel terserap ke satu cluster raksasa yang
      rentangnya selebar kertas — sehingga tidak ada baris yang "seluruhnya di
      dalam region" dan filter tabel gagal total. Dengan syarat dua sisi, baris
      tabel pendek tidak lagi terserap border (fraksi terhadap cluster kecil).
    """
    clusters: List[Dict[str, Any]] = []
    for pos, lo, hi, idx in sorted(rows):
        best, best_ov = None, 0.0
        for c in clusters:
            if pos - c["y_max"] > max_row_gap_pt:
                continue
            ov = min(hi, c["x_hi"]) - max(lo, c["x_lo"])
            if ov <= 0:
                continue
            frac_row = ov / max(1e-9, hi - lo)
            frac_cluster = ov / max(1e-9, c["x_hi"] - c["x_lo"])
            if min(frac_row, frac_cluster) < min_row_overlap:
                continue
            score = min(frac_row, frac_cluster)
            if score > best_ov:
                best, best_ov = c, score
        if best is None:
            clusters.append({"idxs": [idx], "y_min": pos, "y_max": pos,
                             "x_lo": lo, "x_hi": hi})
        else:
            best["idxs"].append(idx)
            best["y_min"] = min(best["y_min"], pos)
            best["y_max"] = max(best["y_max"], pos)
            best["x_lo"] = min(best["x_lo"], lo)
            best["x_hi"] = max(best["x_hi"], hi)
    return [c["idxs"] for c in clusters if len(c["idxs"]) >= min_rows]


def _table_regions(runs: Sequence[Segment], axes: Sequence[str],
                   max_row_gap_pt: float = 12.0,
                   min_rows: int = 3,
                   min_fill_frac: float = 0.5) -> List[Tuple[float, float, float, float]]:
    """Deteksi region TABEL dari geometri vektor murni (tanpa YOLO/citra).

    Tabel drafting (title block, TAG list, NOTES, revision block) tersusun dari
    beberapa garis SEJAJAR berjarak rapat yang rentangnya saling menumpuk. Pipa
    tidak punya profil itu: dua pipa paralel biasanya berjarak > 12 pt dan
    rentangnya tidak saling menutup penuh. Cluster >= `min_rows` baris yang
    mengisi >= `min_fill_frac` dari rentangnya = tabel; hal sama untuk kolom.

    Inilah yang membuat jalur vektor tidak perlu masking furniture raster: garis
    tabel dibuang karena STRUKTUR-nya, bukan karena posisinya di tepi lembar.
    """
    def _axis_clusters(idxs: Sequence[int], horizontal: bool) -> List[List[int]]:
        rows = []
        for i in idxs:
            a, b = runs[i]
            if horizontal:
                rows.append((a[1], min(a[0], b[0]), max(a[0], b[0]), i))
            else:
                rows.append((a[0], min(a[1], b[1]), max(a[1], b[1]), i))
        return _cluster_rows(rows, max_row_gap_pt, min_rows)

    def _region(idxs: List[int], horizontal: bool):
        lo_all, hi_all = None, None
        pos_all = []
        for i in idxs:
            a, b = runs[i]
            if horizontal:
                lo, hi = min(a[0], b[0]), max(a[0], b[0])
                pos_all.append(a[1])
            else:
                lo, hi = min(a[1], b[1]), max(a[1], b[1])
                pos_all.append(a[0])
            lo_all = lo if lo_all is None else min(lo_all, lo)
            hi_all = hi if hi_all is None else max(hi_all, hi)
        if lo_all is None or hi_all is None:
            return None
        span = max(1e-9, hi_all - lo_all)
        # Baris harus benar-benar mengisi rentangnya (ciri tabel), bukan sekadar
        # beberapa garis sejajar kebetulan.
        total_len = sum(_dist(runs[i][0], runs[i][1]) for i in idxs)
        if total_len < min_fill_frac * span * len(idxs):
            return None
        p0, p1 = min(pos_all), max(pos_all)
        if horizontal:
            return (lo_all, p0, hi_all, p1)
        return (p0, lo_all, p1, hi_all)

    regions: List[Tuple[float, float, float, float]] = []
    h_idx = [i for i, ax in enumerate(axes) if ax == "h"]
    v_idx = [i for i, ax in enumerate(axes) if ax == "v"]
    for grp in _axis_clusters(h_idx, True):
        r = _region(grp, True)
        if r:
            regions.append(r)
    for grp in _axis_clusters(v_idx, False):
        r = _region(grp, False)
        if r:
            regions.append(r)
    return regions


def _drop_inside_regions(runs: Sequence[Segment], axes: Sequence[str],
                         regions: Sequence[Tuple[float, float, float, float]],
                         pad_pt: float = 1.0) -> Tuple[List[Segment], List[str]]:
    """Buang run yang SELURUHNYA berada di dalam region tabel."""
    if not regions:
        return list(runs), list(axes)
    keep_runs, keep_axes = [], []
    for (a, b), ax in zip(runs, axes):
        x0, y0 = min(a[0], b[0]), min(a[1], b[1])
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        inside = any(x0 >= rx0 - pad_pt and x1 <= rx1 + pad_pt
                     and y0 >= ry0 - pad_pt and y1 <= ry1 + pad_pt
                     for rx0, ry0, rx1, ry1 in regions)
        if not inside:
            keep_runs.append((a, b))
            keep_axes.append(ax)
    return keep_runs, keep_axes


def page_segments_pdfplumber(pdf_path: str, page_index: int = 0,
                             include_closed: bool = False):
    """Ruas lurus via pdfplumber (koordinat SUDAH display-space).

    Keunggulan pdfplumber: `/Rotate` halaman diterapkan otomatis, jadi tidak ada
    risiko lupa `rotation_matrix` seperti pada `page.get_cdrawings()` PyMuPDF.
    Konsekuensinya lebih lambat (5.8–27 s vs 0.26 s pada lembar referensi) dan
    waktu parsing-nya tidak stabil antar-run.

    Titik ujung diambil dari `pts` (bukan `x0/y0/x1/y1`) supaya polyline dan
    kurva terurai benar — memakai sudut bbox untuk path ber-`pts` hanya benar
    untuk garis lurus tunggal.

    Returns:
        (segments, (page_width_pt, page_height_pt)) dalam ruang tampilan.
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        W_pt, H_pt = float(page.width), float(page.height)
        objects = list(page.lines) + list(page.curves)

    out: List[Segment] = []
    for obj in objects:
        if obj.get("stroke") is False:
            continue
        pts = obj.get("pts") or []
        if not pts:
            x0, y0 = obj.get("x0"), obj.get("y0")
            x1, y1 = obj.get("x1"), obj.get("y1")
            if None in (x0, y0, x1, y1):
                continue
            pts = [(x0, y0), (x1, y1)]
        if not include_closed:
            if obj.get("fill"):
                continue
            if len(pts) >= 3 and _dist((float(pts[0][0]), float(pts[0][1])),
                                       (float(pts[-1][0]), float(pts[-1][1]))) <= CLOSE_TOL_PT:
                continue
        out.extend(_straight_segments([(float(p[0]), float(p[1])) for p in pts]))
    return out, (W_pt, H_pt)


def _rotate_point(x: float, y: float, rot: int, w: float, h: float) -> Tuple[float, float]:
    """Terapkan rotasi manual yang sama dengan `pipeline.rotate_bgr` (CW)."""
    rot = int(rot) % 360
    if rot == 90:
        return h - 1.0 - y, x
    if rot == 180:
        return w - 1.0 - x, h - 1.0 - y
    if rot == 270:
        return y, w - 1.0 - x
    return x, y


def extract_vector_runs(pdf_path: str, dpi: int = 350, page_index: int = 0,
                        rot: int = 0, progress: Optional[Callable[[str], None]] = None,
                        page=None, engine: str = "pdfplumber",
                        keep_furniture: bool = False) -> List[PipeRun]:
    """Ekstrak PipeRun dari geometri vektor PDF.

    Args:
        pdf_path: berkas PDF vektor.
        dpi: target kanvas (skala = dpi / 72.0).
        page_index: halaman yang diproses.
        rot: rotasi manual (CW, kelipatan 90) yang dipakai pipeline pada citra.
        progress: callback progres opsional.
        page: objek halaman PyMuPDF siap pakai (menghindari parsing ulang).
        engine: `"pdfplumber"` (default — mengutamakan KUALITAS dan keamanan
            rotasi: `/Rotate` ditangani otomatis oleh library sehingga tidak ada
            risiko lupa `rotation_matrix`; waktu 5.8–27 s) atau `"pymupdf"`
            (~0.3 s, tetapi koordinatnya un-rotated sehingga `rotation_matrix`
            wajib diterapkan). Geometri keduanya identik (uji tetangga terdekat:
            median 0.0 pt); pilihan ini murni soal kecepatan vs keamanan rotasi.
        keep_furniture: `True` = JANGAN buang furniture (border frame, title
            block, tabel NOTES/TAG). Dipakai Magic Wand: user mengklik garis
            tertentu secara eksplisit, jadi tidak ada alasan membuang geometri
            apa pun dari kandidat. Default `False` = perilaku deteksi biasa.

    Returns:
        list[PipeRun] dengan skema identik keluaran skeleton tracer.
    """
    engine = (engine or "pdfplumber").lower()

    if engine == "pdfplumber":
        try:
            segs, (W_pt, H_pt) = page_segments_pdfplumber(pdf_path, page_index)
            if progress:
                progress(f"vektor: {len(segs)} ruas garis (pdfplumber, display-space)")
            # pdfplumber sudah display-space; rotasi manual tetap diterapkan bila ada.
            return _segs_to_runs(segs, W_pt, H_pt, dpi=dpi, rot=rot, progress=progress,
                                 keep_furniture=keep_furniture)
        except ImportError:
            # Engine opsional: jangan gagalkan tracing hanya karena paketnya tidak
            # terpasang — jatuh ke PyMuPDF, bukan ke jalur raster.
            if progress:
                progress("pdfplumber tidak tersedia — memakai engine PyMuPDF...")
            engine = "pymupdf"

    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf

    own_doc = None
    try:
        if page is None:
            own_doc = pymupdf.open(pdf_path)
            page = own_doc[page_index]

        # Ukuran halaman dalam ruang tampilan (setelah /Rotate diterapkan).
        r = page.rect
        W_pt, H_pt = float(r.width), float(r.height)

        # Satu kali parsing path untuk tier + ekstraksi (get_cdrawings ~0.4 s).
        drawings = page.get_cdrawings() or []
        if progress:
            progress(f"vektor: {len(drawings)} path vektor (tier {tier_of(page, drawings)})")

        # `get_cdrawings()` mengembalikan koordinat UN-ROTATED; `rotation_matrix`
        # halaman wajib diterapkan agar sejajar citra render (tanpa ini, ink
        # coverage jatuh 0.99 -> 0.07 pada lembar ber-/Rotate).
        segs = page_segments(page, drawings=drawings,
                             rotation_matrix=page.rotation_matrix)
        if progress:
            progress(f"vektor: {len(segs)} ruas garis dari PDF")
        return _segs_to_runs(segs, W_pt, H_pt, dpi=dpi, rot=rot, progress=progress,
                             keep_furniture=keep_furniture)
    finally:
        if own_doc is not None:
            try:
                own_doc.close()
            except Exception:
                pass


def _segs_to_runs(segs: Sequence[Segment], W_pt: float, H_pt: float, *,
                  dpi: int = 350, rot: int = 0,
                  progress: Optional[Callable[[str], None]] = None,
                  keep_furniture: bool = False) -> List[PipeRun]:
    """Ruas lurus (pt, display-space) -> PipeRun (px) — merge, filter, konversi."""
    runs_pt, axes = _to_runs(segs, min_seg_pt=GLYPH_MAX_SEG_PT)
    symbol_regions = _rotate_symbol_regions(_inline_symbol_regions(segs), rot, W_pt, H_pt, dpi)
    runs_pt, axes = _split_runs_at_symbol_regions(runs_pt, axes, symbol_regions)
    # Buang sisa glyph/dash yang tidak tersambung ke jaringan mana pun.
    runs_pt, axes = _drop_glyph_noise(runs_pt, axes)
    # Buang baris/kolom TABEL (title block, TAG list, NOTES) berdasarkan
    # struktur geometrisnya — jalur raster sudah mem-blackout area ini lebih
    # dulu, sedangkan jalur vektor harus mengenalinya dari pola garis.
    table_regions = [] if keep_furniture else _table_regions(runs_pt, axes)
    if table_regions:
        runs_pt, axes = _drop_inside_regions(runs_pt, axes, table_regions)
    if progress:
        progress(f"vektor: {len(runs_pt)} run setelah merge, filter glyph "
                 f"& {len(table_regions)} region tabel")

    scale = dpi / 72.0
    out: List[PipeRun] = []
    for (a, b), axis in zip(runs_pt, axes):
        (x0, y0), (x1, y1) = a, b
        length_pt = _dist(a, b)
        # Filter Fase 1 (rasio, jadi sah dalam satuan pt): buang border frame,
        # title block, tabel NOTES, dan tick koordinat.
        if not keep_furniture and is_furniture_geometry(x0, y0, x1, y1, length_pt, W_pt, H_pt):
            continue
        px0, py0 = _rotate_point(x0 * scale, y0 * scale, rot, W_pt * scale, H_pt * scale)
        px1, py1 = _rotate_point(x1 * scale, y1 * scale, rot, W_pt * scale, H_pt * scale)
        pts = [(int(round(px0)), int(round(py0))), (int(round(px1)), int(round(py1)))]
        if pts[0] == pts[1]:
            continue
        out.append(PipeRun(points=pts, axis=axis, color="#2563EB", manual=False))

    # Post-filter Fase 1 (band 15.1% simetris + guard header/label/deteksi):
    # di ruang PDF belum ada deteksi YOLO maupun bbox OCR, jadi guard-nya tidak
    # aktif — yang bekerja adalah band simetris, aturan tepi, dan aturan span.
    # Ini yang membuang tabel TAG/NOTES di puncak lembar dan title block bawah.
    if not keep_furniture:
        out = suppress_furniture_geometry(out, page_wh=(W_pt * scale, H_pt * scale), dpi=dpi)
    return split_runs_at_t_junctions(
        out,
        tol_px=max(2.0, 1.5 * scale),
        min_piece_px=max(6.0, 3.0 * scale),
    )


class VectorLineTracer(BaseLineTracer):
    """Vector-first tracer untuk PDF vektor, dengan fallback raster di orchestrator.

    Args:
        page_index: halaman yang diproses.
        engine: `"pdfplumber"` (default — mengutamakan kualitas hasil: `/Rotate`
            ditangani otomatis sehingga tidak ada risiko salah orientasi; 5.8–27 s)
            atau `"pymupdf"` (~0.3 s, tetapi `rotation_matrix` wajib diterapkan
            manual). Geometri kedua engine identik.
    """

    def __init__(self, page_index: int = 0, engine: str = "pdfplumber"):
        self.page_index = page_index
        self.engine = engine

    # -- tier -----------------------------------------------------------------
    def tier(self, pdf_path: str, page_index: Optional[int] = None) -> str:
        """Tier halaman PDF: 'A1' | 'A2' (vektor) atau 'raster'."""
        return tier_of_pdf(pdf_path, self.page_index if page_index is None else page_index)

    # -- BaseLineTracer -------------------------------------------------------
    def trace(self, img_bgr=None, dpi: int = 350, detections=None, furniture=None,
              progress: Optional[Callable[[str], None]] = None,
              pdf_path: Optional[str] = None, page_index: Optional[int] = None,
              rot: int = 0, **kwargs) -> List[PipeRun]:
        """Ekstraksi vektor bila `pdf_path` tersedia; selain itu kembalikan []."""
        if not pdf_path or not os.path.exists(pdf_path):
            return []
        return extract_vector_runs(
            pdf_path, dpi=dpi,
            page_index=self.page_index if page_index is None else page_index,
            rot=rot, progress=progress, engine=self.engine,
        )
