"""
Propagasi label pipa lewat KONEKTIVITAS + pemecahan run di connection point.

MASALAH yang diselesaikan
-------------------------
Satu line number di P&ID hanya ditulis SEKALI, tetapi jalur pipanya panjang dan pecah
menjadi banyak run oleh percabangan, valve, dan simbol. Akibatnya asosiasi
"piping ID -> pipa terdekat" hanya melabeli segelintir run (terukur: ~29% run pada
drawing uji), sisanya tak berwarna sehingga marking terlihat terputus-putus.

Engineer membaca P&ID dengan cara berbeda: dia MENGIKUTI pipa dari label sampai bertemu
batas (spec break, percabangan berlabel lain, atau equipment). Modul ini meniru cara itu:

  1. split_at_connection_points : pecah run tepat di titik spec break (connection point)
     -> batas warna jatuh persis di tempat yang ditandai perancang drawing.
  2. build_adjacency            : graf ketetanggaan run (ujung-ketemu-ujung + simpul T).
  3. propagate_labels           : Dijkstra multi-sumber dari run BERLABEL ke run kosong.
     Label terdekat (jarak tempuh terpendek di sepanjang pipa) menang -> deterministik.
     Berhenti di: connection point, run berlabel lain, dan batas equipment (run memang
     sudah terpotong di equipment oleh line tracing).

Sifat: DETERMINISTIK dan AUDITABLE — tiap run hasil propagasi menyimpan `src_pid`
(line number sumber), `hops`, dan `dist`, sehingga di GUI bisa ditelusuri "pipa ini
berwarna X karena tersambung ke line number Y sejauh Z px". Tidak ada ML di sini.

Landasan: API RP 970 3.1.4 mensyaratkan system berisi piping yang INTERCONNECTED.
Propagasi berbasis konektivitas inilah yang membuat syarat 'interconnected' benar-benar
diuji — sebelumnya pengelompokan murni berdasar kesamaan kode fluid saja (lihat
CLAUDE.md §4.4, batasan yang disadari).
"""
from __future__ import annotations
import heapq
from collections import defaultdict

TOL_PT = 8.0          # toleransi "ujung ketemu ujung" / simpul T (was 6.0)
MIN_PART_PT = 8.0     # potongan lebih pendek dari ini tidak diciptakan
BRIDGE_PT = 55.0      # celah maks yang boleh dijembatani lewat komponen in-line (was 46.0)
NOISE_GAP_PT = 24.0   # celah sisa tracing/inline fitting/valve, boleh disambung langsung (was 9.0)
CORNER_GAP_PT = 10.0  # toleransi celah belokan elbow H-V


def _pts(r):
    return r.get("points") or [[r["x1"], r["y1"]], [r["x2"], r["y2"]]]


def _length(r):
    p = _pts(r)
    return sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in zip(p, p[1:]))


def _bbox(r):
    p = _pts(r)
    xs = [q[0] for q in p]; ys = [q[1] for q in p]
    return min(xs), min(ys), max(xs), max(ys)


def _mk_run(points, template):
    axis = template.get("axis", "poly")
    if len(points) == 2:
        axis = "h" if abs(points[0][1] - points[1][1]) <= abs(points[0][0] - points[1][0]) else "v"
    return {"points": [[int(x), int(y)] for x, y in points], "axis": axis,
            "x1": int(points[0][0]), "y1": int(points[0][1]),
            "x2": int(points[-1][0]), "y2": int(points[-1][1]),
            "underline": bool(template.get("underline", False))}


