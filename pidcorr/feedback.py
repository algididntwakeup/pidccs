"""
Human-in-the-loop feedback: koreksi kotak simbol/equipment di GUI -> sampel latih YOLO.

Setiap kali penulis MENYIMPAN koreksi di GUI, halaman P&ID + SELURUH kotak simbol (hasil
deteksi yang sudah dibetulkan + tambahan manual via rubber-band) diekspor sebagai satu
sampel training di `data/feedback/`. Sumber ini DIGABUNG saat melatih ulang detektor
(equip_big / finetune) -> makin sering dipakai, makin banyak contoh nyata terkoreksi,
makin pintar. Inilah jawaban atas keresahan "deteksi equipment tak kunjung baik": alih-alih
menunggu dataset besar sekali jadi, model belajar terus dari pemakaian.

Taksonomi kelas SELARAS `data/finetune/data.yaml`: equipment=0, instrument=1, valve=2.
Piping ID TIDAK diekspor ke sini (deteksinya berbasis OCR teks, bukan model kotak).

Format: 1 gambar `images/<stem>.png` + 1 label YOLO `labels/<stem>.txt`
(`cls cx cy w h`, ternormalisasi). Menyimpan ulang drawing yang sama MENIMPA sampel lama
(koreksi terbaru selalu menang) -> tidak menumpuk duplikat, satu drawing = satu sampel.
"""
import os
import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_DIR = os.path.join(ROOT, "data", "feedback")
COARSE_IDX = {"equipment": 0, "instrument": 1, "valve": 2}
NAMES = ["equipment", "instrument", "valve"]


def _safe_stem(image_path):
    stem = os.path.splitext(os.path.basename(image_path or "page"))[0]
    return stem.replace(".", "_") or "page"


def _remove_sample(stem):
    """Tarik sampel latih sebuah drawing. Return True bila memang ada yang dihapus."""
    gone = False
    for sub, ext in (("images", ".png"), ("labels", ".txt")):
        p = os.path.join(FEEDBACK_DIR, sub, stem + ext)
        if os.path.exists(p):
            try:
                os.remove(p); gone = True
            except OSError:
                pass
    return gone


def export_sample(result, img_bgr=None, dpi=350, require_reviewed=True):
    """Tulis 1 sampel latih dari `result` (dict hasil GUI). Menimpa sampel drawing yang
    sama. Return (n_box, stem). n_box=0 bila tak ada kotak simbol / ukuran tak diketahui.

    HANYA drawing yang ditandai 'Reviewed' yang diekspor. Alasannya diukur: tanpa syarat ini
    98,9 persen kotak yang masuk data latih adalah keluaran detektor yang tak pernah dilihat
    manusia, sehingga melatih ulang berarti mengajari model mengulangi kesalahannya sendiri —
    pada percobaan pertama presisi valve turun 22 poin. Menandai Reviewed adalah satu-satunya
    tindakan eksplisit yang menyatakan seluruh kotak di halaman itu sudah diperiksa.
    """
    W, H = result.get("w"), result.get("h")
    if not W or not H:
        return 0, None
    if require_reviewed and not result.get("reviewed"):
        # Review boleh DICABUT: kalau besok ternyata ada koreksi yang keliru, tanda Reviewed
        # dilepas, dan sampel latih drawing ini harus ikut ditarik. Membiarkannya berarti
        # model tetap dilatih dari versi yang sudah dinyatakan tidak benar.
        _remove_sample(_safe_stem(result.get("image_path")))
        return 0, None
    syms = [s for s in result.get("symbols", []) if s.get("coarse") in COARSE_IDX]
    img_dir = os.path.join(FEEDBACK_DIR, "images")
    lbl_dir = os.path.join(FEEDBACK_DIR, "labels")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)
    stem = _safe_stem(result.get("image_path"))

    if img_bgr is None:
        from . import pipeline
        # orientasi harus sama dengan saat kotak dikoreksi, kalau tidak label
        # yang diekspor ke data latih akan meleset
        img_bgr = pipeline.load_image_oriented(result["image_path"], dpi=dpi,
                                               rot=result.get("rot", 0))
    # cv2.imencode+tofile: aman utk path non-ASCII (gotcha Windows)
    cv2.imencode(".png", img_bgr)[1].tofile(os.path.join(img_dir, stem + ".png"))

    lines = []
    for s in syms:
        x1, x2 = sorted((float(s["x1"]), float(s["x2"])))
        y1, y2 = sorted((float(s["y1"]), float(s["y2"])))
        w, h = (x2 - x1) / W, (y2 - y1) / H
        if w <= 0 or h <= 0:
            continue
        cx, cy = (x1 + x2) / 2 / W, (y1 + y2) / 2 / H
        lines.append(f"{COARSE_IDX[s['coarse']]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    tmp = os.path.join(lbl_dir, stem + ".txt.tmp")
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp, os.path.join(lbl_dir, stem + ".txt"))
    return len(lines), stem


def sample_count():
    """Berapa drawing yang sudah jadi data latih feedback."""
    lbl_dir = os.path.join(FEEDBACK_DIR, "labels")
    if not os.path.isdir(lbl_dir):
        return 0
    return sum(1 for f in os.listdir(lbl_dir) if f.endswith(".txt"))


def iter_samples():
    """Yield (image_path, label_path) tiap sampel feedback yang lengkap (gambar+label ada)."""
    img_dir = os.path.join(FEEDBACK_DIR, "images")
    lbl_dir = os.path.join(FEEDBACK_DIR, "labels")
    if not os.path.isdir(lbl_dir):
        return
    for lf in sorted(os.listdir(lbl_dir)):
        if not lf.endswith(".txt"):
            continue
        stem = lf[:-4]
        img = os.path.join(img_dir, stem + ".png")
        if os.path.exists(img):
            yield img, os.path.join(lbl_dir, lf)
