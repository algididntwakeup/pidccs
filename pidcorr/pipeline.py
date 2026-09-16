"""
Orkestrator Fitur 1: satukan deteksi simbol + piping ID + line tracing + asosiasi
menjadi SATU hasil (dict plain -> mudah di-JSON & diedit GUI/koreksi manual).

Hasil:
  {
    "image_path", "dpi", "rot", "w", "h",     # rot = rotasi manual (0/90/180/270, searah jarum jam)
    "symbols":   [{coarse, cls, conf, x1,y1,x2,y2}],            # equipment/instrument/valve
    "runs":      [{x1,y1,x2,y2, axis}],                          # run pipa (line tracing)
    "piping_ids":[{pid, x1,y1,x2,y2, unit,size,fluid,pclass,seq, conf, run_idx, state, manual}],
  }
run_idx = indeks ke runs[] (pipa yg ditunjuk piping ID), -1 = belum.
state in {attached, leader, none, manual}.
"""
from __future__ import annotations
import os, re, json
import numpy as np, cv2, fitz

from .piping_id import detect_piping_ids, get_rapid, PipingID, parse_tokens
from .lines import extract_pipe_runs, associate, detect_boxes
from .layout import detect_furniture, detect_fullpage, suppress_nested
from .subtype import classify_instruments, classify_valves_equipment, classify_valve_crops

_EQ_TAG = re.compile(r'\d{2,3}-[A-Z]{1,3}-\d{2,3}[A-Z]?')   # tag equipment: 605-K-101A, 605-V-201
_EQ_KW = ("PUMP", "TANK", "DRUM", "VESSEL", "EXCHANGER", "COOLER", "HEATER", "COMPRESSOR",
          "FILTER", "COLUMN", "TOWER", "SEPARATOR", "SCRUBBER", "RECEIVER", "REACTOR", "POT",
          "SKID", "PACKAGE", "UNIT", "CONSOLE", "COALESCER", "LAUNCHER", "BLOWER", "CATCHER")


def merge_equipment(symbols, overlap=0.25):
    """Gabung deteksi EQUIPMENT yang saling tumpang-tindih menjadi SATU box (union), secara
    ITERATIF/transitif (A∪B lalu bisa menyerap C). Satu equipment fisik sering terdeteksi
    berkali-kali (detektor tiled + full-page + kontur + struktur internalnya) -> tanpa ini
    muncul banyak kotak menumpuk di 1 equipment. Kriteria: irisan > `overlap` × luas box
    TERKECIL (equipment yang benar-benar terpisah jarang tumpang-tindih sebesar itu)."""
    eqs = [dict(s) for s in symbols if s.get("coarse") == "equipment"]
    others = [s for s in symbols if s.get("coarse") != "equipment"]
    area = lambda s: max(0, s["x2"] - s["x1"]) * max(0, s["y2"] - s["y1"])
    changed = True
    while changed and len(eqs) > 1:
        changed = False
        out = []
        for e in sorted(eqs, key=area, reverse=True):
            hit = None
            for m in out:
                ix = max(0, min(e["x2"], m["x2"]) - max(e["x1"], m["x1"]))
                iy = max(0, min(e["y2"], m["y2"]) - max(e["y1"], m["y1"]))
                small = min(area(e), area(m))
                if small > 0 and (ix * iy) / small > overlap:
                    hit = m; break
            if hit is None:
                out.append(e)
            else:
                hit["x1"] = min(hit["x1"], e["x1"]); hit["y1"] = min(hit["y1"], e["y1"])
                hit["x2"] = max(hit["x2"], e["x2"]); hit["y2"] = max(hit["y2"], e["y2"])
                if not (hit.get("subtype") or "").strip() and (e.get("subtype") or "").strip():
                    hit["subtype"] = e["subtype"]
                changed = True
        eqs = out
    return others + eqs


