"""FAST iteration diag: cache OCR + YOLO + furniture per image, then only run the
TRACER (+associate+propagate) each iteration.

Stage 1-3 (OCR, symbol detect, furniture) dominate runtime (~10 min) and their output is
DETERMINISTIC for a given image -> cache them on disk. Subsequent runs load the cache and
skip straight to the tracer (Stage 4), which is what we actually iterate on.

Usage:
    python _diag_trace_only.py "<image path>"            # uses ./_cache_<hash>.json
    python _diag_trace_only.py "<image path>" --rebuild  # force re-run stages 1-3
    python _diag_trace_only.py "<image path>" --crop x1 y1 x2 y2   # trace only a crop

Writes trace_full_diag.png + _trace_diag.json (runs) inside the container.
"""
import sys, os, json, hashlib, time
import cv2, numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "Contoh P&ID/BCD4-605-42-PID-3-019-02 Rev.1-CCD2.png"
args = sys.argv[2:]
rebuild = "--rebuild" in args
crop = None
if "--crop" in args:
    i = args.index("--crop")
    crop = tuple(int(v) for v in args[i + 1:i + 5])
    rebuild = rebuild  # crop still uses the cached full-image detections

from pidcorr.factory import get_configured_orchestrator
from pidcorr.lines import associate
try:
    from pidcorr.propagate import propagate_run_labels
except ImportError:
    def propagate_run_labels(res, dpi=350): pass
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer

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
    print("[cache miss] running stages 1-3 (OCR + YOLO + furniture)... this is the slow part")
    t0 = time.time()
    orc = get_configured_orchestrator()
    pids, tokens = orc.extractor.extract(img_bgr=img, tile=1200, progress=None)
    syms = orc.detector.detect(img_bgr=img, conf=0.30, progress=None)
    try:
        from pidcorr.layout import detect_furniture
        furniture = detect_furniture(img, orc.layout_weights)
    except Exception:
        furniture = []
    try:
        from pidcorr.subtype import classify_valve_crops
        valves = [s for s in syms if s.get("coarse") == "valve"]
        classify_valve_crops(img, valves, getattr(orc.classifier, "weights_path", None))
    except Exception:
        pass

    def _pid_dump(p):
        if isinstance(p, dict):
            return dict(p)
        return {k: getattr(p, k, "") for k in ("pid", "x1", "y1", "x2", "y2", "unit", "size", "fluid", "pclass", "seq", "conf")}

    cache = {"pids": [_pid_dump(p) for p in pids], "tokens": tokens, "syms": syms, "furniture": furniture}
    json.dump(cache, open(cache_f, "w"))
    print(f"[cache miss] stages 1-3 done in {time.time()-t0:.0f}s -> {cache_f}")

pids = cache["pids"]; tokens = cache["tokens"]; syms = cache["syms"]; furniture = cache["furniture"]
print(f"cached: pids={len(pids)} tokens={len(tokens)} syms={len(syms)} furniture={len(furniture)}")

# --- Stage 4: trace (optionally on a crop) ---
t0 = time.time()
tracer = SkeletonLineTracer()
if crop:
    x1, y1, x2, y2 = crop
    sub = img[y1:y2, x1:x2]
    # shift detections/tokens into crop-local coords and keep those intersecting
    def _shift(b, key=("x1","y1","x2","y2")):
        return {**b, key[0]: b.get(key[0],0)-x1, key[1]: b.get(key[1],0)-y1,
                key[2]: b.get(key[2],0)-x1, key[3]: b.get(key[3],0)-y1}
    syms_c = [_shift(s) for s in syms if s.get("x1",0) < x2 and s.get("x2",0) > x1 and s.get("y1",0) < y2 and s.get("y2",0) > y1]
    toks_c = [_shift(t) for t in tokens if t.get("x1",0) < x2 and t.get("x2",0) > x1 and t.get("y1",0) < y2 and t.get("y2",0) > y1]
    furn_c = [[f[0]-x1, f[1]-y1, f[2]-x1, f[3]-y1] for f in furniture if f[0] < x2 and f[2] > x1 and f[1] < y2 and f[3] > y1]
    runs = tracer.trace(img_bgr=sub, dpi=dpi, detections=syms_c, furniture=furn_c, tokens=toks_c)
    for r in runs:
        if hasattr(r, "points"):
            r.points = [(p[0]+x1, p[1]+y1) for p in r.points]
    print(f"crop trace: {len(runs)} runs in {time.time()-t0:.0f}s")
