"""
Deteksi PIPING ID pada P&ID KOSONGAN (unmarked) + parse process-fluid & piping-class.

Diadaptasi dari sistem lama (piping_loop_detector.py) yang sudah terbukti jago baca
piping ID, TAPI dilucuti dari semua logika warna/callout/loop-membership (itu khusus
P&ID yang sudah dimarking). Yang dipertahankan = inti yang general:
  - regex piping-ID tertuned (PIPE_RE strict + LOOSE + repair salah-baca),
  - OCR ber-tile multi-sudut (0/90/270) -> tangkap label horizontal & vertikal,
  - clustering/voting -> 1 piping ID unik per pipa fisik, dengan bbox utk highlight.

GUI-ready: tiap hasil = PipingID(pid, bbox, unit/size/fluid/class/seq, conf, manual).
Mendukung tambah/koreksi MANUAL (human-in-the-loop) -> makin dipakai makin lengkap.

Input = CITRA (numpy BGR) P&ID kosongan pada DPI memadai (>=300 disarankan utk teks kecil).
"""
from __future__ import annotations
import os, json, re
from dataclasses import dataclass, asdict
from collections import defaultdict
import numpy as np
import cv2

# -------------------------------------------------- parser KONFIGURABEL -------
# CLAUDE.md: JANGAN hardcode urutan token. Tiap perusahaan punya urutan berbeda, jadi
# parser = REGISTRY schema. Tiap schema = regex bernama-grup {unit,size,fluid,pclass,seq}.
# Deteksi mencoba SEMUA schema; yang match menang. Tambah perusahaan baru = tambah 1 entri.
SCHEMA_FIELDS = ("unit", "size", "fluid", "pclass", "seq")

# Versi cache OCR (.pids.json). BUMP setiap kali logika deteksi/match/parse berubah supaya
# cache lama (hasil OCR versi sebelumnya) diabaikan & OCR dijalankan ulang otomatis.
PIDS_CACHE_VERSION = 6


@dataclass
class PidSchema:
    name: str
    pattern: "re.Pattern"
    weight: int = 3


SCHEMAS: list = [
    # PetroChina:  695-6"-GF-CCB-026  atau  650-1"-GT-CPA-019-H40-E / -3A100
    # (unit - size - fluid - class - seq [- suffix...]). SUFFIX (H40/E/3A100/P25) ikut
    # ditangkap ke dalam pid PENUH utk asset register, tapi tak jadi token systemization.
    PidSchema("petrochina", re.compile(
        r'(?P<unit>\d{2,3})-(?P<size>\d{1,2}(?:-\d/\d|/\d)?)["”″\']?'
        r'-(?P<fluid>[A-Z]{1,4})-(?P<pclass>[A-Z]{3})'
        # seq = angka + s/d 3 huruf menempel (201A, 201AB, 201AC, 201AD, 201AE = LINE BEDA!);
        # suffix = token ber-dash setelahnya (-H40-E, -3A100, -P25)
        r'-(?P<seq>\d{1,3}[A-Z]{0,3})(?P<suffix>(?:-[A-Z0-9]{1,5}){0,3})')),
    # Pertamina:   14-P2-1802-A1A2-6"-H-50   (unit - fluid - seq - class - size - ...)
    PidSchema("pertamina", re.compile(
        r'(?P<unit>\d{1,3})-(?P<fluid>[A-Z]{1,2}\d?)-(?P<seq>\d{3,4})'
        r'-(?P<pclass>[A-Z][A-Z0-9]{2,4})-(?P<size>\d{1,2}(?:[-/]\d/\d)?)["”″\']?'
        r'(?:-[A-Z]-?\d{1,3})?')),
]
# fallback longgar PetroChina (perbaiki salah-baca: seq huruf->digit, FLUID digit->huruf)
# fluid boleh mengandung digit di sini (mis. 'L0' = salah-baca 'LO' yg SANGAT sering)
_PETRO_LOOSE = re.compile(
    r'(\d{2,3})-(\d{1,2}(?:-\d/\d|/\d)?)["”″\']?-([A-Z][A-Z0-9]{0,3})-([A-Z]{3})-([A-Z0-9]{1,4})'
    r'((?:-[A-Z0-9]{1,5}){0,3})')