def classify_boxes(img_bgr, boxes, progress=None):
    """OCR label tiap KOTAK tertutup -> pisah EQUIPMENT vs DETAIL inset. WHITELIST supaya AMAN
    (tak menambah sampah): kotak jadi equipment HANYA bila yakin — ada TAG equipment (605-K-101A)
    ATAU kata kunci equipment (PUMP/TANK/POT/UNIT/COMPRESSOR/...). Kotak ber-'DETAIL' (toleran
    salah-baca 'DETAL'/'DTL') = inset gambar utama -> buang kontennya. Kotak lain (tak yakin) =
    hanya outline-suppress, TIDAK ditambah. Return (eq=[(box,name)], detail=[box])."""
    eng = get_rapid()
    eq, detail = [], []
    for b in boxes:
        x0, y0, x1, y1 = b
        crop = img_bgr[int(y0):int(y1), int(x0):int(x1)]
        texts = []
        if crop.size:
            try:
                res, _ = eng(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                texts = [t.upper().strip() for _bb, t, _s in (res or [])]
            except Exception:
                texts = []
        up = " ".join(texts)
        if "DETA" in up or "DTL" in up:                 # detail inset (toleran OCR DETAIL/DETAL)
            detail.append(b); continue
        tag = None
        for t in texts:
            m = _EQ_TAG.search(t.replace(" ", ""))
            if m:
                tag = m.group(0); break
        kw = next((k for k in _EQ_KW if k in up), None)
        if not (tag or kw):                             # tak yakin equipment -> jangan tambah
            continue
        words = [t.title() for t in texts if len(t) >= 2 and "NOTE" not in t
                 and t.replace(" ", "").replace("-", "").isalpha()]
        eq.append((b, tag or " ".join(words[:4]) or (kw.title() if kw else "Equipment")))
    return eq, detail

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # folder project
FINETUNE_WEIGHTS = os.path.join(_ROOT, "runs", "detect", "pid3_finetune", "weights", "best.pt")
LAYOUT_WEIGHTS = os.path.join(_ROOT, "runs", "detect", "layout_furniture", "weights", "best.pt")
EQUIP_BIG_WEIGHTS = os.path.join(_ROOT, "runs", "detect", "equip_big", "weights", "best.pt")
PID20_WEIGHTS = os.path.join(_ROOT, "runs", "detect", "pid20_baseline", "weights", "best.pt")
VALVE_CLS_WEIGHTS = os.path.join(_ROOT, "runs", "classify", "valve_cls", "weights", "best.pt")


def load_image(path: str, dpi: int = 350) -> np.ndarray:
    """PDF -> render halaman 1 (BGR) menggunakan pypdfium2 / PyMuPDF; selain itu -> baca sebagai citra."""
    if path.lower().endswith(".pdf"):
        # Coba pypdfium2 terlebih dahulu (thread-safe, Python 3.14 safe, permissive Apache-2.0)
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(path)
            page = pdf[0]
            pil_img = page.render(scale=dpi / 72.0).to_pil()
            rgb = np.array(pil_img)
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except ImportError:
            # Fallback ke fitz
            pg = fitz.open(path)[0]
            pix = pg.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    # Baca file citra dengan dukungan Windows non-ASCII path yang aman
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is not None:
            return img
    except Exception:
        pass
    return cv2.imread(path)



# --------------------------------------------------------------- orientasi ----
# Sebagian P&ID digambar MIRING di atas halaman potret tanpa menyetel flag /Rotate,
# jadi get_pixmap merendernya menyamping. Deteksi simbol & OCR tahan terhadap ini
# (diukur: tidak ada selisih), tetapi line tracing tidak — penekan garis non-pipa
# kehilangan sasarannya dan jumlah run melonjak. Rotasi karenanya disimpan bersama
# hasil, supaya setiap modul yang memuat ulang gambar memakai orientasi yang sama.
_ROT_CV = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def rotate_bgr(img: np.ndarray, rot: int) -> np.ndarray:
    """Putar searah jarum jam sebesar rot derajat (kelipatan 90). Lossless."""
    rot = int(rot) % 360
    return img if rot == 0 else cv2.rotate(img, _ROT_CV[rot])


def load_image_oriented(path: str, dpi: int = 350, rot: int = 0) -> np.ndarray:
    """load_image + rotasi manual yang tersimpan di hasil."""
    return rotate_bgr(load_image(path, dpi), rot)


def unrotate_matrix(rot: int, w: int, h: int):
    """Matriks fitz: koordinat piksel TERPUTAR -> piksel gambar asli.

    w, h = ukuran gambar SETELAH diputar. Dipakai export PDF supaya annotation
    tetap mendarat di tempat yang benar pada halaman aslinya.
    """
    rot = int(rot) % 360
    if rot == 90:
        return fitz.Matrix(0, -1, 1, 0, 0, w - 1)
    if rot == 180:
        return fitz.Matrix(-1, 0, 0, -1, w - 1, h - 1)
    if rot == 270:
        return fitz.Matrix(0, 1, -1, 0, h - 1, 0)
    return fitz.Matrix(1, 0, 0, 1, 0, 0)


def _detect_symbols(img, weights, conf=0.3, progress=None):
    try:
        import torch
        from ultralytics import YOLO
        from .detect import predict_tiled
        if not os.path.exists(weights):
            return []
        if torch.cuda.is_available():
            torch.zeros(1, device="cuda")
        dev = 0 if torch.cuda.is_available() else "cpu"
        model = YOLO(weights)
        dets, _ = predict_tiled(model, img, tile=640, overlap=0.2, conf=conf, device=dev)
        return [{"coarse": d.coarse, "cls": d.cls, "conf": round(d.conf, 3),
                 "x1": d.x1, "y1": d.y1, "x2": d.x2, "y2": d.y2} for d in dets]
    except Exception as e:
        if progress:
            progress(f"deteksi simbol dilewati: {e}")
        return []


def run_pipeline(img_bgr, image_path="", dpi=350, rot=0, weights=FINETUNE_WEIGHTS,
                 pids_cache=None, do_symbols=True, progress=None) -> dict:
    H, W = img_bgr.shape[:2]
    say = progress or (lambda *a: None)

    # 1) piping ID (boleh dari cache utk skip OCR yg lama). Cache DIBERI VERSI: kalau logika
    #    deteksi/parse berubah (PIDS_CACHE_VERSION naik), cache lama diabaikan -> OCR ulang.
    from .piping_id import PIDS_CACHE_VERSION
    pids, tokens = None, []
    if pids_cache and os.path.exists(pids_cache):
        say("memuat piping ID dari cache...")
        try:
            with open(pids_cache, encoding="utf-8") as f:
                data = json.load(f)
            # Koordinat di cache terikat pada GEOMETRI citra saat OCR dijalankan: orientasi
            # DAN ukuran piksel. Memakainya pada gambar yang diputar atau dirender pada DPI
            # lain akan menempatkan setiap kotak di tempat yang salah, jadi keduanya
            # diperiksa seperti halnya versi cache.
            H0, W0 = img_bgr.shape[:2]
            same_geom = (int(data.get("rot", 0)) % 360 == int(rot) % 360
                         and int(data.get("w", W0)) == W0
                         and int(data.get("h", H0)) == H0)
            if isinstance(data, dict) and data.get("v") == PIDS_CACHE_VERSION and same_geom:
                pids = [PipingID(**d) for d in data["pids"]]
                tokens = data.get("tokens", [])
            elif not same_geom:
                say("cache piping ID dibuat pada geometri citra lain — OCR ulang...")
                pids = None
            else:
                say("cache piping ID versi lama — OCR ulang...")   # format/versi beda
                pids = None
        except Exception:
            say("cache piping ID rusak — OCR ulang...")   # jangan crash; regen saja
            pids = None
    if pids is None:
        say("warmup OCR + deteksi piping ID (bisa beberapa menit)...")
        get_rapid()(np.full((60, 160, 3), 255, np.uint8))
        tokens = []
        # tokens_out: token pendek utk deteksi connection point — ikut pass OCR yg sama
        pids = detect_piping_ids(img_bgr, progress=lambda d, t: say(f"OCR tile {d}/{t}"),
                                 tokens_out=tokens)
        if pids_cache:
            from dataclasses import asdict
            tmp = pids_cache + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": PIDS_CACHE_VERSION, "rot": int(rot) % 360,
                           "w": int(img_bgr.shape[1]), "h": int(img_bgr.shape[0]),
                           "pids": [asdict(p) for p in pids], "tokens": tokens}, f)
            os.replace(tmp, pids_cache)

    # 2) simbol dulu (dipakai utk buang tepi-box saat tracing) — cepat, GPU
    symbols = []
    if do_symbols:
        say("deteksi equipment/instrument/valve (YOLO)...")
        symbols = _detect_symbols(img_bgr, weights, progress=say)

    # 2b) equipment BESAR (vessel/exchanger/kolom) full-page — objek > tile 640 tak pernah
    #     utuh di detektor tiled; model khusus ini menutupnya. Merge: buang box equipment
    #     tiled yg pusatnya tertelan box besar (parsial), tambah box besar sbg equipment.
    if do_symbols:
        say("deteksi equipment besar (full-page)...")
        # conf 0.25 (bukan 0.4): asset register butuh RECALL tinggi — FP (~1/sheet) mudah
        # dihapus user di GUI, equipment terlewat jauh lebih mahal (terukur di val:
        # recall process equipment 0.6 @0.4 -> 8/8 @0.25)
        big = suppress_nested(
            detect_fullpage(img_bgr, EQUIP_BIG_WEIGHTS, conf=0.25, with_conf=True))
        if big:
            def _swallowed(d):
                cx, cy = (d["x1"] + d["x2"]) / 2, (d["y1"] + d["y2"]) / 2
                return d["coarse"] == "equipment" and any(
                    x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1, _ in big)
            symbols = [d for d in symbols if not _swallowed(d)]
            symbols += [{"coarse": "equipment", "cls": "equipment_big", "conf": round(cf, 3),
                         "x1": x0, "y1": y0, "x2": x1, "y2": y1}
                        for x0, y0, x1, y1, cf in big]

    # 2d) furniture (title block/tabel/notes) — dihitung DULUAN krn dipakai mengecualikan
    #     kotak & piping ID di area non-gambar.
    say("deteksi furniture (title block/tabel)...")
    furniture = detect_furniture(img_bgr, LAYOUT_WEIGHTS)

    # 2b2) KOTAK tertutup (kontur, andal utk box-shaped equipment yg sering di-miss YOLO):
    #      equipment kotak (K-101A/DRAIN POT/LUBE OIL UNIT/tank) DITAMBAH ke register;
    #      DETAIL/NOTE inset hanya utk suppress outline + buang konten duplikatnya.
    #      Kecualikan kotak yg pusatnya di title block (sel tabel referensi, bukan aset).
    boxes = suppress_nested(detect_boxes(img_bgr, dpi=dpi))    # buang kotak bersarang
    if furniture:
        def _box_in_furn(b):
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in furniture)
        boxes = [b for b in boxes if not _box_in_furn(b)]
    # Kotak KECIL = kotak anotasi / off-page connector ("lihat gambar 695-006-01") /
    # kotak referensi 2-baris (RH + no. dwg) / kotak keterangan. Semuanya BUKAN pipa:
    # seluruh areanya jadi zona NO-TRACE (outline + garis pembagi di dalamnya).
    S = dpi / 72.0
    small_boxes = [b for b in boxes if min(b[2] - b[0], b[3] - b[1]) < 34 * S]
    boxes = [b for b in boxes if min(b[2] - b[0], b[3] - b[1]) >= 34 * S]
    detail_boxes = []
    if do_symbols and boxes:
        say("klasifikasi kotak (equipment vs detail inset)...")
        eq_boxes, detail_boxes = classify_boxes(img_bgr, boxes, progress=say)
        for bx, name in eq_boxes:
            x0, y0, x1, y1 = bx
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            dup = any(s["coarse"] == "equipment" and (
                        (s["x1"] <= cx <= s["x2"] and s["y1"] <= cy <= s["y2"]) or
                        (x0 <= (s["x1"] + s["x2"]) / 2 <= x1 and y0 <= (s["y1"] + s["y2"]) / 2 <= y1))
                      for s in symbols)
            if not dup:
                symbols.append({"coarse": "equipment", "cls": "equipment_box", "conf": 0.99,
                                "x1": int(x0), "y1": int(y0), "x2": int(x1), "y2": int(y1),
                                "subtype": name or "Equipment (kotak)"})
        if detail_boxes:                                       # buang simbol dlm DETAIL inset (duplikat)
            def _in_detail(s):
                cx = (s["x1"] + s["x2"]) / 2; cy = (s["y1"] + s["y2"]) / 2
                return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in detail_boxes)
            symbols = [s for s in symbols if not _in_detail(s)]
    if do_symbols:
        symbols = merge_equipment(symbols)     # 1 equipment fisik = 1 kotak (bukan banyak)

    # 2c) SUBTYPE simbol -> kelas bermakna (bukan sekadar 'instrument'/'valve'):
    #     valve via classifier bentuk in-domain (valve_cls, dilatih dari drawing
    #     PetroChina sendiri); instrument via OCR tag + ISA-5.1 (deterministik);
    #     equipment via saran model baseline 20-kelas (Pump, Exchanger, ...).
    if do_symbols and symbols:
        say("klasifikasi jenis valve (classifier bentuk)...")
        classify_valve_crops(img_bgr, symbols, VALVE_CLS_WEIGHTS, progress=say)
        say("klasifikasi jenis equipment (baseline 20-kelas)...")
        classify_valves_equipment(img_bgr, symbols, PID20_WEIGHTS, targets=("equipment",))
        say("klasifikasi jenis instrument (OCR tag ISA-5.1)...")
        classify_instruments(img_bgr, symbols, progress=say)

    # 2e) BUANG piping ID yg jatuh di dalam title block/notes/tabel (referensi dokumen, bukan
    #     label pipa) ATAU di dalam DETAIL inset (duplikat gambar utama). Jaga hasil fokus ke pipa.
    zones = list(furniture or []) + list(detail_boxes or [])
    if zones and pids:
        def _in_zone(p):
            cx, cy = (p.x1 + p.x2) / 2, (p.y1 + p.y2) / 2
            return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in zones)
        n0 = len(pids)
        pids = [p for p in pids if not _in_zone(p)]
        if len(pids) < n0:
            say(f"buang {n0 - len(pids)} teks di title block/notes/detail (bukan piping ID pipa)")

    # 3) line tracing (graph: segmen -> buang tepi-box -> buang furniture -> buang OUTLINE
    #    kotak (equipment/detail/note) -> merge) + asosiasi
    say("line tracing + asosiasi piping ID -> pipa...")
    #    zona no-trace = furniture (title block/tabel) + DETAIL inset + kotak anotasi kecil
    zones = list(furniture or []) + list(detail_boxes or []) + list(small_boxes or [])
    runs = extract_pipe_runs(img_bgr, dpi=dpi, detections=symbols, furniture=zones, boxes=boxes)
    assoc = associate(pids, runs, img_bgr, dpi=dpi)
    run_index = {id(r): i for i, r in enumerate(runs)}

    pid_recs = []
    for a in assoc:
        p = pids[a["pid_idx"]]
        ri = run_index.get(id(a["run"]), -1) if a["run"] is not None else -1
        rec = {"pid": p.pid, "x1": float(p.x1), "y1": float(p.y1),
               "x2": float(p.x2), "y2": float(p.y2),
               "unit": p.unit, "size": p.size, "fluid": p.fluid, "pclass": p.pclass,
               "seq": p.seq, "conf": int(p.conf), "run_idx": int(ri), "state": a["state"],
               "manual": False, "extra_runs": []}
        pid_recs.append(rec)

    pid_recs = _dedup_pid_recs(pid_recs)          # 1 nomor line = 1 entri, run digabung

    run_recs = [{"points": [[int(x), int(y)] for x, y in r.points], "axis": r.axis,
                 "x1": int(r.x1), "y1": int(r.y1), "x2": int(r.x2), "y2": int(r.y2),
                 "underline": bool(r.underline)}
                for r in runs]

    # garis bawah teks (judul/tag equipment/catatan) yang lolos tracing -> tandai bukan pipa
    try:
        from .propagate import mark_text_underlines
        n_ul = mark_text_underlines(img_bgr, run_recs, dpi=dpi)
        if n_ul:
            say(f"{n_ul} garis bawah teks ditandai bukan pipa")
    except Exception as e:
        say(f"deteksi garis bawah teks dilewati: {e}")

    # 4) connection point (spec break) — batas circuit yang ditulis eksplisit di drawing.
    #    Rule-based di atas token OCR yg sudah ada: tanpa anotasi & tanpa training.
    conn = []
    if tokens:
        say("deteksi connection point (spec break)...")
        try:
            from .connpoint import find_connection_points, class_vocab
            vocab = class_vocab({(p.get("pclass") or "").upper()
                                 for p in pid_recs if p.get("pclass")})
            conn = find_connection_points(tokens, run_recs, img_bgr, dpi=dpi, vocab=vocab)
            say(f"connection point: {len(conn)} ditemukan")
        except Exception as e:
            say(f"deteksi connection point dilewati: {e}")

    out = {"image_path": image_path, "dpi": dpi, "rot": int(rot) % 360,
           "w": int(W), "h": int(H),
           "symbols": symbols,
           "furniture": [[int(v) for v in b] for b in furniture],
           "runs": run_recs,
           "conn_points": conn,
           "piping_ids": pid_recs}

    # 5) pecah run tepat di connection point -> batas warna jatuh di titik spec break
    if conn:
        from .propagate import split_at_connection_points
        n0 = len(out["runs"])
        split_at_connection_points(out, dpi=dpi)
        if len(out["runs"]) != n0:
            say(f"run dipecah di spec break: {n0} -> {len(out['runs'])}")

    # 6) deteksi off-page connector (OPC)
    opcs = []
    try:
        from .opc_detector import detect_off_page_connectors
        opcs = detect_off_page_connectors(out, tokens=tokens, dpi=dpi)
        if opcs:
            say(f"off-page connector: {len(opcs)} ditemukan")
    except Exception:
        pass
    out["opcs"] = opcs

    say("selesai.")
    return out