else:
    runs = tracer.trace(img_bgr=img, dpi=dpi, detections=syms, furniture=furniture, tokens=tokens, pids=pids)
    print(f"trace: {len(runs)} runs in {time.time()-t0:.0f}s")

# --- serialize runs like orchestrator does ---
def _ser(runs):
    out = []
    for i, r in enumerate(runs):
        if hasattr(r, "points"):
            pts = [[int(x), int(y)] for x, y in r.points]
            axis = getattr(r, "axis", "poly"); und = bool(getattr(r, "underline", False))
            col = getattr(r, "color", "#2563EB"); man = bool(getattr(r, "manual", False))
            eo = bool(getattr(r, "equipment_outline", False))
        else:
            pts = r.get("points", []); axis = r.get("axis", "poly")
            und = bool(r.get("underline", False)); col = r.get("color", "#2563EB")
            man = bool(r.get("manual", False)); eo = bool(r.get("equipment_outline", False))
        out.append({"id": f"run-{i}", "points": pts, "axis": axis,
                    "x1": min(p[0] for p in pts) if pts else 0,
                    "y1": min(p[1] for p in pts) if pts else 0,
                    "x2": max(p[0] for p in pts) if pts else 0,
                    "y2": max(p[1] for p in pts) if pts else 0,
                    "underline": und, "color": col, "label": "", "manual": man,
                    "equipment_outline": eo})
    return out

rruns = _ser(runs)
eq = [r for r in rruns if r["equipment_outline"]]
print(f"total runs {len(rruns)}  equipment_outline {len(eq)}")
for r in eq:
    p = r["points"]; xs=[q[0] for q in p]; ys=[q[1] for q in p]
    print(f"   EOUT pts={len(p)} bbox=({min(xs)},{min(ys)},{max(xs)},{max(ys)})")

# Optional: run label association + propagation (full-pipeline label check, fast).
if "--labels" in args:
    from pidcorr.piping_id import PipingID
    from pidcorr.lines import associate
    pid_objs = [PipingID(pid=p.get("pid", ""), x1=p.get("x1", 0), y1=p.get("y1", 0),
                         x2=p.get("x2", 0), y2=p.get("y2", 0), unit=p.get("unit", ""),
                         size=p.get("size", ""), fluid=p.get("fluid", ""),
                         pclass=p.get("pclass", ""), seq=p.get("seq", ""),
                         conf=int(p.get("conf", 0) or 0)) for p in pids]
    assoc = associate(pid_objs, runs, img, dpi=dpi)
    run_index = {id(r): i for i, r in enumerate(runs)}
    run_label_map = {}
    for a in assoc:
        pid_str = pid_objs[a["pid_idx"]].pid
        ri = run_index.get(id(a["run"]), -1) if a["run"] is not None else -1
        if ri >= 0 and pid_str:
            run_label_map[ri] = pid_str
    n_assoc = 0
    for i, r in enumerate(rruns):
        if r.get("equipment_outline"):
            continue
        if i in run_label_map and not (r.get("label") or ""):
            r["label"] = run_label_map[i]
            n_assoc += 1
    res = {"runs": rruns, "symbols": syms, "furniture": furniture,
           "piping_ids": [], "dpi": dpi}
    propagate_run_labels(res, dpi=dpi)
    print(f"labels: assoc={n_assoc} propagated={res.get('label_propagation',{}).get('n_inferred')}")
    bug = 0
    for i, r in enumerate(rruns):
        if r.get("equipment_outline") and (r.get("label") or ""):
            print(f"   !! EOUT run-{i} has pipe label {r['label']!r} (BUG)")
            bug += 1
    print(f"   EOUT with pipe labels: {bug} (expect 0)")

json.dump({"runs": rruns, "syms": syms, "furniture": furniture},
          open("_trace_diag.json", "w"))

vis = img.copy()
for r in rruns:
    pts = np.array(r["points"], np.int32).reshape(-1, 1, 2)
    col = (0, 128, 255) if r["equipment_outline"] else (0, 0, 255)
    cv2.polylines(vis, [pts], False, col, 3)
cv2.imwrite("trace_full_diag.png", vis)
print("wrote trace_full_diag.png + _trace_diag.json")
