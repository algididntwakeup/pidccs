"""
Unmark P&ID CCD -> P&ID kosongan (faithful), via rekonstruksi vektor.

Latar belakang (terbukti dari data PetroChina, lihat memory unmark-method):
  - Marking corrosion = stroke vektor BERWARNA hasil RECOLOR garis pipa hitam
    (bukan overlay) + kotak metadata CCD (filled-rect berwarna + teks).
  - Stroke marking lebih TEBAL dari garis pipa asli, jadi tidak boleh sekadar
    dihitamkan (pipa jadi gemuk) maupun dihapus (pipa jadi bolong).
  - Solusi: gambar ulang halaman dari objek vektor:
      * objek hitam asli  -> dipertahankan apa adanya (termasuk dash/signal line),
      * stroke marking pipa -> digambar ulang HITAM pada LEBAR PIPA ASLI,
      * fill berwarna (kotak CCD) + stroke anotasi tipis -> dibuang,
      * text layer (label CCD) -> hilang otomatis (tidak di-redraw).
  - Halaman bisa ber-rotasi (mis. 270); get_drawings memakai koordinat unrotated
    (mediabox), jadi page baru dibuat pada dimensi mediabox lalu di-set rotasinya.
"""
from __future__ import annotations
import math
import statistics
from dataclasses import dataclass
import numpy as np
import cv2
import fitz  # PyMuPDF


# ---------------------------------------------------------------- helpers ----
def is_marking_color(c, sat_thresh: float = 0.15) -> bool:
    """True bila warna tersaturasi (marking corrosion), bukan hitam/abu/putih."""
    return c is not None and (max(c) - min(c)) > sat_thresh


def _seg_length(items) -> float:
    """Total panjang segmen garis lurus ('l') dalam satu path (pt)."""
    tot = 0.0
    for it in items:
        if it[0] == "l":
            tot += math.dist((it[1].x, it[1].y), (it[2].x, it[2].y))
    return tot


def median_pipe_width(page, default: float = 0.72) -> float:
    """Median lebar stroke non-marking = lebar garis pipa asli (pt)."""
    widths = [
        d["width"]
        for d in page.get_drawings()
        if d.get("width") and not is_marking_color(d.get("color"))
        and not is_marking_color(d.get("fill"))
    ]
    return statistics.median(widths) if widths else default


@dataclass
class PageStats:
    index: int
    rotation: int
    kept: int = 0
    redrawn_pipe: int = 0
    dropped: int = 0
    pipe_width: float = 0.0


# ------------------------------------------------------------- core redraw ----
def _redraw_page(src_page, out_doc, pipe_width: float | None = None) -> PageStats:
    """Rekonstruksi satu halaman unmarked ke out_doc. Mengembalikan statistik."""
    if pipe_width is None:
        pipe_width = median_pipe_width(src_page)

    mb = src_page.mediabox
    npg = out_doc.new_page(width=mb.width, height=mb.height)
    # NOTE: diasumsikan mediabox berawal (0,0) (benar utk dataset ini).
    # Bila tidak, koordinat get_drawings akan ter-offset -> beri peringatan.
    if abs(mb.x0) > 1e-6 or abs(mb.y0) > 1e-6:
        st_warn = f"[WARN] page {src_page.number}: mediabox origin {(mb.x0, mb.y0)} != 0 (offset belum ditangani)"
        print(st_warn)
    shp = npg.new_shape()
    st = PageStats(index=src_page.number, rotation=src_page.rotation, pipe_width=pipe_width)

    # ambang pemisah: stroke marking PIPA vs anotasi tipis (leader/border)
    pipe_thresh_w = max(1.0, 1.3 * pipe_width)

    for d in src_page.get_drawings():
        col, fil = d.get("color"), d.get("fill")
        items = d["items"]

        def paint():
            for it in items:
                op = it[0]
                if op == "l":
                    shp.draw_line(it[1], it[2])
                elif op == "c":
                    shp.draw_bezier(it[1], it[2], it[3], it[4])
                elif op == "re":
                    shp.draw_rect(it[1])
                elif op == "qu":
                    shp.draw_quad(it[1])

        if is_marking_color(col) or is_marking_color(fil):
            if is_marking_color(fil):                 # kotak metadata CCD -> buang
                st.dropped += 1
                continue
            w = d.get("width") or 0
            if w >= pipe_thresh_w or _seg_length(items) > 40:   # pipa marking
                paint()
                # PENTING: closePath=False. Default PyMuPDF True akan menutup
                # polyline pipa (ujung->awal) -> garis diagonal palsu.
                shp.finish(color=(0, 0, 0), fill=None, width=pipe_width,
                           closePath=False)
                st.redrawn_pipe += 1
            else:                                      # leader/border tipis -> buang
                st.dropped += 1
        else:                                          # objek asli -> pertahankan
            paint()
            shp.finish(
                color=col, fill=fil,
                width=(d.get("width") or pipe_width),
                closePath=d.get("closePath", False),
                even_odd=d.get("even_odd", True),
                dashes=d.get("dashes"),
            )
            st.kept += 1

    shp.commit()
    npg.set_rotation(src_page.rotation)   # samakan orientasi dengan halaman asli
    return st