_SEQ_FIX = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "Z": "2",
            "S": "5", "B": "8", "G": "6", "T": "7"}
_FLUID_FIX = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"}


def _repair_seq(seq: str) -> str:
    return "".join(_SEQ_FIX.get(c, c) for c in seq)


def _repair_fluid(fl: str) -> str:
    return "".join(_FLUID_FIX.get(c, c) for c in fl)


def _normalize(s: str) -> str:
    """OCR text -> kanonik. Lenient lintas-format: samakan inch mark, '!'/'|'->'1'."""
    s = s.upper()
    for a, b in (("”", '"'), ("″", '"'), ("’", '"'), ("‘", '"'), ("'", '"'),
                 ("`", '"'), ("!", "1"), ("|", "1")):
        s = s.replace(a, b)
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'[^A-Z0-9"/]', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s


def _looks_like_tag(s: str) -> bool:
    """Heuristik GENERAL (lintas-perusahaan): apakah string ter-normalisasi berbentuk
    LINE NUMBER? -> mulai 2-4 digit (unit/area), >=4 token dipisah '-', ada token huruf
    (fluid/class), dan ada inch-mark ATAU token terakhir mengandung angka (seq). Ini yg
    membuat sistem mendeteksi piping ID perusahaan yg SKEMANYA belum dikenal (lalu user
    ajari parsing-nya). Sengaja menolak tag equipment 3-token (mis. 650-V-202A) & dimensi."""
    if not re.match(r'^\d{2,4}-', s):
        return False
    toks = s.split("-")
    if len(toks) < 4:
        return False
    if not re.search(r'[A-Z]{2,}', s):
        return False
    return ('"' in s) or bool(re.search(r'\d', toks[-1]))


def match_pid(comp: str):
    """comp ter-normalisasi -> (pid_kanonik, weight). Urutan: schema perusahaan -> fallback
    longgar PetroChina -> GENERIK (bentuk line-number apa pun). Generik weight 1: tertangkap
    utk asset register walau fluid/class-nya belum bisa diparse (user ajari via schema-by-example)."""
    for sc in SCHEMAS:
        m = sc.pattern.search(comp)
        if m:
            return m.group(0), sc.weight
    m2 = _PETRO_LOOSE.search(comp)
    if m2:
        a, sz, sv, cl, sq, suf = m2.groups()
        rsv = _repair_fluid(sv)                  # 'L0'->'LO', 'D0'->'DO' (salah-baca umum)
        rsq = _repair_seq(sq)
        if re.fullmatch(r'[A-Z]{1,4}', rsv) and re.fullmatch(r'\d{1,3}[A-Z]?', rsq):
            return f'{a}-{sz}"-{rsv}-{cl}-{rsq}{suf or ""}', 1
    if _looks_like_tag(comp):                     # GENERAL: bentuk line-number tak-terskema
        return comp, 1
    return None, 0


# --------------------------------------------------- skema yang diajari user ----
# Skema hasil "Teach parsing" disimpan di sini, bukan di dalam hasil satu gambar, supaya
# format penomoran yang sudah diajarkan sekali langsung berlaku pada gambar berikutnya dari
# perusahaan yang sama. Kunci = jumlah token; sebuah kode hanya boleh diparse oleh skema
# yang jumlah tokennya sama, karena skema ini POSISIONAL.
_LEARNED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             ".pidcache", "pid_schemas.json")
_learned_cache = None


def learned_schemas() -> dict:
    """{jumlah_token: {field: indeks}} — dibaca sekali lalu ditahan di memori."""
    global _learned_cache
    if _learned_cache is None:
        try:
            with open(_LEARNED_PATH, encoding="utf-8") as f:
                _learned_cache = {int(k): v for k, v in json.load(f).items()}
        except Exception:
            _learned_cache = {}
    return _learned_cache


