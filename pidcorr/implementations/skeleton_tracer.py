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
    suppress_furniture,
    suppress_box_outlines,
    detect_boxes,
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

        # Order edge pixels from p0 to p1
        vx, vy = p1[0] - p0[0], p1[1] - p0[1]
        vlen = math.hypot(vx, vy)
        if vlen > 0:
            nx, ny = vx / vlen, vy / vlen
            pts.sort(key=lambda pt: (pt[0] - p0[0]) * nx + (pt[1] - p0[1]) * ny)
        ordered_points = [p0] + [(int(x), int(y)) for x, y in pts[::max(1, len(pts)//15)]] + [p1]

        # Simplify collinear points along edge
        pts_arr = np.array(ordered_points, dtype=np.int32).reshape((-1, 1, 2))
        approx = cv2.approxPolyDP(pts_arr, epsilon=2.0, closed=False)
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
    components_with_edges = set(component_labels[edge_labels > 0])

    for component_id in range(1, component_count):
        if component_id in components_with_edges:
            continue
        if component_stats[component_id, cv2.CC_STAT_AREA] < min_length:
            continue
        bx, by, bw, bh, _ = component_stats[component_id]
        sub_ys, sub_xs = np.where(component_labels[by:by + bh, bx:bx + bw] == component_id)
        xs = sub_xs + bx
        ys = sub_ys + by
        points = np.column_stack((xs.astype(float), ys.astype(float)))
        centered = points - points.mean(axis=0)
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        direction = vh[0]
        projection = centered @ direction
        p0 = points[projection.argmin()]
        p1 = points[projection.argmax()]
        dx, dy = abs(p1[0] - p0[0]), abs(p1[1] - p0[1])
        axis = "h" if dx >= 3 * dy else ("v" if dy >= 3 * dx else "d")
        runs.append(PipeRun(
            points=[[int(round(p0[0])), int(round(p0[1]))], [int(round(p1[0])), int(round(p1[1]))]],
            axis=axis,
            underline=False,
        ))

    return runs


class SkeletonLineTracer(BaseLineTracer):
    """Graph-based skeletonization line tracer with crossover vs T-junction classification."""

    def __init__(self, min_length_px: int = 40):
        self.min_length_px = min_length_px

    def trace(
        self,
        img_bgr: np.ndarray,
        dpi: int = 350,
        detections: Optional[List[Dict[str, Any]]] = None,
        furniture: Optional[List[List[int]]] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> List[PipeRun]:
        """Extract pipe runs via skeletonization, junction analysis, and suppression."""
        if progress:
            progress("Skeletonizing P&ID pipe network...")

        H, W = img_bgr.shape[:2]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr

        # Adaptive thresholding to capture clean pipe strokes
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 8
        )

        # Remove very small noise dots
        clean_binary = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        )

        # 1. Skeletonize
        skel = _morphological_skeleton(clean_binary)

        # 2. Extract graph segments and resolve junctions (crossovers vs T-junctions)
        min_len = int(self.min_length_px * (dpi / 350.0))
        raw_runs = _graph_segments(skel, min_len)

        # 3. Apply standard suppressions (symbol edges, equipment interiors, furniture)
        filtered = suppress_box_edges(raw_runs, detections or [])
        filtered = suppress_equipment_interior(
            filtered, detections or [], margin_pt=10, page_wh=(W, H)
        )
        if furniture:
            filtered = suppress_furniture(filtered, furniture)

        boxes = detect_boxes(img_bgr)
        if boxes:
            filtered = suppress_box_outlines(filtered, boxes)

        return filtered
