"""
Panel "Perlu Review" — daftar hal MERAGUKAN hasil komputasi utk diperiksa manusia.
Sistem tidak menyembunyikan ketidakpastian: item di sini bukan error, tapi prioritas QA.

Jenis temuan:
  pid_none      : piping ID belum terhubung ke pipa mana pun (state none / run_idx<0).
  fluid_empty   : piping ID tanpa token fluid (parsing gagal) -> tak ikut systemization.
  orphan_run    : garis UTAMA panjang yg tak terjangkau label mana pun (langsung maupun
                  lewat propagasi konektivitas) -> potensi line number ke-miss OCR, atau
                  tracing palsu yg perlu dihapus.
  cp_unattached : connection point (spec break) terbaca tapi tak menempel ke pipa mana pun
                  -> batas circuit di titik itu belum terpasang.
  fluid_suspect : kode fluid LANGKA (<=1 pemakai) yg berjarak-edit 1 dari kode umum
                  (>=3 pemakai) -> hampir pasti salah OCR (mis. '6R' vs 'GR');
                  disarankan MERGE — dieksekusi hanya bila user konfirmasi.
"""
from __future__ import annotations


def _edit1(a: str, b: str) -> bool:
    """True bila jarak edit (subst/insert/delete) a->b <= 1 dan a != b."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    for i in range(len(b)):                      # hapus 1 huruf dari b -> sama dgn a?
        if b[:i] + b[i + 1:] == a:
            return True
    return False


def _run_len(r):
    pts = r.get("points") or [[r["x1"], r["y1"]], [r["x2"], r["y2"]]]
    return sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
               for a, b in zip(pts, pts[1:]))


def build_review(result, main_len_pt=45):
    """Return list temuan terurut prioritas: [{kind, msg, ...payload}]."""
    if not result:
        return []
    pids = result.get("piping_ids", [])
    runs = result.get("runs", [])
    S = result.get("dpi", 350) / 72.0
    issues = []

    # --- kode fluid suspect (paling penting: mencemari systemization) ---
    counts = {}
    for p in pids:
        fl = (p.get("fluid") or "").strip().upper()
        if fl:
            counts[fl] = counts.get(fl, 0) + 1
    common = {f for f, n in counts.items() if n >= 3}
    for fl, n in sorted(counts.items()):
        if n <= 1 and fl not in common:
            for g in sorted(common, key=lambda x: -counts[x]):
                if _edit1(fl, g):
                    pid_idxs = [i for i, p in enumerate(pids)
                                if (p.get("fluid") or "").strip().upper() == fl]
                    issues.append({
                        "kind": "fluid_suspect", "fluid": fl, "suggest": g,
                        "pid_idxs": pid_idxs,
                        "msg": (f"Kode fluid '{fl}' cuma dipakai {n} line, mirip '{g}' "
                                f"({counts[g]} line) — kemungkinan salah OCR. "
                                f"Dobel-klik utk merge '{fl}' → '{g}'.")})
                    break

    # --- piping ID bermasalah ---
    for i, p in enumerate(pids):
        if p.get("run_idx", -1) is None or p.get("run_idx", -1) < 0:
            # label DI DALAM equipment -> kemungkinan pipa internal equipment. Sistem
            # sengaja TIDAK men-trace bagian dalam equipment, jadi ini diserahkan ke user.
            cx, cy = (p["x1"] + p["x2"]) / 2, (p["y1"] + p["y2"]) / 2
            in_eq = any(s.get("coarse") == "equipment" and
                        s["x1"] <= cx <= s["x2"] and s["y1"] <= cy <= s["y2"]
                        for s in result.get("symbols", []))
            if in_eq:
                issues.append({"kind": "pid_in_equipment", "pid_idx": i,
                               "msg": f"'{p.get('pid','?')}' berada DI DALAM equipment — "
                                      "bagian dalam equipment sengaja tak di-trace; "
                                      "trace manual bila ini memang pipa."})
            else:
                issues.append({"kind": "pid_none", "pid_idx": i,
                               "msg": f"'{p.get('pid','?')}' belum terhubung pipa — "
                                      "klik utk fokus, lalu trace/hubungkan manual."})
        elif not (p.get("fluid") or "").strip():
            issues.append({"kind": "fluid_empty", "pid_idx": i,
                           "msg": f"'{p.get('pid','?')}' tanpa token fluid — "
                                  "edit piping ID agar ikut systemization."})

    # --- connection point terbaca tapi tak menempel ke pipa ---
    # Spec break-nya nyata (dua kode class dikenal), tapi tracing tak menemukan pipa di
    # dekatnya -> batas circuit di titik itu TIDAK terpasang. Wajib dicek user.
    for ci, c in enumerate(result.get("conn_points", [])):
        if c.get("run_idx", -1) < 0:
            issues.append({"kind": "cp_unattached", "cp_idx": ci,
                           "x": c["x"], "y": c["y"],
                           "msg": f"Spec break {c['codes'][0]}|{c['codes'][1]} terbaca tapi tak "
                                  "menempel ke pipa mana pun — batas circuit di titik ini belum "
                                  "terpasang; sambungkan pipanya."})

    # --- garis utama tanpa label (orphan) ---
    # Run yang MEWARISI label lewat propagasi konektivitas bukan orphan — yang dilaporkan
    # hanya run yang benar-benar tak terjangkau label mana pun.
    try:
        from .systemize import run_labels
        lab = run_labels(result)
    except Exception:
        lab = [dict()] * len(runs)
    runs = result.get("runs", runs)              # run_labels bisa memecah run di spec break
    thr = main_len_pt * S
    orphans = []
    for ri, r in enumerate(runs):
        if r.get("underline") or (ri < len(lab) and lab[ri]):
            continue
        L = _run_len(r)
        if L >= thr * 2:                         # ambil yg PANJANG saja (2x main) -> minim noise
            orphans.append((L, ri))
    for L, ri in sorted(orphans, reverse=True):  # terpanjang duluan = paling mencurigakan
        issues.append({"kind": "orphan_run", "run_idx": ri,
                       "msg": f"Garis utama {int(L)}px tak terjangkau label mana pun — "
                              "klik utk lihat; hubungkan ke line atau hapus bila bukan pipa."})
    order = {"fluid_suspect": 0, "pid_none": 1, "pid_in_equipment": 2, "fluid_empty": 3,
             "cp_unattached": 4, "orphan_run": 5}
    issues.sort(key=lambda x: order.get(x["kind"], 9))
    return issues


def apply_fluid_merge(result, issue):
    """Eksekusi merge fluid suspect -> suggest (SETELAH konfirmasi user). Return n line."""
    tgt = issue["suggest"]
    for i in issue["pid_idxs"]:
        result["piping_ids"][i]["fluid"] = tgt
    return len(issue["pid_idxs"])