# ------------------------------------------------ 1) pecah di connection point ----
def split_at_connection_points(result, dpi=350):
    """Pecah run di titik connection point. Mengubah result['runs'] di tempat dan
    me-remap run_idx/extra_runs tiap piping ID. Idempotent (aman dipanggil ulang):
    ditandai lewat result['_split_done'].

    Return: set pasangan (i, j) run bersebelahan yang DILARANG saling menyalurkan label
    (dua sisi dari satu spec break)."""
    if result.get("_split_done"):
        return {tuple(p) for p in result.get("_break_pairs", [])}
    cps = result.get("conn_points") or []
    runs = result.get("runs") or []
    minpart = MIN_PART_PT * dpi / 72.0

    by_run = defaultdict(list)
    for c in cps:
        ri = c.get("run_idx", -1)
        if 0 <= ri < len(runs):
            by_run[ri].append((float(c["x"]), float(c["y"])))

    new_runs, pairs, remap = [], [], {}      # remap: old_idx -> [new_idx, ...]
    for i, r in enumerate(runs):
        cuts = by_run.get(i)
        pts = [tuple(p) for p in _pts(r)]
        if not cuts or len(pts) < 2:
            remap[i] = [len(new_runs)]; new_runs.append(r); continue

        # posisi tiap cut sepanjang polyline (arc length) -> pecah berurutan
        cum, acc = [0.0], 0.0
        for a, b in zip(pts, pts[1:]):
            acc += ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            cum.append(acc)
        ts = []
        for cx, cy in cuts:
            best = None
            for k, (a, b) in enumerate(zip(pts, pts[1:])):
                vx, vy = b[0] - a[0], b[1] - a[1]
                L2 = vx * vx + vy * vy
                if L2 <= 0:
                    continue
                t = max(0.0, min(1.0, ((cx - a[0]) * vx + (cy - a[1]) * vy) / L2))
                px, py = a[0] + t * vx, a[1] + t * vy
                d = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
                if best is None or d < best[0]:
                    best = (d, cum[k] + t * (L2 ** 0.5), (px, py))
            if best and minpart < best[1] < acc - minpart:
                ts.append((best[1], best[2]))
        if not ts:
            remap[i] = [len(new_runs)]; new_runs.append(r); continue
        ts.sort()

        parts, cur, ci = [], [pts[0]], 0
        run_len = 0.0
        for a, b in zip(pts, pts[1:]):
            seg = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            while ci < len(ts) and run_len < ts[ci][0] <= run_len + seg:
                q = ts[ci][1]
                cur.append(q); parts.append(cur); cur = [q]; ci += 1
            cur.append(b); run_len += seg
        parts.append(cur)

        idxs = []
        for p in parts:
            if len(p) >= 2:
                idxs.append(len(new_runs)); new_runs.append(_mk_run(p, r))
        remap[i] = idxs or [len(new_runs)]
        if not idxs:
            new_runs.append(r)
        for a, b in zip(idxs, idxs[1:]):
            pairs.append((a, b))                 # dua sisi spec break: JANGAN disambung

    # remap run_idx piping ID -> potongan yang paling dekat ke kotak labelnya
    for p in result.get("piping_ids", []):
        def pick(old):
            cand = remap.get(old, [])
            if len(cand) <= 1:
                return cand[0] if cand else -1
            px = (p["x1"] + p["x2"]) / 2; py = (p["y1"] + p["y2"]) / 2
            def d(j):
                x0, y0, x1, y1 = _bbox(new_runs[j])
                return (max(x0 - px, 0, px - x1) ** 2 + max(y0 - py, 0, py - y1) ** 2) ** 0.5
            return min(cand, key=d)
        old_primary = p.get("run_idx", -1)
        p["run_idx"] = pick(old_primary) if old_primary >= 0 else -1
        p["extra_runs"] = sorted({pick(e) for e in p.get("extra_runs", []) if e >= 0}
                                 - {p["run_idx"], -1})

    # remap run_idx connection point
    for c in cps:
        ri = c.get("run_idx", -1)
        if ri in remap:
            cand = remap[ri]
            c["run_idx"] = cand[0] if len(cand) == 1 else min(
                cand, key=lambda j: min(
                    (q[0] - c["x"]) ** 2 + (q[1] - c["y"]) ** 2 for q in _pts(new_runs[j])))

    result["runs"] = new_runs
    result["_break_pairs"] = [list(p) for p in pairs]
    result["_split_done"] = True
    return set(pairs)


# ------------------------------------------------------- 2) graf ketetanggaan -----
def _seg_dist(px, py, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L2 = vx * vx + vy * vy
    if L2 <= 0:
        return ((px - a[0]) ** 2 + (py - a[1]) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - a[0]) * vx + (py - a[1]) * vy) / L2))
    return ((a[0] + t * vx - px) ** 2 + (a[1] + t * vy - py) ** 2) ** 0.5