def remember_schema(example_pid: str, schema: dict) -> int:
    """Simpan skema posisional yang diajari user. Return jumlah token yang dicakupnya."""
    n = len(pid_tokens(example_pid))
    reg = dict(learned_schemas())
    reg[n] = {k: int(v) for k, v in (schema or {}).items()
              if k in SCHEMA_FIELDS and isinstance(v, int)}
    os.makedirs(os.path.dirname(_LEARNED_PATH), exist_ok=True)
    tmp = _LEARNED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in reg.items()}, f, indent=1)
    os.replace(tmp, _LEARNED_PATH)
    globals()["_learned_cache"] = reg
    return n


def parse_tokens(pid: str) -> dict:
    """Pecah piping ID -> {unit,size,fluid,pclass,seq} via schema yg cocok. Fluid=basis systemization."""
    for sc in SCHEMAS:
        m = sc.pattern.match(pid) or sc.pattern.search(pid)
        if m:
            gd = m.groupdict()
            return {k: (gd.get(k) or "") for k in SCHEMA_FIELDS}
    # skema bawaan gagal -> coba yang pernah diajarkan user untuk format ini
    reg = learned_schemas()
    if reg:
        sc = reg.get(len(pid_tokens(pid)))
        if sc:
            out = apply_example_schema(pid, sc)
            if out.get("fluid") or out.get("pclass"):
                return out
    return {k: "" for k in SCHEMA_FIELDS}


# ------------------------------------------------------ data class ------------
@dataclass
class PipingID:
    pid: str
    x1: float; y1: float; x2: float; y2: float    # bbox di piksel citra
    unit: str = ""; size: str = ""; fluid: str = ""; pclass: str = ""; seq: str = ""
    conf: int = 0                                   # bobot voting (recall confidence)
    orient: int = 0                                 # 0 horizontal, 90 vertikal
    manual: bool = False                            # True bila ditamb/dikoreksi user

    @property
    def cx(self): return (self.x1 + self.x2) / 2

    @property
    def cy(self): return (self.y1 + self.y2) / 2


# ------------------------------------------------------ RapidOCR engine -------
_RAPID = None


def get_rapid():
    global _RAPID
    if _RAPID is None:
        from rapidocr_onnxruntime import RapidOCR
        _RAPID = RapidOCR()
    return _RAPID


# ------------------------------------------------------ rotasi tile -----------
def _rotate(crop, ang):
    """Return (rotated, mapfn) di mana mapfn(rx,ry)->(x,y) di frame crop asli."""
    h, w = crop.shape[:2]
    if ang == 0:
        return crop, (lambda rx, ry: (rx, ry))
    if ang == 90:   # CW
        return cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE), (lambda rx, ry: (ry, h - 1 - rx))
    if ang == 270:  # CCW
        return cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE), (lambda rx, ry: (w - 1 - ry, rx))
    raise ValueError(ang)


def _offsets(size, tile, overlap):
    if size <= tile:
        return [0]
    step = max(1, int(round(tile * (1 - overlap))))
    offs = list(range(0, size - tile + 1, step))
    if offs[-1] != size - tile:
        offs.append(size - tile)
    return offs


