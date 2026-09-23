"""Per-stage profiling of SkeletonLineTracer.trace() pipeline.

This script instruments every major stage inside trace() and prints a breakdown
table. It reuses the existing _diag_trace_only.py caching for OCR/YOLO/furniture.

Usage (inside api container):
    python _diag_profile.py "Contoh P&ID/BCD4-605-42-PID-3-019-02 Rev.1-CCD2.png"
"""
import sys, os, json, hashlib, time, math
import cv2, numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "Contoh P&ID/BCD4-605-42-PID-3-019-02 Rev.1-CCD2.png"
args = sys.argv[2:]
rebuild = "--rebuild" in args

# --- Load or build cache (same as _diag_trace_only.py) ---
from pidcorr.factory import get_configured_orchestrator
from pidcorr.implementations.skeleton_tracer import (
    SkeletonLineTracer, _morphological_skeleton, _graph_segments,
)
from pidcorr.lines import (
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

img = cv2.imread(path)
if img is None:
    print("cannot read", path); sys.exit(1)
H, W = img.shape[:2]
dpi = 350

h = hashlib.md5(open(path, "rb").read()).hexdigest()[:12]
cache_f = f"_cache_{h}.json"

cache = None
if os.path.exists(cache_f) and not rebuild:
    try:
        cache = json.load(open(cache_f))
        print(f"[cache hit] {cache_f}")
    except Exception as e:
        print("cache load failed:", e)

if cache is None:
    print("[cache miss] running stages 1-3... this takes ~10min")
    t0 = time.time()
    orc = get_configured_orchestrator()
    pids, tokens = orc.extractor.extract(img_bgr=img, tile=1200, progress=None)
    syms = orc.detector.detect(img_bgr=img, conf=0.30, progress=None)
    try:
        from pidcorr.layout import detect_furniture
        furniture = detect_furniture(img, orc.layout_weights)
    except Exception:
        furniture = []
    for s in syms:
        if s.get("coarse") == "valve" and not s.get("subtype"):
            try: s["subtype"] = orc.classifier.classify_valve(img, s)
            except Exception: pass
        elif s.get("coarse") == "instrument" and not s.get("subtype"):
            try:
                func, loop = orc.classifier.classify_instrument(img, s)
                if func: s["subtype"] = func; s["isa_func"] = func; s["isa_loop"] = loop
            except Exception: pass
    def _pid_dump(p):
        if isinstance(p, dict): return dict(p)
        return {k: getattr(p, k, "") for k in ("pid","x1","y1","x2","y2","unit","size","fluid","pclass","seq","conf")}
    cache = {"pids": [_pid_dump(p) for p in pids], "tokens": tokens, "syms": syms, "furniture": furniture}
    json.dump(cache, open(cache_f, "w"))
    print(f"[cache miss] stages 1-3 done in {time.time()-t0:.0f}s")

tokens = cache["tokens"]; syms = cache["syms"]; furniture = cache["furniture"]
print(f"cached: tokens={len(tokens)} syms={len(syms)} furniture={len(furniture)}")

# === PROFILED TRACE ===
timings = []
def _tick(label):
    timings.append((label, time.perf_counter()))

_tick("START")

# --- 0. Binarization ---
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 6)
clean_binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
_tick("0_binarization")

# --- 1. Text masking ---
dil_r = max(3, int(5 * (dpi / 350.0)))
text_tokens = tokens or []
for t in text_tokens:
    tx1 = int(t.get("x1", 0)); ty1 = int(t.get("y1", 0))
    tx2 = int(t.get("x2", 0)); ty2 = int(t.get("y2", 0))
    if tx2 > tx1 and ty2 > ty1:
        clean_binary[max(0,ty1-dil_r):min(H,ty2+dil_r), max(0,tx1-dil_r):min(W,tx2+dil_r)] = 0
_tick("1_text_masking")