def _dedup_pid_recs(pid_recs: list) -> list:
    """Satu nomor line ditulis di 2 tempat (kedua ujung 1 pipa) = SATU entri asset register,
    TAPI run dari SEMUA label digabung (run_idx primer + extra_runs) supaya kedua segmen pipa
    tetap terwarnai di marking. Wakil entri = deteksi terlengkap (ber-inch-mark > terpanjang >
    conf); votes dijumlah. Nomor line kanonik = pid tanpa inch-mark/spasi (samakan varian OCR)."""
    from .piping_id import _canon_key
    def comp(r):
        return ('"' in r["pid"], len(r["pid"]), r["conf"])
    merged: dict = {}
    for rec in pid_recs:
        k = _canon_key(rec["pid"])
        if k not in merged:
            merged[k] = rec
            continue
        keep = merged[k]
        allruns = set()
        for rr in (keep, rec):
            if rr.get("run_idx", -1) >= 0:
                allruns.add(rr["run_idx"])
            allruns.update(rr.get("extra_runs", []))
        win = keep if comp(keep) >= comp(rec) else rec
        win["conf"] = keep["conf"] + rec["conf"]
        primary = win["run_idx"] if win.get("run_idx", -1) >= 0 else (min(allruns) if allruns else -1)
        win["run_idx"] = primary
        win["extra_runs"] = sorted(allruns - ({primary} if primary >= 0 else set()))
        if primary >= 0 and win.get("state") in (None, "none"):
            win["state"] = "attached"
        merged[k] = win
    out = list(merged.values())
    out.sort(key=lambda r: (-r["conf"], r["pid"]))
    return out