def build_adjacency(runs, dpi=350, tol_pt=TOL_PT, blocked=(), symbols=None,
                    bridge_pt=BRIDGE_PT, noise_pt=NOISE_GAP_PT, corner_pt=CORNER_GAP_PT):
    """run_idx -> set(run_idx). Dua run bersebelahan bila:

      (a) SENTUH — salah satu UJUNG run menempel pada run lain (ujung-ke-ujung atau
          ujung-ke-badan = simpul T); atau
      (b) TERJEMBATANI — kedua ujung kolinear dan celah di antaranya ditempati komponen
          IN-LINE (valve/instrument) atau hanya celah kecil/sedang sisa tracing/fitting; atau
      (c) CORNER / ELBOW — ujung pipa horizontal & vertikal bertemu pada belokan siku.

    (b) & (c) penting karena line tracing memotong pipa di setiap simbol dan belokan,
    sehingga satu jalur pipa pecah jadi banyak run yang saling terpisah. Engineer membaca
    pipa MENERUS melewati valve dan belokan — jembatan ini meniru pembacaan itu.

    Yang TIDAK dijembatani: celah yang jatuh di dalam EQUIPMENT (fluida bisa berubah di
    dalam equipment, mis. setelah exchanger) dan run 'underline'."""
    tol = tol_pt * dpi / 72.0
    bridge = bridge_pt * dpi / 72.0
    noise = noise_pt * dpi / 72.0
    corner_gap = corner_pt * dpi / 72.0
    blocked = {tuple(sorted(b)) for b in blocked}
    ok = [i for i, r in enumerate(runs) if not r.get("underline")]
    boxes = {i: _bbox(runs[i]) for i in ok}
    ends = {i: [tuple(_pts(runs[i])[0]), tuple(_pts(runs[i])[-1])] for i in ok}
    segs = {i: list(zip(_pts(runs[i]), _pts(runs[i])[1:])) for i in ok}

    inline, equip = [], []
    for s in (symbols or []):
        (equip if s.get("coarse") == "equipment" else inline).append(
            (s["x1"], s["y1"], s["x2"], s["y2"]))

    def _in_equip_interior(bxs, x, y, inset_pt=4.0):
        # Hanya true bila benar-benar berada di interior equipment (bukan di perimeter/nozzle)
        inset = inset_pt * dpi / 72.0
        return any(x0 + inset <= x <= x1 - inset and y0 + inset <= y <= y1 - inset for x0, y0, x1, y1 in bxs)

    def _in_inline(bxs, x, y, pad_pt=4.0):
        pad = pad_pt * dpi / 72.0
        return any(x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad for x0, y0, x1, y1 in bxs)

    def _bridgeable(pa, pb, axis_a="poly", axis_b="poly"):
        """Celah pa..pb boleh dianggap satu jalur pipa?"""
        dx, dy = abs(pa[0] - pb[0]), abs(pa[1] - pb[1])
        euclid_d = (dx * dx + dy * dy) ** 0.5
        mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2

        # Cek apakah celah menembus masuk ke dalam badan equipment
        if _in_equip_interior(equip, mx, my):
            return False

        # 1. Elbow / Corner proximity (perpendicular runs or polylines meeting at a bend)
        is_perpendicular = (axis_a == "h" and axis_b == "v") or (axis_a == "v" and axis_b == "h") or axis_a == "poly" or axis_b == "poly"
        if is_perpendicular and euclid_d <= corner_gap:
            return True

        # 2. Collinear Straight Pipe
        if min(dx, dy) > tol:                       # tidak kolinear H/V
            return False
        gap = max(dx, dy)
        if gap > bridge:
            return False
        return gap <= noise or _in_inline(inline, mx, my)

    adj = defaultdict(set)
    for ii, i in enumerate(ok):
        bx0, by0, bx1, by1 = boxes[i]
        for j in ok[ii + 1:]:
            if tuple(sorted((i, j))) in blocked:
                continue
            cx0, cy0, cx1, cy1 = boxes[j]
            far = bridge + tol
            if bx0 - far > cx1 or cx0 - far > bx1 or by0 - far > cy1 or cy0 - far > by1:
                continue
            touch = any(_seg_dist(ex, ey, a, b) <= tol
                        for ex, ey in ends[i] for a, b in segs[j]) or \
                    any(_seg_dist(ex, ey, a, b) <= tol
                        for ex, ey in ends[j] for a, b in segs[i])
            if not touch:
                axis_i = runs[i].get("axis", "poly")
                axis_j = runs[j].get("axis", "poly")
                touch = any(_bridgeable(pa, pb, axis_i, axis_j) for pa in ends[i] for pb in ends[j])
            if touch:
                adj[i].add(j); adj[j].add(i)
    return adj