# --- 2. Equipment outline protect mask + interior blackout ---
eq_margin = max(3, int(4 * (dpi / 350.0)))
equip_dets = [d for d in syms if d.get("coarse") == "equipment"]
outline_mask = None
tight_dets = list(syms)
inst_boxes = []
tight_boxes_list = []
if equip_dets:
    outline_mask, tight_boxes = equipment_outline_protect_mask(
        clean_binary, equip_dets, dpi=dpi, eq_margin_px=eq_margin, return_tight=True
    )
    tight_boxes_list = tight_boxes
    tight_iter = iter(tight_boxes)
    new_dets = []
    for d in syms:
        if d.get("coarse") == "equipment":
            tb = next(tight_iter, None)
            if tb is not None:
                d = {**d, "x1": tb[0], "y1": tb[1], "x2": tb[2], "y2": tb[3]}
        new_dets.append(d)
    tight_dets = new_dets
    interior_blackout = np.zeros_like(clean_binary)
    for (ex1, ey1, ex2, ey2) in tight_boxes:
        ex1 += eq_margin; ey1 += eq_margin; ex2 -= eq_margin; ey2 -= eq_margin
        if ex2 > ex1 and ey2 > ey1:
            interior_blackout[ey1:ey2, ex1:ex2] = 255
    interior_blackout[outline_mask > 0] = 0
    clean_binary[interior_blackout > 0] = 0
_tick("2_equip_outline_protect_mask")

# --- 2b. Instrument blackout ---
inst_dets = [d for d in syms if d.get("coarse") == "instrument"]
if inst_dets:
    for d in inst_dets:
        ix1 = max(0, int(d.get("x1",0))-1); iy1 = max(0, int(d.get("y1",0))-1)
        ix2 = min(W, int(d.get("x2",0))+1); iy2 = min(H, int(d.get("y2",0))+1)
        if ix2 > ix1 and iy2 > iy1:
            clean_binary[iy1:iy2, ix1:ix2] = 0
            inst_boxes.append((ix1, iy1, ix2, iy2))
block_boxes = list(inst_boxes)
_tick("2b_instrument_blackout")

# --- 3. Furniture masking ---
if furniture:
    for f in furniture:
        fx1, fy1, fx2, fy2 = f
        clean_binary[max(0,int(fy1)):min(H,int(fy2)), max(0,int(fx1)):min(W,int(fx2))] = 0
_tick("3_furniture_masking")

# --- 3b. Text-artifact suppression ---
protect_boxes = [
    (int(d.get("x1",0)), int(d.get("y1",0)), int(d.get("x2",0)), int(d.get("y2",0)))
    for d in syms if d.get("coarse") in ("equipment","valve","instrument")
]
clean_binary = suppress_text_artifacts(
    clean_binary, detections=syms, dpi=dpi, tokens=tokens,
    max_side_pt=9.3, max_area_pt2=25.5, max_aspect=3.5,
    protect_boxes=protect_boxes,
)
_tick("3b_text_artifact_suppress")

# --- 4. Skeletonize ---
skel = _morphological_skeleton(clean_binary)
_tick("4_skeletonize")

# --- 5. Graph segments + junction classification ---
min_len = max(2, int(12 * (dpi / 350.0)))
raw_runs = _graph_segments(skel, min_len)
_tick("5_graph_segments")
n_raw = len(raw_runs)

# --- 5b. Tag equipment outlines ---
raw_runs = tag_equipment_outlines(raw_runs, tight_dets, page_wh=(W, H), protect_mask=outline_mask)
_tick("5b_tag_equip_outlines")

# --- 6. suppress_box_edges ---
box_edge_dets = [d for d in tight_dets if d.get("coarse") != "instrument"]
filtered = suppress_box_edges(raw_runs, box_edge_dets)
_tick("6a_suppress_box_edges")
n_after_box = len(filtered)

# suppress_equipment_interior
filtered = suppress_equipment_interior(filtered, tight_dets, margin_pt=3, page_wh=(W, H))
_tick("6b_suppress_equip_interior")

# snap_endpoints_to_equipment
filtered = snap_endpoints_to_equipment(filtered, tight_dets, snap_radius_pt=14, dpi=dpi, snap_mask=outline_mask)
_tick("6c_snap_endpoints_to_equipment")

# suppress_furniture
if furniture:
    filtered = suppress_furniture(filtered, furniture)
_tick("6d_suppress_furniture")

# detect_boxes + suppress_box_outlines
boxes = detect_boxes(img)
if boxes:
    filtered = suppress_box_outlines(filtered, boxes)