# ------------------------------------------------------ deteksi ---------------
def _pair_two_line(frags):
    """Gabung fragmen OCR yg BERTUMPUK 2 BARIS jadi 1 kandidat piping ID.

    Pola nyata di P&ID: label panjang ditulis 2 baris (mis. '650-1 1/2"-LO' di atas
    '-APA-022-P25', sering dipisah garis penunjuk) -> tiap baris sendiri TIDAK match
    schema, gabungannya match. Bekerja di FRAME TEROTASI per (tile, sudut), jadi
    label vertikal 2-kolom ikut tertangani (di frame rotasi tampak 2 baris juga).
    Syarat pasangan: fragmen j di bawah i (gap -0.6..1.8x tinggi teks) + overlap
    horizontal >=40% -> concat i+'-'+j -> HARUS match schema penuh."""
    out = []
    metas = []
    for norm, pts in frags:
        if not norm or len(norm) < 2:
            continue
        x1, y1 = pts[:, 0].min(), pts[:, 1].min()
        x2, y2 = pts[:, 0].max(), pts[:, 1].max()
        metas.append((norm, pts, x1, y1, x2, y2))
    for ti, pi, xi1, yi1, xi2, yi2 in metas:
        hi = yi2 - yi1
        for tj, pj, xj1, yj1, xj2, yj2 in metas:
            if tj is ti and pj is pi:
                continue
            h = max(hi, yj2 - yj1, 1.0)
            gap = yj1 - yi2                       # j harus di bawah i
            if not (-0.6 * h <= gap <= 1.8 * h):
                continue
            ov = min(xi2, xj2) - max(xi1, xj1)
            if ov < 0.4 * min(xi2 - xi1, xj2 - xj1):
                continue
            pid, w = match_pid(_normalize(ti + "-" + tj))
            if pid:
                out.append((pid, w, np.vstack([pi, pj])))
    return out


_SHORT_TOK = re.compile(r"^[A-Z]{2,4}$")     # kandidat kode piping class (connection point)


def detect_piping_ids(img_bgr, tile=1200, overlap=0.25, angles=(0, 90, 270),
                      min_votes=1, merge_dist=55, progress=None,
                      tokens_out=None) -> list[PipingID]:
    """OCR ber-tile multi-sudut -> kumpulan piping ID unik (dengan bbox).

    `tokens_out`: bila list diberikan, SEMUA token pendek huruf-kapital (kandidat kode
    piping class utk deteksi connection point / spec break) ikut dikumpulkan lengkap
    dengan bbox global. Gratis — memanfaatkan pass OCR yang sama, tanpa OCR tambahan."""
    eng = get_rapid()
    H, W = img_bgr.shape[:2]
    xs, ys = _offsets(W, tile, overlap), _offsets(H, tile, overlap)
    raw = []  # (pid, cx, cy, bw, bh, orient, weight)
    total = len(xs) * len(ys); done = 0
    for oy in ys:
        for ox in xs:
            crop = img_bgr[oy:oy + tile, ox:ox + tile]
            for ang in angles:
                rot, mapfn = _rotate(crop, ang)
                try:
                    res, _ = eng(cv2.cvtColor(rot, cv2.COLOR_BGR2RGB))
                except Exception:
                    res = None
                unmatched = []
                hits = []                          # (pid, w, pts, paired) di frame rotasi
                for box, text, score in (res or []):
                    norm = _normalize(text)
                    pid, w = match_pid(norm)
                    pts = np.asarray(box, dtype=float)
                    if tokens_out is not None and _SHORT_TOK.match(norm):
                        mp = np.array([mapfn(px, py) for px, py in pts])
                        tokens_out.append({
                            "t": norm, "ang": 0 if ang == 0 else 90,
                            "x1": float(mp[:, 0].min()) + ox, "y1": float(mp[:, 1].min()) + oy,
                            "x2": float(mp[:, 0].max()) + ox, "y2": float(mp[:, 1].max()) + oy})
                    if pid:
                        hits.append((pid, w, pts, False))
                    else:
                        unmatched.append((norm, pts))
                hits += [(p, w, pts, True) for p, w, pts in _pair_two_line(unmatched)]
                for pid, w, pts, paired in hits:
                    mapped = np.array([mapfn(px, py) for px, py in pts])
                    gx = mapped[:, 0] + ox; gy = mapped[:, 1] + oy
                    raw.append((pid, gx.mean(), gy.mean(),
                                gx.max() - gx.min(), gy.max() - gy.min(),
                                0 if ang == 0 else 90, w, paired))
            done += 1
            if progress:
                progress(done, total)
    return _cluster(_snap_units(raw), min_votes, merge_dist)


