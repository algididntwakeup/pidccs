"""
Perbandingan pengelompokan CORROSION CIRCUIT sistem vs GROUND TRUTH manual (Excel).

Ground truth = `combined_dataset/*_loop_dataset.xlsx` (dibuat manual penulis dari CCD
marked P&ID). Tiap baris: Source File + Piping ID + Color Group + Corr. Loop. WARNA tidak
dibandingkan sebagai nilai — warna hanya PENANDA: di satu gambar, piping ID ber-warna sama
= satu kelompok (satu corrosion circuit). Jadi yang dibandingkan PARTISI-nya, per gambar.

Metrik: PARTITION AGREEMENT (color-agnostic) pada level CIRCUIT — untuk tiap pasang piping
ID pada satu gambar, apakah sistem & engineer SAMA-SAMA menaruh mereka 1 grup / beda grup?
Agreement = pasangan-cocok / total-pasangan. Ini metrik standar membandingkan dua clustering
tanpa peduli label/warna (mirip Rand index).

Hanya CIRCUIT yang dibandingkan (output akhir sistem = corrosion circuit). Perbandingan
PER GAMBAR lalu diagregasi (pasangan lintas-gambar tak bermakna: gambar beda unit/loop).
"""
import os
import glob
import re
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GT_DIR = os.path.join(ROOT, "combined_dataset")
CACHE = os.path.join(ROOT, ".pidcache")


def _norm_pid(s: str) -> str:
    """Normalisasi nomor line untuk pencocokan Excel<->sistem: huruf besar, buang spasi &
    tanda kutип inci, samakan pemisah. OCR/manusia bisa beda tipografi tapi kode inti sama."""
    s = str(s).upper().strip()
    s = s.replace('"', "").replace("''", "").replace("”", "").replace("’", "")
    s = re.sub(r"\s+", "", s)
    s = s.replace("--", "-")
    return s


def load_ground_truth():
    """{drawing_stem: {norm_pid: group_key}} dari semua Excel. group_key = (Corr. Loop bila
    ada, else Color Group) — keduanya partisi per gambar; Corr. Loop lebih spesifik."""
    try:
        import pandas as pd
    except Exception as e:
        raise SystemExit(f"pandas dibutuhkan: {e}")
    gt = defaultdict(dict)
    for f in sorted(glob.glob(os.path.join(GT_DIR, "*_loop_dataset.xlsx"))):
        # Excel bikin file kunci '~$nama.xlsx' saat workbook DIBUKA -> bukan data, dan
        # membacanya melempar PermissionError. Lewati.
        if os.path.basename(f).startswith("~$"):
            continue
        try:
            df = pd.read_excel(f, sheet_name="Loop Dataset")
        except Exception:
            try:
                df = pd.read_excel(f)
            except Exception:
                continue                      # file terkunci / rusak -> lewati, jangan gagal total
        cols = {c.lower().strip(): c for c in df.columns}
        c_src = cols.get("source file"); c_pid = cols.get("piping id")
        c_col = cols.get("color group")
        if not (c_src and c_pid and c_col):
            continue
        for _, row in df.iterrows():
            src = str(row[c_src]).strip()
            pid = _norm_pid(row[c_pid])
            if not src or not pid or src.lower() == "nan":
                continue
            # GROUND TRUTH = WARNA per gambar (penanda corrosion circuit yg digambar engineer
            # di P&ID marked). BUKAN 'Corr. Loop' (itu NAMA loop level-sistem yg bisa menyatukan
            # banyak circuit). Warna dibandingkan color-agnostic (yg penting partisinya), dan
            # partisi bersifat PER GAMBAR (warna sama di gambar beda != 1 grup krn key-nya src).
            col = str(row[c_col]).strip()
            if col and col.lower() != "nan":
                gt[src][pid] = col
    return gt


def _match_drawing(src_stem, cache_stems):
    """Cocokkan Source File Excel -> stem cache. Excel sering pakai nama tanpa suffix ' Rev.x'
    atau beda spasi; cocokkan by awalan nomor drawing (BCD?-UUU-...-NNN...)."""
    s = _norm_pid(src_stem)
    # kunci = bagian 'BCD3-605-42-PID-1-011-02' (sebelum Rev/suffix)
    m = re.match(r"(BCD\d-\d+-\d+-PID-[\dA-Z-]+?)(?:REV|_|$)", s)
    key = m.group(1) if m else s
    best = None
    for cs in cache_stems:
        cn = _norm_pid(cs)
        if cn.startswith(key) or key in cn:
            # pilih yang paling mirip panjangnya
            if best is None or abs(len(cn) - len(key)) < abs(len(_norm_pid(best)) - len(key)):
                best = cs
    return best


