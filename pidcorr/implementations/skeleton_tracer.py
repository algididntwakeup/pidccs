import math
from collections import defaultdict
from typing import Callable, Dict, Any, List, Optional, Tuple
import cv2
import numpy as np

from ..interfaces.perception import BaseLineTracer
from ..lines import (
    PipeRun,
    suppress_box_edges,
    suppress_equipment_interior,
    snap_endpoints_to_equipment,
    suppress_furniture,
    suppress_box_outlines,
    detect_boxes,
    suppress_drawing_margins,
    suppress_furniture_geometry,
    suppress_revision_clouds,
    suppress_diagonal_artifacts,
    suppress_low_ink_diagonals,
    suppress_text_artifacts,
    suppress_floating_stubs,
    split_runs_at_t_junctions,
)


# Kernel pembobot bit untuk kode 8-tetangga Zhang-Suen.
# Urutan bit mengikuti P2..P9 (searah jarum jam mulai dari atas):
#   P2=kiri-atas, P3=atas, P4=kanan-atas, P5=kanan,
#   P6=kanan-bawah, P7=bawah, P8=kiri-bawah, P9=kiri
_NEIGHBOUR_CODE_KERNEL = np.array([[128, 1, 2],
                                   [64, 0, 4],
                                   [32, 16, 8]], dtype=np.float32)

_ZS_LUT_CACHE = None


def _zhang_suen_luts():
    """Bangun 2 lookup-table (step 0 & 1) untuk Zhang-Suen; cache sekali per proses.

    Semua kondisi Zhang-Suen (2 <= B <= 6, A == 1, dan dua kondisi penghapusan
    per step) hanya bergantung pada 8 bit tetangga, jadi bisa dipra-hitung untuk
    256 kemungkinan. Saat runtime tinggal `lut[code]` — jauh lebih murah daripada
    mengevaluasi ~14 operasi numpy per piksel per iterasi.
    """
    global _ZS_LUT_CACHE
    if _ZS_LUT_CACHE is not None:
        return _ZS_LUT_CACHE
    luts = []
    for step in (0, 1):
        lut = np.zeros(256, dtype=bool)
        for code in range(256):
            p = [(code >> i) & 1 for i in range(8)]
            B = sum(p)
            if B < 2 or B > 6:
                continue
            seq = p + [p[0]]
            A = sum(1 for i in range(8) if seq[i] == 0 and seq[i + 1] == 1)
            if A != 1:
                continue
            P2, P3, P4, P5, P6, P7, P8, P9 = p
            if step == 0:
                if P2 * P4 * P6:
                    continue
                if P4 * P6 * P8:
                    continue
            else:
                if P2 * P4 * P8:
                    continue
                if P2 * P6 * P8:
                    continue
            lut[code] = True
        luts.append(lut)
    _ZS_LUT_CACHE = luts
    return luts

def _morphological_skeleton(binary_img: np.ndarray) -> np.ndarray:
    """Thin binary ink to one-pixel paths with the Zhang-Suen algorithm."""
    img = (binary_img > 0).astype(np.uint8)
    if not img.any():
        return (img * 255).astype(np.uint8)

    luts = _zhang_suen_luts()
    padded = np.pad(img, 1)
    center = padded[1:-1, 1:-1]
    max_iter = max(img.shape) + 8
    for _ in range(max_iter):
        changed = False
        for lut in luts:
            # filter2D menghitung kode 8-tetangga (bit P2..P9) dalam SATU call C++,
            # menggantikan 8 pergeseran + 7 penjumlahan numpy per iterasi.
            code = cv2.filter2D(padded, cv2.CV_8U, _NEIGHBOUR_CODE_KERNEL,
                                borderType=cv2.BORDER_CONSTANT)[1:-1, 1:-1]
            cond = lut[code] & (center > 0)
            if cond.any():
                center[cond] = 0
                changed = True
        if not changed:
            break

    return (center * 255).astype(np.uint8)