def _snap_units(raw):
    """Snap unit/area salah-baca ke unit dominan drawing (mis. 95 -> 695).

    P&ID satu unit -> mayoritas piping ID berawalan unit sama. Deteksi conf-tinggi
    (weight>=3) menentukan unit dominan; deteksi dgn unit yg jelas typo (suffix/prefix
    beda 1 digit) di-snap supaya tak jadi duplikat terpisah."""
    from collections import Counter
    units = Counter(d[0].split("-", 1)[0] for d in raw if d[6] >= 3 and not d[7])
    if not units:
        return raw
    dom = units.most_common(1)[0][0]
    out = []
    for d in raw:
        u = d[0].split("-", 1)[0]
        if d[7] and u != dom:
            # kandidat hasil PAIRING 2-baris wajib ber-unit dominan drawing — pasangan
            # fragmen acak (mis. '011-...' dari potongan seq + label lain) dibuang.
            if abs(len(dom) - len(u)) <= 1 and (dom.endswith(u) or u.endswith(dom)):
                d = (dom + d[0][len(u):],) + tuple(d[1:])
            else:
                continue
        elif u != dom and abs(len(dom) - len(u)) <= 1 and (dom.endswith(u) or u.endswith(dom)):
            d = (dom + d[0][len(u):],) + tuple(d[1:])
        out.append(d)
    return out


def _cluster(raw, min_votes, merge_dist) -> list[PipingID]:
    """Cluster SPATIAL-first: deteksi se-lokasi = 1 pipa fisik, walau string pid sedikit
    beda (varian OCR: inch mark / suffix -H-50 kadang ke-potong). Pilih pid pemenang =
    bobot voting tertinggi (tie -> paling lengkap)."""
    clusters = []  # tiap cluster: dict(cx, cy, dets)
    for d in sorted(raw, key=lambda e: -e[6]):   # seed dgn deteksi terkuat dulu
        placed = False
        for cl in clusters:
            if abs(d[1] - cl["cx"]) <= merge_dist and abs(d[2] - cl["cy"]) <= merge_dist:
                cl["dets"].append(d)
                cl["cx"] = float(np.mean([e[1] for e in cl["dets"]]))
                cl["cy"] = float(np.mean([e[2] for e in cl["dets"]]))
                placed = True
                break
        if not placed:
            clusters.append({"cx": d[1], "cy": d[2], "dets": [d]})

    out: list[PipingID] = []
    for cl in clusters:
        ds = cl["dets"]
        wsum = defaultdict(int)
        for e in ds:
            wsum[e[0]] += e[6]
        votes = sum(wsum.values())
        if votes < min_votes:
            continue
        pid = max(wsum, key=lambda p: (wsum[p], len(p)))   # pemenang voting (tie->terlengkap)
        cx = np.median([e[1] for e in ds]); cy = np.median([e[2] for e in ds])
        bw = np.median([e[3] for e in ds]); bh = np.median([e[4] for e in ds])
        # orientasi dari RASIO BBOX (andal), bukan sudut OCR (RapidOCR baca teks H
        # juga di tile terotasi -> sudut OCR sering salah). Lebar>tinggi = horizontal.
        orient = 0 if bw >= bh else 90
        out.append(PipingID(pid, cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2,
                            conf=int(votes), orient=orient, **parse_tokens(pid)))
    # CATATAN: dedup nomor-line TIDAK di sini — dilakukan di pipeline SETELAH asosiasi,
    # supaya tiap label (2 ujung 1 pipa) sempat terasosiasi ke run-nya dulu, lalu run
    # digabung ke 1 entri register (extra_runs) → kedua segmen tetap terwarnai.
    out.sort(key=lambda p: (-p.conf, p.pid))
    return out


def _canon_key(pid: str) -> str:
    """Kunci identitas LINE NUMBER: samakan varian OCR (inch-mark ada/tidak, spasi) supaya
    label sama yg ditulis 2x jadi satu kunci. '650-3/4\"-AI-ACB-010' == '650-3/4-AI-ACB-010'."""
    return pid.replace('"', "").replace(" ", "").upper()


