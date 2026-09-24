"""Phase 3 regression tests — PDF annotation Appearance Stream (/AP).

Viewer berbasis PDFium (Google Chrome, Microsoft Edge) TIDAK mensintesis tampilan
untuk anotasi yang tidak punya Appearance Stream. Bila `/AP` kosong atau berisi
default, garis pipa hasil export tampak tipis/salah warna — atau tidak terlihat.

Tes di sini mengunci tiga hal:
  1. setiap anotasi PolyLine punya `/AP` dengan `/N` yang dapat di-resolve,
  2. properti visual (warna, ketebalan, opasitas) benar-benar TER-BAKE ke dalam
     stream tersebut — bukan hanya tersimpan sebagai entri kamus anotasi,
  3. hasilnya benar-benar ter-render di PDFium (mesin yang sama dengan Chrome/Edge),
     pada halaman ber-`/Rotate` (kasus nyata P&ID).
"""
import os
import sys

import numpy as np
import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
for _p in (_BACKEND_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pymupdf
from pidcorr.export import export_marked_pdf
from _fixtures import fixture_path

_PDF_SRC = str(fixture_path("Contoh P&ID", "BCD3-605-42-PID-1-014-01 Rev.6-CCD2.pdf"))


def _build(tmp_path, rot=0, runs=None):
    """Export PDF engineer-mode dari result minimal; return path."""
    if not os.path.exists(_PDF_SRC):
        pytest.skip("reference vector PDF not available")
    runs = runs or [
        {"points": [[300, 400], [1500, 400]], "color": "#2563EB"},
        {"points": [[1500, 400], [1500, 1200]], "color": "#2563EB"},
        {"points": [[600, 1800], [2400, 1800]], "color": "#E11D48"},
    ]
    result = {
        "image_path": _PDF_SRC if os.path.exists(_PDF_SRC) else "",
        "dpi": 200, "rot": rot, "w": 3309, "h": 2339,
        "runs": runs,
        "piping_ids": [{"pid": "TEST-001", "run_idx": 0}],
    }
    out = str(tmp_path / "marked.pdf")
    n = export_marked_pdf(result, out, mode="engineer")
    return out, n


# ------------------------------------------------------------------ struktur ----
def test_every_polyline_annotation_has_resolvable_ap(tmp_path):
    """/AP harus ada DAN /N-nya menunjuk stream yang bisa dibaca."""
    out, n = _build(tmp_path)
    assert n == 3

    doc = pymupdf.open(out)
    page = doc[0]
    checked = 0
    for a in page.annots():
        raw = doc.xref_object(a.xref)
        assert "/AP" in raw, f"{a.type[1]} annotation {a.xref} has no /AP"

        key = doc.xref_get_key(a.xref, "AP/N")
        assert key[0] == "xref", f"/AP/N not resolvable for {a.type[1]}: {key}"
        ap_xref = int(key[1].split()[0])
        stream = doc.xref_stream(ap_xref)
        assert stream, f"/AP/N stream empty for {a.type[1]}"
        checked += 1
    doc.close()
    assert checked >= 3


def test_polyline_ap_bakes_stroke_color_and_width(tmp_path):
    """Warna & ketebalan harus tertulis di stream /AP, bukan default viewer."""
    out, _ = _build(tmp_path)
    doc = pymupdf.open(out)
    page = doc[0]

    found = []
    for a in page.annots():
        if a.type[1] != "PolyLine":
            continue
        ap_xref = int(doc.xref_get_key(a.xref, "AP/N")[1].split()[0])
        found.append(doc.xref_stream(ap_xref).decode("latin-1"))
    doc.close()

    assert found, "expected PolyLine annotations"
    for stream in found:
        # `w` = line width operator; harus > 1 (default viewer hanya 1).
        assert " w" in stream, f"no stroke width operator in /AP: {stream!r}"
        width = None
        for tok in stream.replace("\n", " ").split():
            try:
                val = float(tok)
            except ValueError:
                continue
            width = val if width is None else width
        # cari pola "<num> w"
        import re
        m = re.search(r"([\d.]+)\s+w\b", stream)
        assert m, f"stroke width not found in /AP: {stream!r}"
        assert float(m.group(1)) > 1.0, \
            f"stroke width {m.group(1)} looks like the viewer default, not baked"
        # operator warna stroke: "R G B RG"
        assert re.search(r"[\d.]+\s+[\d.]+\s+[\d.]+\s+RG\b", stream), \
            f"stroke colour operator (RG) missing in /AP: {stream!r}"
        # opacity via ExtGState: "/H gs" + Resources berisi CA/ca
        assert "/H gs" in stream, f"ExtGState not applied in /AP: {stream!r}"


def test_polyline_ap_uses_extgstate_for_opacity(tmp_path):
    """Opasitas di-bake lewat ExtGState (CA/ca), sesuai instruksi Phase 3."""
    out, _ = _build(tmp_path)
    doc = pymupdf.open(out)
    page = doc[0]
    res = None
    for a in page.annots():
        if a.type[1] != "PolyLine":
            continue
        ap_xref = int(doc.xref_get_key(a.xref, "AP/N")[1].split()[0])
        res = doc.xref_get_key(ap_xref, "Resources")
        break
    doc.close()

    assert res is not None, "PolyLine annotation not found"
    assert "ExtGState" in str(res[1]), f"no ExtGState in AP Resources: {res}"
    assert "/CA" in str(res[1]) and "/ca" in str(res[1]), \
        f"stroke/non-stroke alpha missing in ExtGState: {res}"


def test_annotations_still_editable_after_update(tmp_path):
    """update() tidak boleh mengunci anotasi — tetap bisa diedit di Acrobat."""
    out, _ = _build(tmp_path)
    doc = pymupdf.open(out)
    page = doc[0]
    for a in page.annots():
        flags = int(a.flags)
        # bit 1 (value 1) = Invisible, bit 7 (value 64) = Locked
        assert not (flags & 1), f"annotation {a.xref} is marked Invisible"
        assert not (flags & 64), f"annotation {a.xref} is marked Locked"
        assert flags & pymupdf.PDF_ANNOT_IS_PRINT, \
            f"annotation {a.xref} lost the Print flag"
    doc.close()


# -------------------------------------------------------------------- visual ----
@pytest.mark.skipif(not os.path.exists(_PDF_SRC),
                    reason="reference vector PDF not available")
def test_annotations_render_in_pdfium_on_rotated_page(tmp_path):
    """Uji inti Phase 3: garis TERLIHAT di PDFium (mesin Chrome/Edge).

    Halaman referensi ber-/Rotate 270, jadi ini sekaligus memverifikasi bahwa
    Appearance Stream dan transformasi rotasi bekerja bersama.
    """
    pdfium = pytest.importorskip("pypdfium2")
    out, _ = _build(tmp_path)

    pdf = pdfium.PdfDocument(out)
    page = pdf[0]
    assert page.get_rotation() == 270, "fixture must exercise the rotated-page path"

    def nonwhite(img):
        return int((np.asarray(img)[:, :, :3] < 240).any(axis=2).sum())

    without = nonwhite(page.render(scale=1.0, draw_annots=False).to_pil())
    with_ann = nonwhite(page.render(scale=1.0, draw_annots=True).to_pil())
    visible = with_ann - without

    assert visible > 0, "annotations are INVISIBLE in PDFium (no /AP synthesis)"
    # 3 run tebal 6 pt pada skala 1.0 -> ribuan piksel; ambang longgar tapi nyata.
    assert visible > 1000, f"only {visible} annotation pixels — suspiciously thin"


@pytest.mark.skipif(not os.path.exists(_PDF_SRC),
                    reason="reference vector PDF not available")
def test_annotation_positions_match_requested_runs(tmp_path):
    """Posisi anotasi harus tepat di koordinat run yang diminta (px -> pt)."""
    pdfium = pytest.importorskip("pypdfium2")
    out, _ = _build(tmp_path)

    dpi = 200
    s = 72.0 / dpi
    pdf = pdfium.PdfDocument(out)
    img = np.asarray(pdf[0].render(scale=1.0, draw_annots=True).to_pil())

    r = img[:, :, 0].astype(int)
    b = img[:, :, 2].astype(int)
    bluish = (b > r + 40) & (b > 120)
    rose_target = np.array([225, 29, 72])
    rose = np.abs(img[:, :, :3].astype(int) - rose_target).sum(axis=2) < 150

    def hit_fraction(pts, mask, rad=3):
        ok = tot = 0
        for x, y in pts:
            xi, yi = int(round(x)), int(round(y))
            if not (0 <= yi < img.shape[0] and 0 <= xi < img.shape[1]):
                continue
            tot += 1
            if mask[max(0, yi - rad):yi + rad + 1,
                    max(0, xi - rad):xi + rad + 1].any():
                ok += 1
        return ok / tot if tot else 0.0

    horizontal = hit_fraction([(x * s, 400 * s) for x in np.linspace(300, 1500, 25)], bluish)
    vertical = hit_fraction([(1500 * s, y * s) for y in np.linspace(400, 1200, 25)], bluish)
    rose_frac = hit_fraction([(x * s, 1800 * s) for x in np.linspace(600, 2400, 25)], rose)

    assert horizontal > 0.9, f"horizontal run misplaced (hit {horizontal:.0%})"
    assert vertical > 0.9, f"vertical run misplaced (hit {vertical:.0%})"
    assert rose_frac > 0.9, f"rose run misplaced (hit {rose_frac:.0%})"


def test_missing_update_would_break_appearance(tmp_path):
    """Kontrol negatif: tanpa update(), /AP berisi default viewer (warna/tebal hilang).

    Tes ini membuktikan bahwa `annot.update()` di `export_marked_pdf` memang
    beban kerja yang nyata — bukan sekadar formalitas — dengan mereproduksi
    failure mode-nya secara terpisah.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    pts = [pymupdf.Point(50, 50), pymupdf.Point(350, 50)]
    a = page.add_polyline_annot(pts)
    a.set_colors(stroke=(0.0, 0.5, 1.0))
    a.set_border(width=6)
    a.set_opacity(0.8)
    # SENGAJA tidak memanggil a.update()
    out = str(tmp_path / "no_update.pdf")
    doc.save(out, garbage=3, deflate=True)
    doc.close()

    d = pymupdf.open(out)
    p = d[0]
    streams = []
    for an in p.annots():
        ap_xref = int(d.xref_get_key(an.xref, "AP/N")[1].split()[0])
        streams.append(d.xref_stream(ap_xref).decode("latin-1"))
    d.close()

    assert streams
    # Default viewer: lebar 1 pt dan warna merah (1 0 0 RG) — bukan 6 pt / biru.
    assert any("1 w" in s for s in streams), \
        "expected the un-updated /AP to fall back to viewer defaults"
    assert not any("/H gs" in s for s in streams), \
        "un-updated /AP should not carry the ExtGState opacity"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td)
        test_every_polyline_annotation_has_resolvable_ap(tp)
        test_polyline_ap_bakes_stroke_color_and_width(tp)
        test_polyline_ap_uses_extgstate_for_opacity(tp)
        test_annotations_still_editable_after_update(tp)
        test_missing_update_would_break_appearance(tp)
        print("[PASS] Phase 3 appearance-stream tests")