SEED_MAX_PT = 40.0    # jarak maks kotak teks line number -> pipa utk seed cadangan


def mark_text_underlines(img_bgr, runs, dpi=350, adj=None, band_pt=10, cover=0.72):
    """Tandai run yang sebenarnya GARIS BAWAH TEKS (judul, tag equipment, catatan) sebagai
    `underline` supaya tidak ikut dihitung/diwarnai sebagai pipa.

    Terukur pada drawing uji: seluruh sisa 'pipa' tak terwarnai ternyata garis bawah
    semacam ini ("TO ATM VIA EXHAUST STACK", "695-S-101", "695-E-106"), bukan pipa proses.

    Syarat sengaja KETAT supaya tak pernah membuang pipa asli:
      1. run MENDATAR;
      2. TERISOLASI di graf pipa (tak bertetangga dengan run mana pun) — pipa asli hampir
         selalu tersambung ke sesuatu, jadi syarat ini saja sudah menutup risiko utama;
      3. ada tinta (teks) tepat DI ATAS garis di sepanjang >=72% panjangnya — pipa tidak
         pernah punya teks yang menempel sepanjang garisnya, label pipa jauh lebih pendek;
      4. TIDAK ada run lain yang KOLINEAR di dekatnya. Syarat ini lahir dari false positive
         nyata: satu penggal pipa utama yang terputus oleh tracing lolos syarat 1-3 karena
         kebetulan ada 'SUS 316L' di atasnya — tetapi lanjutan pipanya ada di y yang sama.
         Garis bawah teks tidak pernah punya lanjutan kolinear.
    Return jumlah run yang ditandai."""
    if img_bgr is None or not runs:
        return 0
    S = dpi / 72.0
    band = max(4, int(band_pt * S))
    gray = img_bgr if img_bgr.ndim == 2 else img_bgr.mean(axis=2)
    H, W = gray.shape[:2]
    if adj is None:
        adj = build_adjacency(runs, dpi=dpi)
    n = 0
    for i, r in enumerate(runs):
        if r.get("underline") or adj.get(i):
            continue
        p = _pts(r)
        if len(p) != 2 or abs(p[0][1] - p[1][1]) > 2 * S:
            continue                                   # bukan segmen mendatar lurus
        y = int((p[0][1] + p[1][1]) / 2)
        x0, x1 = int(min(p[0][0], p[1][0])), int(max(p[0][0], p[1][0]))
        if x1 - x0 < 8 or y - band - 2 < 0:
            continue
        strip = gray[max(0, y - band - 2):max(1, y - 2), max(0, x0):min(W, x1)]
        if strip.size == 0:
            continue
        col_has_ink = (strip < 128).any(axis=0)
        if col_has_ink.mean() < cover:
            continue
        near = 300.0 * S                               # jangkauan cek lanjutan kolinear
        collinear = False
        for j, q in enumerate(runs):
            if j == i or q.get("underline"):
                continue
            pq = _pts(q)
            if len(pq) != 2 or abs(pq[0][1] - pq[1][1]) > 2 * S:
                continue
            if abs((pq[0][1] + pq[1][1]) / 2 - y) > 3 * S:
                continue
            qx0, qx1 = min(pq[0][0], pq[1][0]), max(pq[0][0], pq[1][0])
            if max(x0 - qx1, qx0 - x1) <= near:         # bersinggungan / berdekatan di x
                collinear = True
                break
        if not collinear:
            r["underline"] = True
            n += 1
    return n