_tick("6e_detect_suppress_box_outlines")

# suppress_drawing_margins
filtered = suppress_drawing_margins(filtered, page_wh=(W, H))
_tick("6f_suppress_drawing_margins")

# suppress_page_frame
filtered = suppress_page_frame(filtered, page_wh=(W, H))
_tick("6g_suppress_page_frame")

# suppress_revision_clouds
filtered = suppress_revision_clouds(filtered)
_tick("6h_suppress_revision_clouds")

# suppress_diagonal_artifacts
filtered = suppress_diagonal_artifacts(filtered, page_wh=(W, H))
_tick("6i_suppress_diagonal_artifacts")

# --- 7. bridge_collinear_headers ---
filtered = bridge_collinear_headers(filtered, max_gap_px=55, block_boxes=block_boxes)
_tick("7a_bridge_collinear_headers")

# --- 7. bridge_inline_valve_gaps ---
filtered = bridge_inline_valve_gaps(filtered, detections=syms, max_gap_px=115, block_boxes=block_boxes)
_tick("7b_bridge_inline_valve_gaps")

# --- 7a. chain_collinear_segments ---
filtered = chain_collinear_segments(
    filtered, max_gap_px=max(12, int(15*(dpi/350.0))), tol_px=6, block_boxes=block_boxes,
)
_tick("7c_chain_collinear_segments")

# --- 7a-bis. bridge_piecemeal_gaps ---
filtered = bridge_piecemeal_gaps(
    filtered,
    max_gap_px=max(24, int(48*(dpi/350.0))),
    tol_px=max(6, int(8*(dpi/350.0))),
    angle_tol_deg=35.0,
    short_len_px=max(24, int(40*(dpi/350.0))),
    block_boxes=block_boxes,
)
_tick("7d_bridge_piecemeal_gaps")

# --- 7b. snap_t_junctions ---
filtered = snap_t_junctions(filtered, near_px=max(12, int(16*(dpi/350.0))))
_tick("7e_snap_t_junctions")

# --- 8. suppress_floating_stubs ---
filtered = suppress_floating_stubs(filtered, detections=syms, page_wh=(W, H), dpi=dpi, max_len_px=28.0)
_tick("8_suppress_floating_stubs")

# --- 9. Re-tag equipment outlines ---
filtered = tag_equipment_outlines(filtered, tight_dets, page_wh=(W, H), protect_mask=outline_mask)
_tick("9_retag_equip_outlines")

# --- 10. bridge_equipment_outline_fragments ---
if outline_mask is not None:
    filtered = bridge_equipment_outline_fragments(
        filtered, detections=tight_dets, max_gap_px=max(30, int(90*(dpi/350.0)))
    )
_tick("10_bridge_equip_outline_frags")

n_final = len(filtered)
n_eq = sum(1 for r in filtered if (getattr(r, "equipment_outline", False) if hasattr(r, "equipment_outline") else r.get("equipment_outline", False)))

# === PRINT REPORT ===
print("\n" + "="*80)
print("PROFILING REPORT — SkeletonLineTracer.trace() pipeline")
print(f"Image: {path}  ({W}x{H} @ {dpi}dpi)")
print(f"Detections: {len(syms)} symbols, {len(tokens)} tokens, {len(furniture)} furniture")
print("="*80)
print(f"{'Stage':<45} {'Time (ms)':>10} {'Cum (ms)':>10} {'%':>6}")
print("-"*80)

t0_abs = timings[0][1]
total = timings[-1][1] - t0_abs
for i in range(1, len(timings)):
    label = timings[i][0]
    dt = (timings[i][1] - timings[i-1][1]) * 1000
    cum = (timings[i][1] - t0_abs) * 1000
    pct = (dt / (total*1000)) * 100 if total > 0 else 0
    print(f"  {label:<43} {dt:>10.1f} {cum:>10.1f} {pct:>5.1f}%")

print("-"*80)
print(f"  {'TOTAL':<43} {total*1000:>10.1f}")
print(f"\nRun counts: raw={n_raw} -> after_box_edges={n_after_box} -> final={n_final} (equip_outline={n_eq})")
print("="*80)