def _classify_junction_geometry(
    node_id: int,
    centroid: Tuple[float, float],
    incident_edge_info: List[Tuple[int, Tuple[float, float]]],
) -> Tuple[str, List[Tuple[int, int]], List[int]]:
    """Classify junction degree and geometry (Task B.09 Junction Classifier).

    Distinguishes:
    - 4-way crossover: 2 pairs of approximately collinear opposite edges (dot product <= -0.35).
      Splits the crossing into 2 independent through-paths that pass over each other without merging.
    - 3-way T-junction: 1 pair of collinear through-edges (dot product <= -0.4) and 1 branch edge.
      The through-pipe continues unbroken, while the branch pipe connects at the junction.
    - 2-way corner / elbow: connects the two edges into a single polyline run.
    - 1-way endpoint: line termination.

    Returns:
        (junction_type, through_pairs, branch_edges)
    """
    degree = len(incident_edge_info)
    if degree <= 1:
        branch = [e[0] for e in incident_edge_info]
        return "endpoint", [], branch

    if degree == 2:
        e0, u0 = incident_edge_info[0]
        e1, u1 = incident_edge_info[1]
        dp = u0[0] * u1[0] + u0[1] * u1[1]
        jtype = "straight" if dp <= -0.7 else "corner"
        return jtype, [(e0, e1)], []

    if degree == 3:
        edges = [e[0] for e in incident_edge_info]
        u = [e[1] for e in incident_edge_info]
        pairs = [(0, 1, 2), (0, 2, 1), (1, 2, 0)]  # (a, b, branch)
        best_pair = None
        min_dot = 1.0
        for a, b, branch in pairs:
            dp = u[a][0] * u[b][0] + u[a][1] * u[b][1]
            if dp < min_dot:
                min_dot = dp
                best_pair = (edges[a], edges[b], edges[branch])

        if min_dot <= -0.35 and best_pair is not None:
            return "t_junction", [(best_pair[0], best_pair[1])], [best_pair[2]]
        else:
            return "complex", [], edges

    if degree == 4:
        edges = [e[0] for e in incident_edge_info]
        u = [e[1] for e in incident_edge_info]
        partitions = [
            ((0, 1), (2, 3)),
            ((0, 2), (1, 3)),
            ((0, 3), (1, 2)),
        ]
        best_part = None
        best_score = 999.0
        for (a, b), (c, d) in partitions:
            dp1 = u[a][0] * u[b][0] + u[a][1] * u[b][1]
            dp2 = u[c][0] * u[d][0] + u[c][1] * u[d][1]
            score = dp1 + dp2
            if score < best_score:
                best_score = score
                best_part = ((edges[a], edges[b], dp1), (edges[c], edges[d], dp2))

        if best_part is not None:
            (p1_a, p1_b, dp1), (p2_a, p2_b, dp2) = best_part
            if dp1 <= -0.3 and dp2 <= -0.3:
                return "crossover", [(p1_a, p1_b), (p2_a, p2_b)], []
            elif dp1 <= -0.35:
                return "crossover_partial", [(p1_a, p1_b)], [p2_a, p2_b]
            elif dp2 <= -0.35:
                return "crossover_partial", [(p2_a, p2_b)], [p1_a, p1_b]

        return "complex", [], edges

    edges = [e[0] for e in incident_edge_info]
    return "complex", [], edges


def _order_edge_pixels(pts, p0, p1):
    """Urutkan piksel sebuah edge menjadi jalur kontinu p0 -> p1 (8-connected walk).

    Mengapa perlu: urutan berbasis proyeksi sumbu (dot product) tidak menentukan
    urutan yang unik untuk edge diagonal/tangga - banyak piksel punya skalar
    proyeksi yang sama, dan urutan antar-piksel itu menjadi sembarang. Akibatnya
    polyline yang dibentuk bisa melompat dan berbalik arah (backtracking), yang
    lalu tampak di UI sebagai garis zig-zag.

    Walk ini selalu memilih tetangga 8-connected yang belum dikunjungi, dengan
    prioritas yang paling dekat ke tujuan p1. Kalau tidak ada tetangga tersisa
    (edge terputus karena skeleton bertingkat), lompat ke piksel belum dikunjungi
    terdekat agar semua piksel tetap terwakili.
    """
    if not pts:
        return []
    remaining = set((int(x), int(y)) for x, y in pts)
    if not remaining:
        return []

    def _nearest_to(target, candidates):
        tx, ty = target
        return min(candidates, key=lambda p: (p[0] - tx) ** 2 + (p[1] - ty) ** 2)

    cur = _nearest_to(p0, remaining)
    order = [cur]
    remaining.discard(cur)
    while remaining:
        nbrs = [q for q in ((cur[0] + dx, cur[1] + dy)
                            for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)
                if q in remaining]
        nxt = _nearest_to(p1, nbrs) if nbrs else _nearest_to(cur, remaining)
        order.append(nxt)
        remaining.discard(nxt)
        cur = nxt
    return order