def _components(runs, adj):
    """run_idx -> id komponen terhubung (run pipa saja)."""
    comp, cid = {}, 0
    for i, r in enumerate(runs):
        if r.get("underline") or i in comp:
            continue
        stack, cid = [i], cid + 1
        comp[i] = cid
        while stack:
            u = stack.pop()
            for v in adj.get(u, ()):
                if v not in comp:
                    comp[v] = cid; stack.append(v)
    return comp


def _fallback_seeds(runs, pids, lab, adj, dpi):
    """[(run_idx, pid_idx)] — pasangan seed cadangan untuk komponen pipa yang kosong."""
    comp = _components(runs, adj)
    labeled_comp = {comp[i] for i, L in enumerate(lab) if L and i in comp}
    maxd = SEED_MAX_PT * dpi / 72.0

    cand = []                                    # (jarak, comp_id, run_idx, pid_idx)
    for pi, p in enumerate(pids):
        if p.get("run_idx", -1) >= 0 or p.get("extra_runs"):
            continue                             # sudah terasosiasi ketat
        if not (p.get("fluid") or "").strip():
            continue                             # tanpa fluid tak bisa masuk system
        px = (p.get("x1", 0) + p.get("x2", 0)) / 2.0
        py = (p.get("y1", 0) + p.get("y2", 0)) / 2.0
        for ri, r in enumerate(runs):
            c = comp.get(ri)
            if c is None or c in labeled_comp or r.get("underline"):
                continue
            d = min(_seg_dist(px, py, a, b) for a, b in zip(_pts(r), _pts(r)[1:]))
            if d <= maxd:
                cand.append((d, c, ri, pi))

    out, taken_comp, taken_pid = [], set(), set()
    for d, c, ri, pi in sorted(cand):            # terdekat menang -> deterministik
        if c in taken_comp or pi in taken_pid:
            continue
        taken_comp.add(c); taken_pid.add(pi)
        out.append((ri, pi))
    return out


# ------------------------------------------------------- 3) propagasi label -------
def propagate_labels(result, dpi=350, max_dist_pt=None):
    """Isi label (fluid, pclass) run yang kosong dari run berlabel via konektivitas.

    Return list sepanjang runs: {fluid, pclass, src_pid, dist, hops, inferred} —
    entri kosong ({} ) berarti run tetap tak terkelompok."""
    runs = result.get("runs") or []
    pids = result.get("piping_ids") or []
    blocked = split_at_connection_points(result, dpi=dpi)
    runs = result["runs"]                                  # bisa berubah akibat split
    adj = build_adjacency(runs, dpi=dpi, blocked=blocked,
                          symbols=result.get("symbols") or [])
    maxd = (max_dist_pt * dpi / 72.0) if max_dist_pt else float("inf")

    lab = [dict() for _ in runs]
    pq = []
    for pi, p in enumerate(pids):
        fl = (p.get("fluid") or "").strip().upper()
        pc = (p.get("pclass") or "").strip().upper()
        if not fl and not pc:
            continue
        for ri in [p.get("run_idx", -1), *p.get("extra_runs", [])]:
            if 0 <= ri < len(runs) and not lab[ri]:
                lab[ri] = {"fluid": fl, "pclass": pc, "src_pid": p.get("pid", ""),
                           "src_idx": pi, "dist": 0.0, "hops": 0, "inferred": False}
                heapq.heappush(pq, (0.0, 0, ri))

    # Seed CADANGAN: line number yang gagal terasosiasi ketat (run_idx < 0) tetap boleh
    # menyalakan KOMPONEN pipa terdekat yang belum punya label sama sekali. Terukur, ini
    # bagian terbesar pipa tak terwarnai: bukan pipanya yang terisolasi, melainkan seluruh
    # komponennya tak punya satu pun seed. Syarat ketat supaya tidak ngawur:
    #   - hanya untuk pid yang memang gagal terasosiasi,
    #   - komponen tujuan harus BENAR-BENAR kosong (tak menimpa apa pun),
    #   - jarak teks ke pipa terbatas, dan satu komponen hanya diklaim satu pid (terdekat).
    for ri, pi in _fallback_seeds(runs, pids, lab, adj, dpi):
        p = pids[pi]
        lab[ri] = {"fluid": (p.get("fluid") or "").strip().upper(),
                   "pclass": (p.get("pclass") or "").strip().upper(),
                   "src_pid": p.get("pid", ""), "src_idx": pi,
                   "dist": 0.0, "hops": 0, "inferred": False, "weak_seed": True}
        heapq.heappush(pq, (0.0, 0, ri))

    # Dijkstra multi-sumber: label dengan jarak tempuh TERPENDEK di sepanjang pipa menang.
    # Urutan pop deterministik (jarak, hops, idx) -> hasil identik tiap kali dijalankan.
    while pq:
        d, h, i = heapq.heappop(pq)
        if d > lab[i].get("dist", float("inf")) + 1e-6:
            continue
        for j in sorted(adj.get(i, ())):
            nd = d + _length(runs[j])
            if nd > maxd:
                continue
            cur = lab[j].get("dist", float("inf"))
            if not lab[j] or nd < cur - 1e-6:
                if lab[j] and not lab[j].get("inferred"):
                    continue                               # label asli tak boleh ditimpa
                lab[j] = {"fluid": lab[i]["fluid"], "pclass": lab[i]["pclass"],
                          "src_pid": lab[i]["src_pid"], "src_idx": lab[i]["src_idx"],
                          "dist": nd, "hops": h + 1, "inferred": True}
                heapq.heappush(pq, (nd, h + 1, j))

    apply_conn_point_classes(result, lab, dpi=dpi)
    return lab


