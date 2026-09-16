"""
Fitur 2a — Systemization (Corrosion System).

Kelompokkan PIPA berdasarkan process fluid (kode di piping ID) -> 1 fluid = 1 corrosion
system, tiap system diberi 1 warna unik (Fitur 2b marking). DETERMINISTIK (aturan, BUKAN
ML) sehingga auditable: hasil sepenuhnya ditentukan kode fluid hasil parsing Fitur 1.

Landasan: API RP 970 5.6 — 'process stream composition' sah sebagai basis tunggal
systemization. Kode fluid dipakai apa adanya (RH tetap RH), tidak diterjemahkan. Warna
per-SEGMEN sesuai fluid line number-nya; batas antar warna muncul alami saat fluid berubah.
Hanya PIPING (equipment tidak masuk corrosion system).
"""
import os, json
from collections import defaultdict

# palet warna distinct (RGB) -> beda corrosion system beda warna
PALETTE = [
    (230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180),
    (0, 158, 158), (240, 50, 230), (128, 128, 0), (70, 130, 180), (170, 110, 40),
    (188, 20, 90), (0, 100, 60), (85, 85, 200), (210, 160, 0), (100, 60, 140),
]
NEUTRAL = (150, 160, 175)   # pipa tanpa fluid / tak terkelompok

# ---- registry warna GLOBAL per kode fluid (konsisten lintas drawing & sesi) ----
# GENERAL utk semua perusahaan: tidak ada daftar kode hardcode — kode fluid APA PUN yg
# pertama kali muncul di project ini di-assign 1 warna palet & dikunci permanen di
# .pidcache/fluid_colors.json. Fluid sama = warna sama di SEMUA P&ID.
_REG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".pidcache", "fluid_colors.json")
_registry = None