def _np_default(o):
    """Konversi tipe numpy (intc/int32/float32/array) -> tipe Python untuk JSON."""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"{type(o).__name__} tidak bisa di-JSON-kan")


def is_manual_symbol(s) -> bool:
    """Simbol yang DITAMBAH/dikoreksi MANUAL user di GUI (bukan hasil detektor).

    Ada DUA jejak koreksi dan keduanya harus dikenali. Kotak yang digeser atau diubah
    ukurannya diberi field `manual` oleh GUI sementara nama kelasnya tidak berubah,
    sedangkan kotak yang digambar lewat rubber-band memperoleh nama kelas ber-akhiran
    '_box'. Memeriksa nama kelas saja melewatkan bentuk yang pertama — pada cache proyek
    ini hanya 1 dari 23 koreksi yang terkenali.
    """
    if s.get("manual"):
        return True
    cls = (s.get("cls") or "").lower()
    return "manual" in cls or "box" in cls


def _iou(a, b) -> float:
    ix = max(0, min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]))
    iy = max(0, min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]))
    inter = ix * iy
    ua = max(0, a["x2"] - a["x1"]) * max(0, a["y2"] - a["y1"])
    ub = max(0, b["x2"] - b["x1"]) * max(0, b["y2"] - b["y1"])
    return inter / (ua + ub - inter) if (ua + ub - inter) > 0 else 0.0


def merge_manual_symbols(new_result: dict, old_result: dict, iou_thr=0.45) -> int:
    """Pertahankan simbol yang DITAMBAH MANUAL user saat deteksi ULANG: append simbol
    manual dari `old_result` ke `new_result` bila belum tercakup deteksi baru (IoU rendah).
    Deteksi ulang meng-generate simbol dari nol -> tanpa ini, kotak equipment manual user
    (mis. 'LUBE OIL COOLER') HILANG. Return jumlah yang dipulihkan."""
    if not old_result:
        return 0
    news = new_result.setdefault("symbols", [])
    n = 0
    for s in old_result.get("symbols", []):
        if not is_manual_symbol(s):
            continue
        if any(_iou(s, t) >= iou_thr for t in news):
            continue                              # sudah ada padanannya di deteksi baru
        news.append(dict(s)); n += 1
    return n


def save_result(result: dict, path: str):
    """Tulis ATOMIK: ke file .tmp dulu lalu os.replace. Kalau proses terputus di tengah
    (error serialisasi / app tertutup), file asli TIDAK pernah korup separuh."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=_np_default)
    os.replace(tmp, path)


def load_result(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