# ------------------------------------------------------------------ public ----
def unmark_document(src_path: str) -> tuple[fitz.Document, list[PageStats]]:
    """Buka PDF CCD, kembalikan Document baru yang sudah di-unmark + statistik per halaman.

    Hanya menangani halaman VEKTOR. Untuk halaman raster gunakan unmark_page_image().
    """
    src = fitz.open(src_path)
    out = fitz.open()
    stats = [_redraw_page(pg, out) for pg in src]
    src.close()
    return out, stats


def render_doc_to_pngs(doc: fitz.Document, dpi: int = 300) -> list[fitz.Pixmap]:
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    return [pg.get_pixmap(matrix=mat, alpha=False) for pg in doc]


# -------------------------------------------------- deteksi raster vs vektor ----
def largest_image_coverage(page) -> tuple[float, int | None]:
    """Cakupan image terbesar relatif luas halaman + xref-nya."""
    parea = page.rect.width * page.rect.height
    best_cov, best_xref = 0.0, None
    info = page.get_image_info(xrefs=True)
    for im in info:
        r = fitz.Rect(im["bbox"])
        cov = (r.width * r.height) / parea if parea else 0
        if cov > best_cov:
            best_cov, best_xref = cov, im.get("xref")
    return best_cov, best_xref


def page_is_raster(page, cover_thresh: float = 0.4) -> bool:
    """True bila drawing utama = scan raster (image menutupi sebagian besar halaman)."""
    cov, _ = largest_image_coverage(page)
    if cov <= cover_thresh:
        return False
    nblack = sum(
        1 for d in page.get_drawings()
        if not is_marking_color(d.get("color")) and not is_marking_color(d.get("fill"))
    )
    return nblack < 200  # konten vektor minim -> drawing ada di image


def _rotate_rgb(arr: np.ndarray, rot: int) -> np.ndarray:
    rot %= 360
    if rot == 90:
        return cv2.rotate(arr, cv2.ROTATE_90_CLOCKWISE)
    if rot == 180:
        return cv2.rotate(arr, cv2.ROTATE_180)
    if rot == 270:
        return cv2.rotate(arr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return arr


def extract_scan_rgb(page) -> np.ndarray:
    """Ekstrak layer scan (image terbesar) sebagai RGB, sudah diputar sesuai rotasi halaman.

    Untuk file raster: ini = P&ID asli bersih (marking corrosion = overlay vektor yang
    dengan sendirinya tidak ikut karena kita hanya mengambil image-nya).
    """
    doc = page.parent
    _, xref = largest_image_coverage(page)
    d = doc.extract_image(xref)
    bgr = cv2.imdecode(np.frombuffer(d["image"], np.uint8), cv2.IMREAD_COLOR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return _rotate_rgb(rgb, page.rotation)


# --------------------------------------------------------- API tingkat-tinggi ----
def unmark_page_rgb(page, dpi: int = 300) -> tuple[np.ndarray, str, PageStats | None]:
    """Kembalikan citra RGB P&ID kosongan untuk satu halaman, mode auto.

    return (rgb, mode, stats)  mode in {"raster","vector"}; stats None utk raster.
    """
    if page_is_raster(page):
        return extract_scan_rgb(page), "raster", None
    tmp = fitz.open()
    st = _redraw_page(page, tmp)
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    px = tmp[0].get_pixmap(matrix=mat, alpha=False)
    rgb = np.frombuffer(px.samples, np.uint8).reshape(px.height, px.width, px.n)[:, :, :3].copy()
    tmp.close()
    return rgb, "vector", st