def _palette_color(idx):
    """Warna ke-idx. Di luar palet manual, warna dibangkitkan deterministik dengan
    langkah HUE rasio-emas -> jumlah fluid TAK TERBATAS tanpa pernah bertabrakan
    (dulu index di-modulo panjang palet, jadi fluid ke-16 memakai warna fluid ke-1)."""
    if idx < len(PALETTE):
        return PALETTE[idx]
    import colorsys
    k = idx - len(PALETTE)
    h = (0.083 + k * 0.6180339887) % 1.0
    s = 0.90 - 0.18 * (k % 3)                       # variasi saturasi/nilai supaya hue
    v = 0.78 - 0.16 * ((k // 3) % 2)                # yang berdekatan tetap terbedakan
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _col_dist(a, b):
    """Jarak warna 'redmean' — pendekatan murah tapi jauh lebih dekat ke persepsi mata
    daripada Euclidean RGB polos. Dipakai untuk menolak dua fluid berwarna nyaris sama
    (mis. GR hijau vs VRE hijau, yang di P&ID nyata tak terbedakan)."""
    rm = (a[0] + b[0]) / 2.0
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return ((2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db) ** 0.5


MIN_COL_DIST = 95.0         # di bawah ini dua warna terbaca 'senada' di layar & cetak


def _col_of_reg(v):
    """Nilai registry -> rgb. Boleh berupa INDEX palet (int) atau warna EKSPLISIT [r,g,b]
    — warna eksplisit dipakai saat generator palet tak menyediakan kontras yang cukup
    (mis. semua kandidat tersisa masih senada hijau)."""
    return tuple(v) if isinstance(v, (list, tuple)) else _palette_color(v)


# Kandidat kontras tinggi di luar generator palet, untuk kasus 'palet sudah padat'.
# Sengaja TANPA warna gelap/kehitaman: linework P&ID sendiri hitam, jadi marking gelap
# hilang di atas gambar meski jarak numeriknya terlihat besar.
_STRONG = [(150, 20, 160), (130, 40, 210), (200, 20, 120), (230, 90, 160), (255, 120, 0),
           (0, 90, 200), (170, 60, 30), (0, 140, 190), (190, 0, 0), (110, 90, 0)]


def _free_far_index(used_idxs, limit=400):
    """Index bebas dengan warna PALING KONTRAS terhadap semua warna terpakai (maksimalkan
    jarak minimum). Berhenti lebih awal begitu ada kandidat yang sudah melewati ambang —
    tak perlu cari yang sempurna, cukup yang jelas terbedakan."""
    used_cols = [_palette_color(i) for i in used_idxs if isinstance(i, int)]
    best, best_d = None, -1.0
    for idx in range(limit):
        if idx in used_idxs:
            continue
        c = _palette_color(idx)
        d = min((_col_dist(c, u) for u in used_cols), default=1e9)
        if d > best_d:
            best, best_d = idx, d
        if best_d >= MIN_COL_DIST * 1.5:
            break
    return best if best is not None else len(used_idxs)


def _save_registry(reg):
    try:
        os.makedirs(os.path.dirname(_REG_PATH), exist_ok=True)
        tmp = _REG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, indent=1)
        os.replace(tmp, _REG_PATH)
    except Exception:
        pass                                       # gagal simpan -> tetap konsisten sesi ini


def _load_registry():
    global _registry
    if _registry is None:
        try:
            with open(_REG_PATH, encoding="utf-8") as f:
                _registry = json.load(f)
        except Exception:
            _registry = {}
        # perbaikan sekali-jalan: registry lama bisa berisi index kembar (efek modulo
        # palet). Fluid yang terdaftar LEBIH DULU mempertahankan warnanya; yang kembar
        # dipindah ke index bebas -> warna lama yang sudah dipakai tidak berubah.
        seen, fixed = set(), False
        for fl, idx in list(_registry.items()):
            if not isinstance(idx, int):
                continue
            if idx in seen:
                n = 0
                while n in seen:
                    n += 1
                _registry[fl] = n; fixed = True
                seen.add(n)
            else:
                seen.add(idx)
        if fixed:
            _save_registry(_registry)
    return _registry


def recolor_fluid(fluid):
    """Pindahkan SATU kode fluid ke warna paling kontras yang masih bebas, tanpa menyentuh
    fluid lain. Dipakai saat dua kode ternyata terbaca senada di gambar nyata (mis. GR vs
    VRE yang sama-sama hijau). Return (rgb_lama, rgb_baru)."""
    reg = _load_registry()
    fl = (fluid or "").strip().upper()
    if fl not in reg:
        return None, None
    old = _col_of_reg(reg[fl])
    other_cols = [_col_of_reg(v) for f, v in reg.items() if f != fl]
    idx = _free_far_index({v for f, v in reg.items() if f != fl and isinstance(v, int)})
    best = _palette_color(idx)
    best_d = min(_col_dist(best, c) for c in other_cols) if other_cols else 1e9
    for cand in _STRONG:                              # warna eksplisit boleh menang
        d = min(_col_dist(cand, c) for c in other_cols) if other_cols else 1e9
        if d > best_d:
            best, best_d = cand, d
    reg[fl] = list(best) if best not in (_palette_color(idx),) else idx
    _save_registry(reg)
    return old, _col_of_reg(reg[fl])


def fluid_color(fluid):
    """Warna (rgb) permanen utk kode fluid — assign sekali, dipakai selamanya."""
    reg = _load_registry()
    fl = (fluid or "").strip().upper()
    if not fl:
        return NEUTRAL
    if fl not in reg:
        # registry boleh berisi indeks palet (int) ATAU warna eksplisit [r,g,b] hasil
        # recolor_fluid; hanya indeks yang bermakna di sini, dan list tidak hashable
        taken = {v for v in reg.values() if isinstance(v, int)}
        reg[fl] = _free_far_index(taken)               # bebas DAN cukup kontras
        _save_registry(reg)
    return _col_of_reg(reg[fl])


def _fluid_of(p):
    return (p.get("fluid") or "").strip().upper()


# Propagasi label lewat konektivitas pipa (propagate.py). Bisa dimatikan untuk
# membandingkan hasil SEBELUM vs SESUDAH di laporan TA.
PROPAGATE = True


_lab_cache = {}                                     # fingerprint -> label list


def _fingerprint(result):
    """Sidik jari murah dari isi yang mempengaruhi label. Berubah bila user mengoreksi
    piping ID / pipa di GUI -> cache otomatis batal."""
    pids = result.get("piping_ids", [])
    return (id(result), len(result.get("runs", [])), len(pids),
            hash(tuple((p.get("fluid"), p.get("pclass"), p.get("run_idx"),
                        tuple(p.get("extra_runs", ()))) for p in pids)),
            len(result.get("conn_points", [])), PROPAGATE)


def run_labels(result):
    """run_idx -> {fluid, pclass, src_pid, inferred, ...} untuk SEMUA run pipa.
    Dengan PROPAGATE: label menyebar dari line number mengikuti sambungan pipa dan
    berhenti di connection point (spec break). Tanpa PROPAGATE: hanya run yang langsung
    ditunjuk piping ID. Hasil di-cache — satu render GUI memanggil ini berkali-kali."""
    runs = result.get("runs", [])
    if PROPAGATE:
        from .propagate import propagate_labels
        fp = _fingerprint(result)
        if fp in _lab_cache:
            return _lab_cache[fp]
        lab = propagate_labels(result)
        if len(_lab_cache) > 8:
            _lab_cache.clear()
        _lab_cache[_fingerprint(result)] = lab      # fingerprint bisa berubah akibat split
        return lab
    lab = [dict() for _ in runs]
    for pi, p in enumerate(result.get("piping_ids", [])):
        for ri in [p.get("run_idx", -1), *p.get("extra_runs", [])]:
            if 0 <= ri < len(lab) and not lab[ri]:
                lab[ri] = {"fluid": _fluid_of(p), "pclass": (p.get("pclass") or "").upper(),
                           "src_pid": p.get("pid", ""), "src_idx": pi,
                           "dist": 0.0, "hops": 0, "inferred": False}
    return lab


def systemize(result):
    """Kembalikan list corrosion system, urut jumlah pipa terbanyak lalu abjad fluid:
       [{fluid, color(rgb), run_idxs, pid_idxs, n_pipes}]. Stabil & deterministik."""
    pids = result.get("piping_ids", [])
    lab = run_labels(result)

    by_fluid = defaultdict(list)
    for ri, L in enumerate(lab):
        if L and L.get("fluid"):
            by_fluid[L["fluid"]].append(ri)

    # urut legend: pipa terbanyak dulu, lalu abjad. WARNA dari registry global per kode
    # fluid (BUKAN dari urutan) -> fluid sama = warna sama di semua drawing & sesi.
    fluids = sorted(by_fluid, key=lambda f: (-len(by_fluid[f]), f))
    systems = []
    for fl in fluids:
        pid_idxs = [i for i, p in enumerate(pids) if _fluid_of(p) == fl]
        systems.append({
            "fluid": fl,
            "color": fluid_color(fl),
            "run_idxs": sorted(by_fluid[fl]),
            "pid_idxs": pid_idxs,
            "n_pipes": len(by_fluid[fl]),
        })
    return systems


def run_color_map(systems):
    """run_idx -> warna(rgb) untuk render cepat."""
    m = {}
    for s in systems:
        for ri in s["run_idxs"]:
            m[ri] = s["color"]
    return m


# ------------------------------------------------- Fitur 2b: Circuitization -------
# Mapping material configurable: file material_map.csv di root project (kolom:
# piping_class,material). User bisa edit di Excel — mis. isi spec resmi perusahaan
# (ASA -> CS dst). Class yg tak terdaftar fallback ke huruf ke-2 kode class (asumsi
# baseline). Auto-reload bila file berubah (cek mtime) — tak perlu restart GUI.
from datetime import datetime

_MATMAP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "material_map.csv")
_MATSPEC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "material_spec.json")
_matmap, _matmap_mtime = {}, None
_matspec, _matspec_mtime = {}, None