def _prune_chain(points, min_step=3.0, cos_thresh=-0.5):
    """Bersihkan polyline dari lompatan/zig-zag hasil chaining di junction.

    Setelah skeletonisasi Zhang-Suen, tiap piksel jalur punya tepat 2 tetangga,
    sehingga `_graph_segments` bisa mengurai graf dengan benar. Namun pada
    junction, pasangan "through" hasil `_classify_junction_geometry` kadang
    menyambung edge yang arahnya berbalik. Akibatnya polyline hasil chaining
    berisi titik yang mundur-maju, yang di UI tampak sebagai garis zig-zag.

    Terukur sebelum prune (lembar referensi): 7 dari 35 run punya backtrack,
    contoh nyata
        [(1712,1838), (1962,1838), (1960,1838), (1712,1802)]
    -- titik ketiga mundur 2px lalu titik keempat melompat balik ke kiri.

    Dua pembersihan, diulang sampai stabil:
      1. buang titik yang jaraknya < `min_step` dari titik sebelumnya
         (node cluster selebar 2-3px menghasilkan titik nyaris kembar),
      2. buang titik yang membuat arah BERBALIK (cosinus sudut < `cos_thresh`).

    Pipa proses TIDAK PERNAH berbalik arah, jadi ambang -0.5 aman: siku 90
    derajat punya cos 0 dan tetap dipertahankan.
    """
    if len(points) < 3:
        return points
    out = list(points)
    changed = True
    while changed and len(out) >= 3:
        changed = False
        # 1) buang titik berdekatan
        dedup = [out[0]]
        for p in out[1:]:
            if math.hypot(p[0] - dedup[-1][0], p[1] - dedup[-1][1]) >= min_step:
                dedup.append(p)
        if len(dedup) != len(out):
            out = dedup
            changed = True
        # 2) buang titik yg membalik arah
        i = 1
        while i < len(out) - 1:
            a, b, c = out[i - 1], out[i], out[i + 1]
            v1 = (b[0] - a[0], b[1] - a[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            d1 = math.hypot(v1[0], v1[1])
            d2 = math.hypot(v2[0], v2[1])
            if d1 < 1e-9 or d2 < 1e-9:
                out.pop(i)
                changed = True
                continue
            cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (d1 * d2)
            if cos < cos_thresh:
                out.pop(i)
                changed = True
                continue
            i += 1
    return out if len(out) >= 2 else points

def _graph_segments(skel: np.ndarray, min_length: int) -> List[PipeRun]:
    """Extract and chain skeleton graph edges into continuous PipeRun polylines (Task B.08 + B.09).

    Uses crossing-number node detection and local geometry classification to:
    1. Resolve 4-way crossovers into 2 independent through-pipes.
    2. Merge 3-way T-junction through-pipes while keeping the branch pipe distinct.
    3. Chain 2-way elbow corners into clean polyline runs.
    """
    ink = (skel > 0).astype(np.uint8)
    padded = np.pad(ink, 1)
    neighbours = [
        padded[:-2, 1:-1], padded[:-2, 2:], padded[1:-1, 2:],
        padded[2:, 2:], padded[2:, 1:-1], padded[2:, :-2],
        padded[1:-1, :-2], padded[:-2, :-2],
    ]
    # A diagonal staircase can have three adjacent pixels but only two distinct
    # arms. Count neighbour groups to keep those pixels inside a graph edge.
    degree = sum(neighbours)
    transitions = sum(
        (neighbours[i] != neighbours[(i + 1) % 8]).astype(np.uint8)
        for i in range(8)
    ) // 2
    cardinal_degree = neighbours[0] + neighbours[2] + neighbours[4] + neighbours[6]
    junctions = (degree > 2) & ((transitions >= 3) | (cardinal_degree >= 3))
    node_pixels = ((ink > 0) & ((transitions == 1) | junctions)).astype(np.uint8)

    # Merge adjacent node pixels into node clusters
    node_pixels = cv2.dilate(node_pixels, np.ones((3, 3), np.uint8), iterations=1) & ink
    node_count, node_labels, _, node_centroids = cv2.connectedComponentsWithStats(
        node_pixels, 8
    )
    if node_count <= 1:
        return []

    graph_body = ink.copy()
    graph_body[node_pixels > 0] = 0
    edge_count, edge_labels, edge_stats, _ = cv2.connectedComponentsWithStats(graph_body, 8)

    dilated_nodes = cv2.dilate(node_pixels, np.ones((3, 3), np.uint8))
    touch_mask = (edge_labels > 0) & (dilated_nodes > 0)
    touch_ys, touch_xs = np.where(touch_mask)
    H, W = node_labels.shape

    edge_adjacent: Dict[int, set] = defaultdict(set)
    for x, y in zip(touch_xs, touch_ys):
        e_id = int(edge_labels[y, x])
        y0, y1 = max(0, y - 1), min(H, y + 2)
        x0, x1 = max(0, x - 1), min(W, x + 2)
        for nid in node_labels[y0:y1, x0:x1].flat:
            if nid > 0:
                edge_adjacent[e_id].add(int(nid))

    valid_edge_ids = {eid for eid, adj in edge_adjacent.items() if len(adj) == 2}

    edges: Dict[int, Dict[str, Any]] = {}
    node_incident: Dict[int, List[int]] = defaultdict(list)

    for edge_id in valid_edge_ids:
        adj = edge_adjacent[edge_id]
        first, second = sorted(adj)
        p0 = (int(round(node_centroids[first][0])), int(round(node_centroids[first][1])))
        p1 = (int(round(node_centroids[second][0])), int(round(node_centroids[second][1])))

        bx, by, bw, bh, _ = edge_stats[edge_id]
        sub_ys, sub_xs = np.where(edge_labels[by:by + bh, bx:bx + bw] == edge_id)
        pts = list(zip((sub_xs + bx).tolist(), (sub_ys + by).tolist()))

        # Order edge pixels from p0 to p1 dengan PATH WALK 8-connected.
        # Proyeksi ke sumbu p0->p1 (cara lama) salah untuk edge diagonal/tangga:
        # banyak piksel berbagi skalar proyeksi yang sama sehingga urutannya acak
        # dan polyline hasilnya bisa mundur-maju (backtracking). Terukur: satu run
        # berisi 18 titik dengan lompatan (832,646)->(932,665)->(939,656)->(900,661)
        # ->(895,646) -> jalur tidak kontinu.
        walk = _order_edge_pixels(pts, p0, p1)
        ordered_points = [p0] + [(int(x), int(y)) for x, y in walk] + [p1]

        # Simplify collinear points along edge
        pts_arr = np.array(ordered_points, dtype=np.int32).reshape((-1, 1, 2))
        approx = cv2.approxPolyDP(pts_arr, epsilon=1.5, closed=False)
        clean_pts = [(int(pt[0][0]), int(pt[0][1])) for pt in approx]
        if len(clean_pts) < 2:
            clean_pts = [p0, p1]

        edges[edge_id] = {
            "first_node": first,
            "second_node": second,
            "points": clean_pts,
            "length": math.hypot(p1[0] - p0[0], p1[1] - p0[1]),
        }
        node_incident[first].append(edge_id)
        node_incident[second].append(edge_id)

    # Classify each junction
    through_map: Dict[Tuple[int, int], int] = {}

    for node_id in range(1, node_count):
        inc_edges = node_incident[node_id]
        if len(inc_edges) < 2:
            continue

        cx, cy = node_centroids[node_id]
        incident_info = []
        for e_id in inc_edges:
            e = edges[e_id]
            if e["first_node"] == node_id:
                idx = min(len(e["points"]) - 1, 3)
                target = e["points"][idx]
            else:
                idx = max(0, len(e["points"]) - 1 - 3)
                target = e["points"][idx]

            dx, dy = target[0] - cx, target[1] - cy
            dlen = math.hypot(dx, dy)
            if dlen > 0:
                incident_info.append((e_id, (dx / dlen, dy / dlen)))

        _, through_pairs, _ = _classify_junction_geometry(node_id, (cx, cy), incident_info)
        for ea, eb in through_pairs:
            through_map[(node_id, ea)] = eb
            through_map[(node_id, eb)] = ea

    # Chain through-edges into continuous PipeRun polylines
    visited_edges = set()
    runs: List[PipeRun] = []

    for edge_id in sorted(edges.keys()):
        if edge_id in visited_edges:
            continue

        visited_edges.add(edge_id)
        curr_edge = edges[edge_id]
        chain = list(curr_edge["points"])

        # Extend forward from second_node
        curr_e_id = edge_id
        curr_node = curr_edge["second_node"]
        walked_nodes = {curr_edge["first_node"], curr_edge["second_node"]}
        while (curr_node, curr_e_id) in through_map:
            next_e_id = through_map[(curr_node, curr_e_id)]
            if next_e_id in visited_edges:
                break
            visited_edges.add(next_e_id)
            next_edge = edges[next_e_id]
            next_pts = list(next_edge["points"])
            if next_edge["first_node"] != curr_node:
                next_pts = next_pts[::-1]
            chain.extend(next_pts[1:])
            curr_node = next_edge["second_node"] if next_edge["first_node"] == curr_node else next_edge["first_node"]
            curr_e_id = next_e_id
            # Anti-loop: pipa proses tidak pernah mengelilingi simpul yang sama.
            # Tanpa ini, chaining bisa masuk kembali ke junction yang sudah
            # dilewati dan menyisipkan titik mundur (zig-zag di UI).
            if curr_node in walked_nodes:
                break
            walked_nodes.add(curr_node)

        # Extend backward from first_node
        curr_e_id = edge_id
        curr_node = curr_edge["first_node"]
        while (curr_node, curr_e_id) in through_map:
            prev_e_id = through_map[(curr_node, curr_e_id)]
            if prev_e_id in visited_edges:
                break
            visited_edges.add(prev_e_id)
            prev_edge = edges[prev_e_id]
            prev_pts = list(prev_edge["points"])
            if prev_edge["second_node"] != curr_node:
                prev_pts = prev_pts[::-1]
            chain = prev_pts[:-1] + chain
            curr_node = prev_edge["first_node"] if prev_edge["second_node"] == curr_node else prev_edge["second_node"]
            curr_e_id = prev_e_id
            # Anti-loop (lihat penjelasan di arah forward).
            if curr_node in walked_nodes:
                break
            walked_nodes.add(curr_node)

        chain = _prune_chain(chain)

        tot_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(chain, chain[1:]))
        if tot_len < min_length:
            continue

        dx = abs(chain[-1][0] - chain[0][0])
        dy = abs(chain[-1][1] - chain[0][1])
        axis = "h" if dx >= 3 * dy else ("v" if dy >= 3 * dx else "d")
        runs.append(PipeRun(points=chain, axis=axis, underline=False))

    # Diagonal PCA staircase fallback for isolated diagonal strokes
    component_count, component_labels, component_stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    components_with_edges = set(component_labels[edge_labels > 0])
    H, W = ink.shape[:2]

    for component_id in range(1, component_count):
        if component_id in components_with_edges:
            continue
        if component_stats[component_id, cv2.CC_STAT_AREA] < min_length:
            continue
        bx, by, bw, bh, _ = component_stats[component_id]
        # An isolated straight diagonal stroke in P&ID never spans large areas
        if bw > 250 or bh > 250 or (min(W, H) >= 800 and (bw > 0.08 * W or bh > 0.08 * H)):
            continue

        sub_ys, sub_xs = np.where(component_labels[by:by + bh, bx:bx + bw] == component_id)
        xs = sub_xs + bx
        ys = sub_ys + by
        points = np.column_stack((xs.astype(float), ys.astype(float)))
        centered = points - points.mean(axis=0)
        _, s, vh = np.linalg.svd(centered, full_matrices=False)
        # Check linearity: true straight stroke must have low perpendicular variance
        if s[0] <= 0 or (s[1] / s[0]) > 0.12:
            continue

        direction = vh[0]
        projection = centered @ direction
        p0 = points[projection.argmin()]
        p1 = points[projection.argmax()]
        dx, dy = abs(p1[0] - p0[0]), abs(p1[1] - p0[1])
        if math.hypot(dx, dy) > 250:
            continue

        axis = "h" if dx >= 3 * dy else ("v" if dy >= 3 * dx else "d")
        runs.append(PipeRun(
            points=[[int(round(p0[0])), int(round(p0[1]))], [int(round(p1[0])), int(round(p1[1]))]],
            axis=axis,
            underline=False,
        ))

    return runs


class SkeletonLineTracer(BaseLineTracer):
    """Graph-based skeletonization line tracer with crossover vs T-junction classification."""

    def __init__(self, min_length_px: int = 12, suppress_text_artifacts: bool = True,
                 text_artifact_max_side_pt: float = 10.0,
                 text_artifact_max_area_pt2: float = 40.0,
                 text_artifact_max_aspect: float = 4.0,
                 suppress_floating_stubs: bool = True,
                 floating_stub_max_len_px: float = 28.0):
        self.min_length_px = min_length_px
        self.suppress_text_artifacts = suppress_text_artifacts
        self.text_artifact_max_side_pt = text_artifact_max_side_pt
        self.text_artifact_max_area_pt2 = text_artifact_max_area_pt2
        self.text_artifact_max_aspect = text_artifact_max_aspect
        self.suppress_floating_stubs = suppress_floating_stubs
        self.floating_stub_max_len_px = floating_stub_max_len_px

    def trace(
        self,
        img_bgr: np.ndarray,
        dpi: int = 350,
        detections: Optional[List[Dict[str, Any]]] = None,
        furniture: Optional[List[List[int]]] = None,
        tokens: Optional[List[Dict[str, Any]]] = None,
        pids: Optional[List[Any]] = None,
        progress: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> List[PipeRun]:
        """Extract pipe runs via skeletonization, junction analysis, and suppression."""
        if progress:
            progress("Skeletonizing P&ID pipe network...")

        H, W = img_bgr.shape[:2]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr

        # Adaptive thresholding to capture clean pipe strokes (tuned to blockSize=21, C=6 for faint CAD lines)
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 6
        )

        # Preserve one-pixel branches; opening erases them before graph tracing.
        clean_binary = binary.copy()

        # 1. Dilated Text Masking: Scaled with DPI (5px at 350 DPI)
        # to blackout notes/underlines while avoiding false gap creation in dense areas.
        # Bottom padding (dil_r * 2.5) is intentionally larger to catch thin text
        # underlines that survive suppress_text_artifacts (high aspect ratio → not
        # a "glyph", but still not a pipe). Without this, underlines get skeletonized
        # into false horizontal runs that pollute the pipe network.
        dil_r = max(3, int(5 * (dpi / 350.0)))
        dil_r_bottom = int(dil_r * 2.5)
        text_tokens = tokens or kwargs.get("pids") or []
        for t in text_tokens:
            if isinstance(t, dict):
                tx1 = int(t.get("x1", 0))
                ty1 = int(t.get("y1", 0))
                tx2 = int(t.get("x2", 0))
                ty2 = int(t.get("y2", 0))
            else:
                tx1 = int(getattr(t, "x1", 0))
                ty1 = int(getattr(t, "y1", 0))
                tx2 = int(getattr(t, "x2", 0))
                ty2 = int(getattr(t, "y2", 0))
            if tx2 > tx1 and ty2 > ty1:
                dil_x1 = max(0, tx1 - dil_r)
                dil_y1 = max(0, ty1 - dil_r)
                dil_x2 = min(W, tx2 + dil_r)
                dil_y2 = min(H, ty2 + dil_r_bottom)
                clean_binary[dil_y1:dil_y2, dil_x1:dil_x2] = 0

        # Keep an actual break at detected inline valves and instrument bubbles.
        # Physical gaps remain separate runs for manual correction.
        symbol_pad = max(1, int(round(dpi / 350.0)))
        for detection in detections or []:
            if detection.get("coarse") not in ("valve", "instrument"):
                continue
            x1 = max(0, int(detection.get("x1", 0)) - symbol_pad)
            y1 = max(0, int(detection.get("y1", 0)) - symbol_pad)
            x2 = min(W, int(detection.get("x2", 0)) + symbol_pad)
            y2 = min(H, int(detection.get("y2", 0)) + symbol_pad)
            if x2 > x1 and y2 > y1:
                clean_binary[y1:y2, x1:x2] = 0

        # Fast Trace intentionally skips YOLO, so use a conservative geometric
        # valve cue for raster pages: paired short diagonal strokes around a
        # compact valve body. Reuse the vector geometry classifier after mapping
        # Hough segments into PDF-point units; no model inference is introduced.
        try:
            hough_scale = min(1.0, 1800.0 / max(H, W))
            hough_image = clean_binary
            if hough_scale < 1.0:
                hough_image = cv2.resize(
                    clean_binary,
                    (max(1, int(round(W * hough_scale))), max(1, int(round(H * hough_scale)))),
                    interpolation=cv2.INTER_NEAREST,
                )
            hough_dpi = dpi * hough_scale
            hough = cv2.HoughLinesP(
                hough_image,
                rho=1,
                theta=np.pi / 180,
                threshold=max(8, int(2.0 * hough_dpi / 72.0)),
                minLineLength=max(6, int(4.0 * hough_dpi / 72.0)),
                maxLineGap=max(2, int(1.0 * hough_dpi / 72.0)),
            )
            if hough is not None:
                from .vector_tracer import _inline_symbol_regions

                scale_to_pt = 72.0 / hough_dpi
                scale_to_original_px = 72.0 / dpi
                hough_segments = []
                for line in hough:
                    coords = np.asarray(line).reshape(-1)
                    if coords.size < 4:
                        continue
                    hough_segments.append(
                        (
                            (float(coords[0]) * scale_to_pt, float(coords[1]) * scale_to_pt),
                            (float(coords[2]) * scale_to_pt, float(coords[3]) * scale_to_pt),
                        )
                    )
                for _orientation, sx1, sy1, sx2, sy2 in _inline_symbol_regions(hough_segments):
                    x1 = max(0, int(math.floor(sx1 / scale_to_original_px)))
                    y1 = max(0, int(math.floor(sy1 / scale_to_original_px)))
                    x2 = min(W, int(math.ceil(sx2 / scale_to_original_px)))
                    y2 = min(H, int(math.ceil(sy2 / scale_to_original_px)))
                    if x2 > x1 and y2 > y1:
                        clean_binary[y1:y2, x1:x2] = 0
        except (cv2.error, ImportError, ValueError):
            # Geometric masking is a conservative enhancement; a Hough failure
            # must not prevent the ordinary skeleton trace from running.
            pass

        # 2. Equipment Interior Masking: buang ISI equipment, PERTAHANKAN dinding + nozzle.
        #
        # Bug yang diperbaiki 2026-09-24: versi lama mem-blackout SELURUH bbox equipment
        # (hanya menyisakan inset 4px), padahal bbox detektor jauh lebih longgar daripada
        # badan alat - bbox 605-E-102 (674,769)-(999,1238) misalnya juga mencakup pipa
        # 10" di atasnya (y=799) dan instrumentasi di bawahnya. Akibatnya pipa nozzle
        # yang NYATA dan ber-label (terukur: pipa 10" 249px, 4" 249px, 2" 240px) lenyap
        # sebelum skeletonisasi - coverage 1.00 -> 0.00.
        #
        # Ganti pendekatan: di dalam bbox equipment, PERTAHANKAN hanya garis H/V panjang
        # yang MENYENTUH tepi bbox (dinding alat + nozzle yang menembus dinding), dan
        # buang sisanya (teks label, simbol instrumen, pengaduk). Cara ini tidak
        # bergantung pada bentuk alat (silinder, kubus, heat exchanger) sehingga tetap
        # benar walau bbox longgar.
        eq_margin = max(3, int(4 * (dpi / 350.0)))
        min_struct = max(24, int(60 * (dpi / 350.0)))   # ~7.6mm pada lembar 420mm
        for d in (detections or []):
            if d.get("coarse") != "equipment":
                continue
            ex1 = int(d.get("x1", 0)) + eq_margin
            ey1 = int(d.get("y1", 0)) + eq_margin
            ex2 = int(d.get("x2", 0)) - eq_margin
            ey2 = int(d.get("y2", 0)) - eq_margin
            if ex2 <= ex1 or ey2 <= ey1:
                continue
            roi = clean_binary[ey1:ey2, ex1:ex2]
            if not roi.any():
                continue
            # Garis panjang H/V (kandidat dinding / nozzle)
            horiz = cv2.morphologyEx(
                roi, cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (min_struct, 1)))
            vert = cv2.morphologyEx(
                roi, cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_struct)))
            keep = cv2.bitwise_or(horiz, vert)
            if not keep.any():
                roi[:] = 0
                continue
            # Hanya simpan yang MENYENTUH tepi bbox -> dinding & nozzle.
            # (Garis panjang di TENGAH alat, mis. batang pengaduk, tidak menyentuh
            #  tepi sehingga ikut terbuang - sesuai permintaan: jangan isi vessel.)
            n_lab, lab = cv2.connectedComponents((keep > 0).astype(np.uint8), 8)
            border_ids = set(lab[0, :].tolist()) | set(lab[-1, :].tolist())
            border_ids |= set(lab[:, 0].tolist()) | set(lab[:, -1].tolist())
            border_ids.discard(0)
            if border_ids:
                mask = np.isin(lab, list(border_ids))
            else:
                mask = np.zeros_like(keep, bool)
            roi[~mask] = 0

        # 3. Furniture Masking (title block, drawing border, notes tables)
        if furniture:
            for f in furniture:
                fx1, fy1, fx2, fy2 = f
                clean_binary[max(0, int(fy1)):min(H, int(fy2)), max(0, int(fx1)):min(W, int(fx2))] = 0

        # 3b. Text-artifact island suppression (pre-skeletonization).
        # OCR bounding-box masking is leaky: single characters that OCR fails to label
        # (size fractions "3/4", notes "BY INSTR", hand scratches) survive as ink and get
        # skeletonized into garbage traces. This geometry-based pass blackouts compact
        # glyph-like islands while preserving thin, elongated pipe stubs.
        if self.suppress_text_artifacts:
            protect_boxes = [
                (int(d.get("x1", 0)), int(d.get("y1", 0)), int(d.get("x2", 0)), int(d.get("y2", 0)))
                for d in (detections or [])
                if d.get("coarse") in ("equipment", "valve", "instrument")
            ]
            clean_binary = suppress_text_artifacts(
                clean_binary,
                detections=detections,
                dpi=dpi,
                tokens=tokens,
                max_side_pt=self.text_artifact_max_side_pt,
                max_area_pt2=self.text_artifact_max_area_pt2,
                max_aspect=self.text_artifact_max_aspect,
                protect_boxes=protect_boxes,
            )

        # 4. Skeletonize
        skel = _morphological_skeleton(clean_binary)

        # 5. Extract graph segments and resolve junctions (crossovers vs T-junctions)
        min_len = int(self.min_length_px * (dpi / 350.0))
        raw_runs = _graph_segments(skel, min_len)

        # 6. Apply standard suppressions (symbol edges, equipment interiors, furniture)
        filtered = suppress_box_edges(raw_runs, detections or [])
        filtered = suppress_equipment_interior(
            filtered, detections or [], margin_pt=3, page_wh=(W, H)
        )
        # Snap endpoints to equipment perimeter (Phase B.5)
        filtered = snap_endpoints_to_equipment(
            filtered, detections or [], snap_radius_pt=14, dpi=dpi
        )
        if furniture:
            filtered = suppress_furniture(filtered, furniture)

        boxes = detect_boxes(img_bgr)
        if boxes:
            # detections diperlukan agar pipa yang tersambung ke valve/instrument
            # TIDAK ikut terbuang (guard di dalam suppress_box_outlines).
            filtered = suppress_box_outlines(filtered, boxes, detections=detections)

        # Drafting suppressions & header continuity
        filtered = suppress_drawing_margins(filtered, page_wh=(W, H))
        # Phase 1 — 5 golden-ratio geometry rules (anti table/frame leak).
        # Removes paper border, title block, NOTES tables and coordinate ticks that
        # survived into runs; real pipes (detection-attached or long headers) are guarded.
        filtered = suppress_furniture_geometry(
            filtered, page_wh=(W, H), detections=detections, furniture=furniture,
            label_boxes=pids or kwargs.get("pids"), dpi=dpi
        )
        filtered = suppress_revision_clouds(filtered)
        filtered = suppress_diagonal_artifacts(filtered, page_wh=(W, H))
        # Buang garis lurus PALSU hasil skeletonisasi yang melintasi kertas kosong
        # (mayoritas titik sampelnya tidak menyentuh tinta). Terukur: satu run
        # 1017 px di lembar referensi hanya 20% tinta — bukan pipa, bukan gambar.
        filtered = suppress_low_ink_diagonals(filtered, img_bgr=gray)
        # Keep physical gaps at valves and other symbols. HITL tools can join
        # fragments explicitly when the drawing calls for it.

        # 8. Post-filter: drop isolated short diagonal strokes (surviving text/hand scratches)
        #    that do not attach to any detected symbol. No-op when detections is empty.
        if self.suppress_floating_stubs:
            filtered = suppress_floating_stubs(
                filtered, detections=detections, page_wh=(W, H), dpi=dpi,
                max_len_px=self.floating_stub_max_len_px,
            )

        # Keep T-junction legs independently markable.
        return split_runs_at_t_junctions(
            filtered,
            tol_px=max(2.0, 2.0 * dpi / 350.0),
            min_piece_px=max(6.0, 6.0 * dpi / 350.0),
        )
