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
    snap_t_junctions,
    suppress_box_outlines,
    detect_boxes,
    suppress_drawing_margins,
    suppress_page_frame,
    suppress_revision_clouds,
    suppress_diagonal_artifacts,
    suppress_text_artifacts,
    suppress_floating_stubs,
    bridge_collinear_headers,
    bridge_inline_valve_gaps,
    chain_collinear_segments,
    equipment_outline_protect_mask,
    tag_equipment_outlines,
    bridge_piecemeal_gaps,
    bridge_equipment_outline_fragments,
)


def _morphological_skeleton(binary_img: np.ndarray) -> np.ndarray:
    """Fast morphological skeletonization using an 8-connected kernel."""
    skel = np.zeros(binary_img.shape, dtype=np.uint8)
    element = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    temp = np.empty(binary_img.shape, dtype=np.uint8)
    img = binary_img.copy()

    while True:
        cv2.morphologyEx(img, cv2.MORPH_OPEN, element, temp)
        cv2.bitwise_not(temp, temp)
        cv2.bitwise_and(img, temp, temp)
        cv2.bitwise_or(skel, temp, skel)
        cv2.erode(img, element, img)
        if cv2.countNonZero(img) == 0:
            break

    return skel


def _find_junctions_and_endpoints(skel: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Classify skeleton pixels by 8-connected degree."""
    kernel = np.array([
        [1, 1, 1],
        [1, 0, 1],
        [1, 1, 1]
    ], dtype=np.uint8)

    skel_binary = (skel > 0).astype(np.uint8)
    neighbor_count = cv2.filter2D(skel_binary, -1, kernel) * skel_binary

    endpoints = (neighbor_count == 1).astype(np.uint8)
    t_junctions = (neighbor_count == 3).astype(np.uint8)
    crossovers = (neighbor_count >= 4).astype(np.uint8)

    return endpoints, t_junctions, crossovers


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
    transitions = sum(
        (neighbours[i] != neighbours[(i + 1) % 8]).astype(np.uint8)
        for i in range(8)
    ) // 2
    node_pixels = ((ink > 0) & ((transitions == 1) | (transitions >= 3))).astype(np.uint8)

    # Merge adjacent node pixels into node clusters
    node_pixels = cv2.dilate(node_pixels, np.ones((3, 3), np.uint8), iterations=1) & ink
    node_count, node_labels, node_stats, node_centroids = cv2.connectedComponentsWithStats(
        node_pixels, 8
    )
    if node_count <= 1:
        return []

    graph_body = ink.copy()
    graph_body[node_pixels > 0] = 0
    edge_count, edge_labels, edge_stats, _ = cv2.connectedComponentsWithStats(graph_body, 8)

    dilated_nodes = cv2.dilate(node_pixels, np.ones((3, 3), np.uint8))
    touch_mask = (edge_labels > 0) & (dilated_nodes > 0)
    e_touch = edge_labels[touch_mask]

    edge_adjacent: Dict[int, set] = defaultdict(set)
    padded_nodes = np.pad(node_labels, 1, mode="constant", constant_values=0)
    shifts = [
        padded_nodes[:-2, 1:-1], padded_nodes[2:, 1:-1],
        padded_nodes[1:-1, :-2], padded_nodes[1:-1, 2:],
        padded_nodes[:-2, :-2], padded_nodes[:-2, 2:],
        padded_nodes[2:, :-2], padded_nodes[2:, 2:],
    ]
    for s in shifts:
        s_touch = s[touch_mask]
        valid = s_touch > 0
        if np.any(valid):
            ev = e_touch[valid]
            nv = s_touch[valid]
            unq = np.unique(np.column_stack((ev, nv)), axis=0)
            for e_id, n_id in unq:
                edge_adjacent[int(e_id)].add(int(n_id))

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

        # Order edge pixels from p0 to p1
        vx, vy = p1[0] - p0[0], p1[1] - p0[1]
        vlen = math.hypot(vx, vy)
        if vlen > 0:
            nx, ny = vx / vlen, vy / vlen
            pts.sort(key=lambda pt: (pt[0] - p0[0]) * nx + (pt[1] - p0[1]) * ny)
        ordered_points = [p0] + [(int(x), int(y)) for x, y in pts[::max(1, len(pts)//15)]] + [p1]

        # Simplify collinear points along edge. For STRAIGHT edges a coarse epsilon (1.5) is
        # ideal: the skeleton is a 1px staircase and we want clean orthogonal runs. But
        # vessel/equipment cylinder outlines (ellipse/arc boundaries) live in the SAME skeleton:
        # a coarse epsilon turns their smooth curve into a stiff zig-zag. So we branch: if the
        # edge is a genuine curve (its ordered pixels bow away from the p0->p1 chord), simplify
        # with a much finer epsilon (0.5-0.8) to keep the arc smooth and vertex-dense.
        pts_all = [(int(x), int(y)) for x, y in pts]
        chord_len = max(1.0, math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
        curve_bow = 0.0
        if len(pts_all) >= 3:
            ux, uy = (p1[0] - p0[0]) / chord_len, (p1[1] - p0[1]) / chord_len
            for qx, qy in pts_all[:: max(1, len(pts_all) // 24)]:
                # perpendicular distance from the p0->p1 chord
                perp = abs((qx - p0[0]) * (-uy) + (qy - p0[1]) * ux)
                curve_bow = max(curve_bow, perp)
        is_curved = curve_bow > max(1.5, 0.035 * chord_len)
        eps = 0.6 if is_curved else 1.5
        pts_arr = np.array(ordered_points, dtype=np.int32).reshape((-1, 1, 2))
        approx = cv2.approxPolyDP(pts_arr, epsilon=eps, closed=False)
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

        jtype, through_pairs, _ = _classify_junction_geometry(node_id, (cx, cy), incident_info)
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

        tot_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(chain, chain[1:]))
        if tot_len < min_length:
            continue

        dx = abs(chain[-1][0] - chain[0][0])
        dy = abs(chain[-1][1] - chain[0][1])
        axis = "h" if dx >= 3 * dy else ("v" if dy >= 3 * dx else "d")
        runs.append(PipeRun(points=chain, axis=axis, underline=False))

    # Diagonal PCA staircase fallback for isolated diagonal strokes
    component_count, component_labels, component_stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    components_with_edges = set(np.unique(component_labels[edge_labels > 0]))
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
        
        # Closed-form 2x2 covariance eigenvalues (100x faster than full SVD)
        # C = X^T X = [[a, b], [b, c]]
        a = float(np.sum(centered[:, 0] ** 2))
        b = float(np.sum(centered[:, 0] * centered[:, 1]))
        c = float(np.sum(centered[:, 1] ** 2))
        tr = a + c
        delta = math.sqrt(max(0.0, (a - c) ** 2 + 4.0 * b * b))
        lam1 = (tr + delta) * 0.5
        lam2 = max(0.0, (tr - delta) * 0.5)

        # Check linearity: true straight stroke must have low perpendicular variance (s1/s0 <= 0.12 => lam2/lam1 <= 0.0144)
        if lam1 <= 1e-6 or (lam2 / lam1) > 0.0144:
            continue

        if abs(b) > 1e-6:
            v_raw = (b, lam1 - a)
            v_len = math.hypot(v_raw[0], v_raw[1])
            direction = np.array([v_raw[0] / v_len, v_raw[1] / v_len])
        else:
            direction = np.array([1.0, 0.0]) if a >= c else np.array([0.0, 1.0])

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
                 text_artifact_max_side_pt: float = 9.3,
                 text_artifact_max_area_pt2: float = 25.5,
                 text_artifact_max_aspect: float = 3.5,
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
        progress: Optional[Callable[[str], None]] = None,
        binary_img: Optional[np.ndarray] = None,
        roi: bool = False,
        **kwargs,
    ) -> List[PipeRun]:
        """Extract pipe runs via skeletonization, junction analysis, and suppression.

        Parameters
        ----------
        binary_img : np.ndarray, optional
            Pra-binarisasi (BINARY_INV, ink=255) yang SUDAH dihitung pada resolusi penuh.
            Jika diberikan, adaptive threshold TIDAK dijalankan ulang. Ini penting untuk
            ROI re-scan: crop sub-image yang didominasi background putih tidak memiliki
            statistik lokal yang cukup sehingga adaptive threshold lokal rusak dan stroke
            pipa tipis hilang.
        roi : bool
            Mode ROI re-scan. Menyambungkan kembali piksel pipa yang berlubang karena
            masking teks OCR di sekitar kotak (MORPH_CLOSE kernel 3x3) dan memakai
            min_length_px adaptif.
        """
        if progress:
            progress("Skeletonizing P&ID pipe network...")

        H, W = img_bgr.shape[:2]

        if binary_img is not None:
            # Region/whole-image path: reuse the already-computed full-resolution binary.
            # Never re-run adaptive thresholding on a cropped sub-image (local statistics
            # collapse under white-background domination -> thin pipe strokes vanish).
            binary = binary_img
            if binary.shape[:2] != (H, W):
                binary = cv2.resize(binary, (W, H), interpolation=cv2.INTER_NEAREST)
        else:
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
            # Adaptive thresholding to capture clean pipe strokes (tuned to blockSize=21, C=6 for faint CAD lines)
            binary = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 6
            )

        # Remove very small noise dots
        clean_binary = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        )

        # ROI bridge: reconnect pipe pixels that OCR masking punched through near the box
        # so a line crossing a masked label does not shatter into disconnected fragments.
        if roi:
            clean_binary = cv2.morphologyEx(
                clean_binary, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            )

        # 1. Dilated Text Masking: Scaled with DPI (5px at 350 DPI)
        # to blackout notes/underlines while avoiding false gap creation in dense areas
        dil_r = max(3, int(5 * (dpi / 350.0)))
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
                dil_y2 = min(H, ty2 + dil_r)
                clean_binary[dil_y1:dil_y2, dil_x1:dil_x2] = 0

        # 2. Equipment Interior Masking: Blackout the chest of equipment boxes (vessels, tanks,
        #    columns) so their DENSE interior art/text does not become pipe runs. BUT the tool's
        #    own OUTLINE must survive: if the detection box is slightly offset (common) or the
        #    vessel is drawn as a thin-walled tube (dome + two walls), zeroing the whole box would
        #    delete the vessel outline itself. So we compute an OUTLINE-PROTECT mask (long thin
        #    strokes + curved arcs inside each equipment box) and subtract it from the blackout.
        eq_margin = max(3, int(4 * (dpi / 350.0)))
        equip_dets = [d for d in (detections or []) if d.get("coarse") == "equipment"]
        outline_mask = None
        tight_dets = list(detections or [])
        inst_boxes = []
        if equip_dets:
            outline_mask, tight_boxes = equipment_outline_protect_mask(
                clean_binary, equip_dets, dpi=dpi, eq_margin_px=eq_margin, return_tight=True
            )
            # Tighten equipment boxes to the actual silhouette bbox (union of protected
            # outline components). The raw YOLO box is often much wider than the tool
            # (vessel 605-V-221-B: box (1362,846,1921,1657) vs silhouette x≈1393..1663),
            # so using the raw box for interior blackout / interior clipping would erase
            # REAL nozzle pipes outside the tool (N6A/N6B/N4 on the right wall).
            tight_iter = iter(tight_boxes)
            new_dets = []
            for d in (detections or []):
                if d.get("coarse") == "equipment":
                    tb = next(tight_iter, None)
                    if tb is not None:
                        d = {**d, "x1": tb[0], "y1": tb[1], "x2": tb[2], "y2": tb[3]}
                new_dets.append(d)
            tight_dets = new_dets
            interior_blackout = np.zeros_like(clean_binary)
            for (ex1, ey1, ex2, ey2) in tight_boxes:
                ex1 += eq_margin; ey1 += eq_margin
                ex2 -= eq_margin; ey2 -= eq_margin
                if ex2 > ex1 and ey2 > ey1:
                    interior_blackout[ey1:ey2, ex1:ex2] = 255
            # keep only the interior portion that is NOT part of the preserved outline
            interior_blackout[outline_mask > 0] = 0
            clean_binary[interior_blackout > 0] = 0

        # 2b. Instrument bubble blackout: process pipes STOP at instruments and resume
        #     after them (user request: "kalo ada instrumen dia gk garisi dan lanjut lagi
        #     setelah instrumen"). Bridging passes receive `inst_boxes` so the gap is
        #     never re-bridged. Valve symbols are NOT blacked out (they get bridged).
        inst_dets = [d for d in (detections or []) if d.get("coarse") == "instrument"]
        if inst_dets:
            inst_blk = np.zeros_like(clean_binary)
            for d in inst_dets:
                ix1 = int(d.get("x1", 0)) - 1
                iy1 = int(d.get("y1", 0)) - 1
                ix2 = int(d.get("x2", 0)) + 1
                iy2 = int(d.get("y2", 0)) + 1
                ix1 = max(0, ix1); iy1 = max(0, iy1)
                ix2 = min(W, ix2); iy2 = min(H, iy2)
                if ix2 > ix1 and iy2 > iy1:
                    inst_blk[iy1:iy2, ix1:ix2] = 255
                    inst_boxes.append((ix1, iy1, ix2, iy2))
        # 2c. Collect block boxes that bridging passes must NOT cross.
        # Pada mode ROI re-scan, bodi valve juga dimasukkan ke block_boxes agar bridging passes
        # apa pun (collinear headers, piecemeal, chain collinear) tidak menyeberang menembus valve.
        block_boxes = list(inst_boxes)
        if roi and detections:
            for d in detections:
                if d.get("coarse") == "valve":
                    vx1 = max(0, int(d.get("x1", 0)))
                    vy1 = max(0, int(d.get("y1", 0)))
                    vx2 = min(W, int(d.get("x2", 0)))
                    vy2 = min(H, int(d.get("y2", 0)))
                    if vx2 > vx1 and vy2 > vy1:
                        block_boxes.append((vx1, vy1, vx2, vy2))

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
        # In ROI mode the selection box is small; shrink the minimum run length so short but
        # genuine pipe fragments inside the box are kept, while never exceeding the configured
        # page-level minimum (min(8, min_length_px) per task spec).
        effective_min = self.min_length_px
        if roi:
            effective_min = min(8, self.min_length_px)
        min_len = max(2, int(effective_min * (dpi / 350.0)))
        raw_runs = _graph_segments(skel, min_len)

        # 5b. Tag equipment-outline runs (vessel/tangki kontur) BEFORE suppression passes so
        #     that interior-clipping does not destroy the tool's own outline. They remain in
        #     the run list so the engineer can label/split them manually.
        raw_runs = tag_equipment_outlines(raw_runs, tight_dets, page_wh=(W, H),
                                          protect_mask=outline_mask)

        # 6. Apply standard suppressions (symbol edges, equipment interiors, furniture)
        #    Instruments are excluded from box-edge suppression: their small bbox + wide
        #    edge band would drop real nozzle stubs next to the bubble (they are handled
        #    by the blackout above instead).
        box_edge_dets = [d for d in tight_dets if d.get("coarse") != "instrument"]
        filtered = suppress_box_edges(raw_runs, box_edge_dets)
        filtered = suppress_equipment_interior(
            filtered, tight_dets, margin_pt=3, page_wh=(W, H)
        )
        # Snap endpoints to equipment perimeter (Phase B.5); ray-snap to the real wall
        # ink via the outline mask so endpoints never get pulled to the raw box edge.
        filtered = snap_endpoints_to_equipment(
            filtered, tight_dets, snap_radius_pt=14, dpi=dpi, snap_mask=outline_mask
        )
        if furniture:
            filtered = suppress_furniture(filtered, furniture)

        boxes = detect_boxes(img_bgr)
        if boxes:
            filtered = suppress_box_outlines(filtered, boxes)

        # Drafting suppressions & header continuity
        filtered = suppress_drawing_margins(filtered, page_wh=(W, H))
        # Frame guard: buang bingkai gambar (polyline yang menelusuri 3-4 sisi kertas)
        # yang lolos filter margin di atas karena vertex-nya menyentuh 2 dimensi.
        filtered = suppress_page_frame(filtered, page_wh=(W, H))
        filtered = suppress_revision_clouds(filtered)
        filtered = suppress_diagonal_artifacts(filtered, page_wh=(W, H))
        filtered = bridge_collinear_headers(filtered, max_gap_px=55, block_boxes=block_boxes)

        # 7. Bridge pipe runs cut by inline valves (relaxed: containment + 20deg collinearity, 115px gap)
        # Pada mode ROI re-scan, pipa harus berhenti rapi di port valve, bukan menembus bodinya.
        if not roi:
            filtered = bridge_inline_valve_gaps(filtered, detections=detections, max_gap_px=115,
                                                block_boxes=block_boxes)

        # 7a. Chain collinear segments whose ends sit within a tiny residual crack
        #     (<= 15px) and are NOT a T-branch point, fusing one physical pipe that the
        #     skeletonizer fractured into several runs along a single transmission path.
        filtered = chain_collinear_segments(
            filtered, max_gap_px=max(12, int(15 * (dpi / 350.0))), tol_px=6,
            block_boxes=block_boxes,
        )

        # 7a-bis. Piecemeal bridge: fragment pendek hasil skeletonisasi yang tidak nyambung
        # ke graf sering gugur `min_length` -> pipa panjang HILANG. Sambung agresif (celah
        # sampai ~48px, sudut longgar) dengan mengutamakan fragmen pendek sebagai jembatan.
        filtered = bridge_piecemeal_gaps(
            filtered,
            max_gap_px=max(24, int(48 * (dpi / 350.0))),
            tol_px=max(6, int(8 * (dpi / 350.0))),
            angle_tol_deg=35.0,
            short_len_px=max(24, int(40 * (dpi / 350.0))),
            block_boxes=block_boxes,
        )

        # 7b. T-junction orthogonal snap: pull dead-end endpoints onto the body of a
        #     perpendicular run so branches meet their header without a 5-15px gap.
        filtered = snap_t_junctions(filtered, near_px=max(12, int(16 * (dpi / 350.0))))

        # 8. Post-filter: drop isolated short diagonal strokes (surviving text/hand scratches)
        #    that do not attach to any detected symbol. No-op when detections is empty.
        if self.suppress_floating_stubs:
            filtered = suppress_floating_stubs(
                filtered, detections=detections, page_wh=(W, H), dpi=dpi,
                max_len_px=self.floating_stub_max_len_px,
            )

        # 9. (final) Re-tag equipment-outline runs in case merging/bridging created new
        #    geometry; keeps the flag authoritative for downstream consumers.
        filtered = tag_equipment_outlines(filtered, tight_dets, page_wh=(W, H),
                                          protect_mask=outline_mask)

        # 10. Re-join outline fragments into ONE closed polyline per tool (user request:
        #     vessel outline = 1 closed polyline; engineer can split it later). Grouped
        #     per equipment box so fragments of different tools never merge.
        if outline_mask is not None:
            filtered = bridge_equipment_outline_fragments(
                filtered, detections=tight_dets,
                max_gap_px=max(30, int(90 * (dpi / 350.0)))
            )

        return filtered