def _load_matmap():
    global _matmap, _matmap_mtime
    try:
        mt = os.path.getmtime(_MATMAP_PATH)
    except OSError:
        _matmap, _matmap_mtime = {}, None
        return _matmap
    if mt != _matmap_mtime:
        m = {}
        try:
            with open(_MATMAP_PATH, encoding="utf-8-sig") as f:
                for line in f:
                    parts = [t.strip() for t in line.split(",")]
                    if len(parts) >= 2 and parts[0] and parts[1] \
                            and parts[0].lower() != "piping_class":
                        m[parts[0].upper()] = parts[1].upper()
            _matmap, _matmap_mtime = m, mt
        except Exception:
            pass
    return _matmap


def _load_matspec():
    global _matspec, _matspec_mtime
    try:
        mt = os.path.getmtime(_MATSPEC_PATH)
    except OSError:
        return _matspec or {}
    if mt != _matspec_mtime:
        try:
            with open(_MATSPEC_PATH, "r", encoding="utf-8") as f:
                _matspec = json.load(f)
            _matspec_mtime = mt
        except Exception:
            pass
    return _matspec or {}


def material_of(pclass, explicit_mat=""):
    """Token MATERIAL dari kode piping class atau data operasi line list.
    Urutan prioritas:
      (1) explicit_mat dari Line List / user edit bila ada.
      (2) data/material_spec.json (spesifikasi resmi piping class).
      (3) material_map.csv bila class terdaftar.
      (4) fallback ASUMSI baseline = huruf ke-2 kode class (CDA->D, CCC->C).
    """
    if explicit_mat and str(explicit_mat).strip():
        return str(explicit_mat).strip().upper()

    pc = (pclass or "").strip().upper()
    if not pc:
        return ""

    spec = _load_matspec()
    classes = spec.get("classes", {})
    if pc in classes and classes[pc].get("material"):
        return str(classes[pc]["material"]).strip().upper()

    m = _load_matmap().get(pc)
    if m:
        return m.upper()

    return pc[1] if len(pc) >= 2 else ""