def _pairwise_agreement(items):
    """items: list (pid, gt_group, sys_group). Return (n_pair, agree, gt_only_same, sys_only_same).
    gt_only_same = pasangan yang engineer gabung tapi sistem pisah; sys_only_same = sebaliknya."""
    n = len(items)
    n_pair = agree = eng_merge = sys_merge = 0
    for i in range(n):
        for j in range(i + 1, n):
            n_pair += 1
            g_same = items[i][1] == items[j][1]
            s_same = items[i][2] == items[j][2]
            if g_same == s_same:
                agree += 1
            elif g_same and not s_same:
                eng_merge += 1        # engineer 1 grup, sistem pisah
            else:
                sys_merge += 1        # sistem 1 grup, engineer pisah
    return n_pair, agree, eng_merge, sys_merge


def compare(result, gt_for_drawing):
    """Bandingkan CIRCUIT sistem vs ground truth utk SATU gambar. Return dict metrik / None
    bila kurang dari 2 piping ID yang cocok."""
    from .systemize import circuitize
    systems = circuitize(result)
    pids = result.get("piping_ids", [])
    # pid -> circuit code sistem
    sys_grp = {}
    for s in systems:
        for c in s["circuits"]:
            for pi in c["pid_idxs"]:
                sys_grp[_norm_pid(pids[pi].get("pid", ""))] = c["code"]
    items = []
    for npid, ggrp in gt_for_drawing.items():
        if npid in sys_grp:
            items.append((npid, ggrp, sys_grp[npid]))
    if len(items) < 2:
        return None
    n_pair, agree, eng_m, sys_m = _pairwise_agreement(items)
    return {"n_matched": len(items), "n_gt": len(gt_for_drawing),
            "n_pair": n_pair, "agree": agree,
            "pct": 100.0 * agree / n_pair if n_pair else 0.0,
            "eng_merge": eng_m, "sys_merge": sys_m}


_GT_CACHE = None


def compare_by_stem(result, stem):
    """Bandingkan CIRCUIT sistem vs ground truth utk drawing `stem` (nama cache). Return
    metrik compare() + {'in_gt':bool}. Ground truth di-cache antar panggilan."""
    global _GT_CACHE
    if _GT_CACHE is None:
        try:
            _GT_CACHE = load_ground_truth()
        except Exception:
            _GT_CACHE = {}
    # cocokkan stem cache -> source Excel (arah balik _match_drawing)
    gmap = {}
    for src, m in _GT_CACHE.items():
        if _match_drawing(src, [stem]) == stem:
            gmap.update(m)
    if not gmap:
        return {"in_gt": False}
    out = compare(result, gmap) or {"n_matched": 0}
    out["in_gt"] = True
    return out


def evaluate_all(load_result_fn):
    """Agregasi semua gambar yang punya ground truth + hasil cache. `load_result_fn(stem)`
    -> result dict / None. Return (per_drawing list, ringkasan dict)."""
    gt = load_ground_truth()
    cache_stems = [os.path.basename(f)[:-13]
                   for f in glob.glob(os.path.join(CACHE, "*.pidcorr.json"))]
    # gabung ground truth per CACHE STEM lebih dulu: 1 drawing bisa muncul di >1 Excel
    # (mis. 605_loop + 605_CCD2_loop) -> jangan dihitung dua kali. Union pid->grup.
    by_stem = defaultdict(dict)
    for src, gmap in gt.items():
        stem = _match_drawing(src, cache_stems)
        if stem:
            by_stem[stem].update(gmap)

    rows = []
    tot_pair = tot_agree = tot_em = tot_sm = 0
    for stem, gmap in by_stem.items():
        res = load_result_fn(stem)
        if not res:
            continue
        m = compare(res, gmap)
        if not m:
            continue
        m["drawing"] = stem
        rows.append(m)
        tot_pair += m["n_pair"]; tot_agree += m["agree"]
        tot_em += m["eng_merge"]; tot_sm += m["sys_merge"]
    summary = {
        "n_drawings": len(rows),
        "pct": 100.0 * tot_agree / tot_pair if tot_pair else 0.0,
        "n_pair": tot_pair, "agree": tot_agree,
        "eng_merge_pct": 100.0 * tot_em / tot_pair if tot_pair else 0.0,
        "sys_merge_pct": 100.0 * tot_sm / tot_pair if tot_pair else 0.0,
        "mean_per_drawing": (sum(r["pct"] for r in rows) / len(rows)) if rows else 0.0,
    }
    return rows, summary