def apply_conn_point_classes(result, lab, dpi=350, tol_pt=TOL_PT):
    """Terapkan kode class dari CONNECTION POINT ke pipa di kedua sisinya.

    Connection point menyatakan class DI KIRI dan DI KANAN titik break (atau atas/bawah).
    Informasi ini lebih kuat daripada hasil propagasi — perancang drawing menuliskannya
    secara eksplisit — tetapi LEBIH LEMAH daripada line number yang menempel langsung
    pada pipa itu. Urutan kekuatan bukti yang dipakai:

        line number langsung  >  connection point  >  propagasi konektivitas

    Jadi hanya run ber-label hasil propagasi (`inferred`) yang ditimpa. Fluid TIDAK
    diubah — connection point hanya bicara soal piping class (material), bukan fluida."""
    runs = result.get("runs") or []
    tol = tol_pt * dpi / 72.0
    n = 0
    for c in result.get("conn_points", []):
        if c.get("run_idx", -1) < 0 or not c.get("side_a"):
            continue
        cx, cy = float(c["x"]), float(c["y"])
        # dua potongan pipa yang bertemu di titik break
        halves = [i for i, r in enumerate(runs) if not r.get("underline")
                  and any(abs(p[0] - cx) <= tol and abs(p[1] - cy) <= tol
                          for p in (_pts(r)[0], _pts(r)[-1]))]
        if len(halves) != 2:
            continue
        horiz = c["side_a"] == "left"
        key = (lambda i: sum(p[0] for p in _pts(runs[i])) / len(_pts(runs[i]))) if horiz \
              else (lambda i: sum(p[1] for p in _pts(runs[i])) / len(_pts(runs[i])))
        first, second = sorted(halves, key=key)     # kiri->kanan, atau atas->bawah
        for ri, code in ((first, c["codes"][0]), (second, c["codes"][1])):
            if lab[ri] and not lab[ri].get("inferred"):
                continue                            # line number langsung: jangan ditimpa
            if not lab[ri]:
                continue                            # tanpa fluid -> tak masuk system mana pun
            if lab[ri].get("pclass") != code:
                lab[ri] = {**lab[ri], "pclass": code, "class_src": "conn_point"}
                n += 1
    return n


def coverage(result, dpi=350):
    """(n_berlabel_langsung, n_hasil_propagasi, n_total_run_pipa) untuk pelaporan."""
    lab = propagate_labels(result, dpi=dpi)
    runs = result["runs"]
    pipe = [i for i, r in enumerate(runs) if not r.get("underline")]
    direct = sum(1 for i in pipe if lab[i] and not lab[i]["inferred"])
    inferred = sum(1 for i in pipe if lab[i] and lab[i]["inferred"])
    return direct, inferred, len(pipe)