def _shade(color, f):
    """Variasi warna: f>0 menuju putih, f<0 menuju hitam (|f|<=1)."""
    r, g, b = color
    if f >= 0:
        return (int(r + (255 - r) * f), int(g + (255 - g) * f), int(b + (255 - b) * f))
    f = -f
    return (int(r * (1 - f)), int(g * (1 - f)), int(b * (1 - f)))


_CIRCUIT_SHADES = [0.0, 0.5, -0.4, 0.7, -0.6, 0.3, -0.25]   # circuit-1 = warna system asli


def circuitize(result):
    """Pecah tiap corrosion system menjadi CIRCUIT berdasarkan:
       1. Kesamaan MATERIAL konstruksi (API RP 970 5.6)
       2. Batas FASA FLUIDA (Liquid vs Gas vs 2-Phase per API RP 970 5.6.1)
       3. Corrosion Loop resmi dari Line List engineering.

    Deterministik & auditable. Return list system + circuits: [{
        code '01.02', material, classes, fluid_phase, color, run_idxs, pid_idxs,
        operating_summary, provenance: {rule, evidence, source, confidence, timestamp}
    }].
    """
    pids = result.get("piping_ids", [])
    systems = systemize(result)
    lab = run_labels(result)
    out = []

    # Helper map for accessing piping ID by index safely
    pid_by_idx = {i: p for i, p in enumerate(pids)}

    for si, s in enumerate(systems):
        # Kelompokkan run berdasarkan kriteria API RP 970
        groups = defaultdict(list)
        run_meta = {}

        for ri in s["run_idxs"]:
            L = lab[ri] or {}
            src_idx = L.get("src_idx")
            p = pid_by_idx.get(src_idx, {}) if src_idx is not None else {}

            p_pclass = (L.get("pclass") or p.get("pclass") or "").strip().upper()
            p_mat = material_of(p_pclass, p.get("material"))
            p_phase = str(p.get("fluid_phase") or "").strip().upper()
            p_loop = str(p.get("corrosion_loop") or "").strip()

            # Partition key: (material, fluid_phase, corrosion_loop)
            key = (p_mat, p_phase, p_loop)
            groups[key].append(ri)
            run_meta[ri] = {"mat": p_mat, "phase": p_phase, "loop": p_loop, "pclass": p_pclass}

        # Urutkan circuit: jumlah pipa terbanyak dulu
        sorted_keys = sorted(groups.keys(), key=lambda k: (-len(groups[k]), k[0], k[1], k[2]))
        circuits = []

        for ci, key in enumerate(sorted_keys):
            k_mat, k_phase, k_loop = key
            run_idxs = sorted(groups[key])

            # Hubungkan piping IDs yang cocok dengan key circuit ini
            pidx = []
            for pi in s["pid_idxs"]:
                p = pids[pi]
                p_pclass = (p.get("pclass") or "").strip().upper()
                p_mat = material_of(p_pclass, p.get("material"))
                p_phase = str(p.get("fluid_phase") or "").strip().upper()
                p_loop = str(p.get("corrosion_loop") or "").strip()

                if (p_mat, p_phase, p_loop) == key:
                    pidx.append(pi)
                elif not k_phase and not k_loop and p_mat == k_mat:
                    pidx.append(pi)

            # Hitung ringkasan data operasi across member PIDs
            member_temps = [
                pids[pi].get("operating_temperature")
                for pi in pidx
                if pids[pi].get("operating_temperature") is not None
            ]
            member_press = [
                pids[pi].get("operating_pressure")
                for pi in pidx
                if pids[pi].get("operating_pressure") is not None
            ]
            member_cas = [
                pids[pi].get("corrosion_allowance")
                for pi in pidx
                if pids[pi].get("corrosion_allowance") is not None
            ]

            op_summary = {
                "avg_temperature_c": round(sum(member_temps) / len(member_temps), 1) if member_temps else None,
                "avg_pressure_barg": round(sum(member_press) / len(member_press), 1) if member_press else None,
                "corrosion_allowance_mm": min(member_cas) if member_cas else None,
                "corrosion_loop": k_loop or None,
            }

            # Buat Circuit Code (e.g. '01.01')
            code = f"{si + 1:02d}.{ci + 1:02d}"
            circuit_classes = sorted({(lab[ri] or {}).get("pclass", "") for ri in run_idxs
                                      if (lab[ri] or {}).get("pclass")})

            # Tentukan Jejak Audit (Provenance) per API RP 970
            now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            if k_loop:
                rule = "LINE_LIST_CORROSION_LOOP"
                evidence = f"Direct match from engineering Line List loop tag '{k_loop}'"
                source = "linelist"
                conf = 1.0
            elif k_phase:
                rule = "API_RP_970_PHASE_BOUNDARY"
                phase_label = {"L": "Liquid", "G": "Gas/Vapor", "2P": "Two-Phase"}.get(k_phase, k_phase)
                evidence = f"Partitioned per API RP 970 §5.6.1 fluid phase boundary ({phase_label}, Material: {k_mat})"
                source = "linelist" if any(pids[pi].get("fluid_phase") for pi in pidx) else "heuristic"
                conf = 0.95
            elif any(c in _load_matspec().get("classes", {}) for c in circuit_classes):
                rule = "API_RP_970_MATERIAL_SPEC"
                matching_classes = [c for c in circuit_classes if c in _load_matspec().get("classes", {})]
                evidence = f"Material '{k_mat}' verified from structured specification data/material_spec.json for class {matching_classes}"
                source = "material_spec"
                conf = 0.95
            else:
                rule = "API_RP_970_MATERIAL_SPLIT"
                evidence = f"Partitioned by material class '{k_mat}'"
                source = "piping_class"
                conf = 0.85

            provenance = {
                "circuit_code": code,
                "rule": rule,
                "evidence": evidence,
                "source": source,
                "confidence": conf,
                "timestamp": now_iso,
            }

            circuits.append({
                "code": code,
                "material": k_mat,
                "fluid_phase": k_phase,
                "classes": circuit_classes,
                "color": s["color"] if len(sorted_keys) == 1
                         else _shade(s["color"], _CIRCUIT_SHADES[ci % len(_CIRCUIT_SHADES)]),
                "run_idxs": run_idxs,
                "pid_idxs": pidx,
                "operating_summary": op_summary,
                "provenance": provenance,
            })

        s2 = dict(s)
        s2["index"] = si + 1
        s2["circuits"] = circuits
        out.append(s2)

    return out