def _dedup_by_line(items: list[PipingID]) -> list[PipingID]:
    """Satu nomor line = satu pipa = SATU entri register, walau digambar di 2 tempat
    berjauhan (lazim di P&ID: label di kedua ujung 1 pipa). Cluster spatial tak menyatukan
    yg berjauhan → dedup GLOBAL by nomor line kanonik. Wakil = deteksi TERLENGKAP
    (utamakan yg ber-inch-mark, lalu terpanjang, lalu conf tertinggi); votes dijumlah."""
    def completeness(p):
        return ('"' in p.pid, len(p.pid), p.conf)
    best: dict = {}
    for p in items:
        k = _canon_key(p.pid)
        if k not in best:
            best[k] = p
        else:
            q = best[k]
            win = max((q, p), key=completeness)
            win.conf = q.conf + p.conf            # gabung bobot voting kedua label
            best[k] = win
    out = list(best.values())
    out.sort(key=lambda p: (-p.conf, p.pid))
    return out


# ------------------------------------------------------ overlay + IO ----------
def draw(img_bgr, items: list[PipingID]) -> np.ndarray:
    vis = img_bgr.copy()
    for p in items:
        col = (0, 0, 255) if p.manual else (0, 150, 0)
        cv2.rectangle(vis, (int(p.x1), int(p.y1)), (int(p.x2), int(p.y2)), col, 2)
        cv2.putText(vis, p.pid, (int(p.x1), int(p.y1) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
    return vis


def to_records(items: list[PipingID]) -> list[dict]:
    return [asdict(p) for p in items]


# ------------------------------------------- schema-by-example (parsing diajari user) ---
def pid_tokens(pid: str) -> list[str]:
    """Pecah pid (dinormalkan) jadi token '-' untuk pemilihan manual di GUI.
    Contoh: '650-1 1/2\"-DO-ACC-009' -> ['650','11/2\"','DO','ACC','009']."""
    return [t for t in _normalize(pid).split("-") if t]


def apply_example_schema(pid: str, schema: dict) -> dict:
    """Parse pid berdasar skema POSISIONAL yg diajari user (field -> indeks token).
    General: berlaku utk format perusahaan apa pun asal urutan token konsisten. Field yg
    tak dipetakan -> ''. Indeks di luar jangkauan (token lebih sedikit) -> ''."""
    toks = pid_tokens(pid)
    out = {k: "" for k in SCHEMA_FIELDS}
    for f, idx in (schema or {}).items():
        if f in out and isinstance(idx, int) and 0 <= idx < len(toks):
            out[f] = toks[idx]
    return out


# ------------------------------------------------------ OCR region (rubber-band) --------
def ocr_region(img_bgr, x1, y1, x2, y2, pad=6):
    """OCR HANYA di kotak (x1,y1,x2,y2) — utk fitur 'kotakkan piping ID yg tak terdeteksi'.
    Multi-sudut (0/90/270) + upscale crop kecil. Return (teks_terbaca, matched_schema_bool).
    Teks = pid kanonik bila cocok schema/generik, else gabungan fragmen ternormalkan."""
    eng = get_rapid()
    H, W = img_bgr.shape[:2]
    x1i, y1i = max(0, int(min(x1, x2)) - pad), max(0, int(min(y1, y2)) - pad)
    x2i, y2i = min(W, int(max(x1, x2)) + pad), min(H, int(max(y1, y2)) + pad)
    crop = img_bgr[y1i:y2i, x1i:x2i]
    if crop.size == 0:
        return "", False
    if crop.shape[0] < 60:                        # bikin teks kecil terbaca
        f = 60 / crop.shape[0]
        crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    best, best_matched = "", False
    for ang in (0, 90, 270):
        rot, _ = _rotate(crop, ang)
        try:
            res, _ = eng(cv2.cvtColor(rot, cv2.COLOR_BGR2RGB))
        except Exception:
            res = None
        if not res:
            continue
        joined = _normalize("-".join(t for _b, t, _s in res))
        pid, w = match_pid(joined)
        cand = pid or joined
        matched = pid is not None
        # pilih kandidat: prioritas yg match schema, lalu yg terpanjang
        if (matched and not best_matched) or (matched == best_matched and len(cand) > len(best)):
            best, best_matched = cand, matched
    return best, best_matched
