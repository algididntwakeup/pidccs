"""Export stempel grup manual (tanpa kotak legend) — HITL Fase C.

Yang dikunci:
  1. tiap grup manual menghasilkan SATU stempel FreeText berisi nama grup, ditaruh
     di titik tengah run TERPANJANG grup itu (bukan tengah bbox, bukan run pertama),
  2. stempel memakai warna grup (teks) dan PolyLine anggotanya memakai warna grup
     (bukan warna circuit otomatis) — grup manual menang atas mode,
  3. kotak legend TIDAK lagi ada secara default; muncul hanya bila
     `include_legend=True`.
"""
import os
import re
import sys

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
_DPI = 200

def _result(runs, manual_groups):
    return {
        "image_path": _PDF_SRC,
        "dpi": _DPI, "rot": 0, "w": 3309, "h": 2339,
        "runs": runs,
        "piping_ids": [{"pid": "605-6\"-GF-CCB-001", "run_idx": 0, "fluid": "GF",
                        "pclass": "CCB"}],
        "manual_groups": manual_groups,
    }

def _export(tmp_path, include_legend=False):
    if not os.path.exists(_PDF_SRC):
        pytest.skip("reference vector PDF not available")
    runs = [
        {"points": [[300, 400], [1500, 400]], "group_id": "g1", "color": "#F59E0B"},
        {"points": [[1500, 400], [1500, 1200]], "group_id": "g1", "color": "#F59E0B"},
        {"points": [[600, 1800], [2400, 1800]], "group_id": "g2", "color": "#10B981"},
    ]
    groups = [
        {"id": "g1", "name": "CC #07-06-12", "color": "#F59E0B"},
        {"id": "g2", "name": "CC #07-07-01", "color": "#10B981"},
    ]
    out = str(tmp_path / "stamped.pdf")
    n = export_marked_pdf(_result(runs, groups), out, mode="circuit",
                          include_legend=include_legend)
    return out, n

def _ap_stream(doc, annot):
    ap_xref = int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0])
    return doc.xref_stream(ap_xref).decode("latin-1")

def _hex_ops(stream, rgb):
    """True bila stream memuat operator warna (rg/RG) dengan nilai rgb yang diminta."""
    want = [c / 255.0 for c in rgb]
    for m in re.finditer(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(rg|RG)\b", stream):
        got = [float(m.group(i)) for i in (1, 2, 3)]
        if all(abs(g - w) < 0.01 for g, w in zip(got, want)):
            return True
    return False


def test_dashed_run_exports_with_dashed_pdf_annotation(tmp_path):
    source = tmp_path / "source.pdf"
    document = pymupdf.open()
    document.new_page(width=400, height=400)
    document.save(source)
    document.close()

    output = tmp_path / "dashed.pdf"
    result = {
        "image_path": str(source),
        "dpi": 72,
        "w": 400,
        "h": 400,
        "runs": [{"points": [[40, 60], [300, 60]], "line_style": "dashed", "color": "#2563EB"}],
        "piping_ids": [],
    }
    assert export_marked_pdf(result, str(output), mode="engineer") == 1

    exported = pymupdf.open(output)
    page = exported[0]
    annotation = next(page.annots())
    border = exported.xref_get_key(annotation.xref, "BS")[1]
    assert "/S/D" in border and "/D[3 2]" in border

def test_stamp_annotation_placed_at_longest_run_midpoint(tmp_path):
    out, n = _export(tmp_path)
    assert n == 3, "stempel tidak boleh menambah hitungan annotation garis"

    doc = pymupdf.open(out)
    page = doc[0]
    stamps = {a.info["content"]: a for a in page.annots()
              if a.type[1] == "FreeText" and a.info.get("subject") == "Group Stamp"}
    assert set(stamps) == {"CC #07-06-12", "CC #07-07-01"}, stamps.keys()

    def display_rect(annot):
        """Rect annotation -> ruang TAMPILAN (pt). Untuk halaman ber-/Rotate, rect
        annotation tersimpan un-rotated, jadi lebar/tingginya tertukar di sana."""
        a = annot.rect
        q1 = pymupdf.Point(a.x0, a.y0) * page.rotation_matrix
        q2 = pymupdf.Point(a.x1, a.y1) * page.rotation_matrix
        return pymupdf.Rect(min(q1.x, q2.x), min(q1.y, q2.y),
                            max(q1.x, q2.x), max(q1.y, q2.y))

    def assert_anchored(annot, px, py):
        """Stempel harus duduk di ruang tampilan tepat di atas/beside titik (px, py)
        dalam ruang PIXEL citra: sudut kanan-bawah teks = titik itu, teks tumbuh
        ke kiri-atas. Seluruh rect harus berada di dalam halaman."""
        pt = 72.0 / _DPI
        ax, ay = px * pt, py * pt
        r = display_rect(annot)
        assert abs(r.x0 - ax) < 1.5, f"anchor x meleset: {r} vs ({ax}, {ay})"
        assert abs(r.y1 - ay) < 1.5, f"anchor y meleset: {r} vs ({ax}, {ay})"
        assert r.y0 < r.y1 and r.x1 > r.x0, f"rect stempel degenerat: {r}"
        assert page.rect.contains(r), f"stempel keluar halaman: {r}"

    # g1: run terpanjang = segmen horizontal (1200 px) -> tengah (900, 400); stempel
    # horizontal ditaruh 8 px di ATAS pipa, jadi anchor = (900, 392).
    g1 = stamps["CC #07-06-12"]
    assert_anchored(g1, 900, 392)
    assert display_rect(g1).width > 60, "lebar stempel tidak sesuai panjang teks"
    stream = _ap_stream(doc, g1)
    assert _hex_ops(stream, (245, 158, 11)), f"warna grup #F59E0B tidak di-bake: {stream!r}"

    # g2: hanya punya satu run -> stempel di tengah run itu (1500, 1800) -> (1500, 1792).
    assert_anchored(stamps["CC #07-07-01"], 1500, 1792)

    # PolyLine anggota grup manual memakai warna grup, bukan warna circuit otomatis.
    g2_lines = [a for a in page.annots()
                if a.type[1] == "PolyLine" and _hex_ops(_ap_stream(doc, a), (16, 185, 129))]
    assert len(g2_lines) == 1, "PolyLine grup g2 tidak memakai warna grup #10B981"
    doc.close()

def test_legend_absent_by_default_and_present_when_requested(tmp_path):
    out, _ = _export(tmp_path)
    doc = pymupdf.open(out)
    titles = [a.info.get("title") for a in doc[0].annots()]
    assert "Legend" not in titles, f"kotak legend masih ada: {titles}"
    doc.close()

    out2, _ = _export(tmp_path, include_legend=True)
    doc = pymupdf.open(out2)
    titles2 = [a.info.get("title") for a in doc[0].annots()]
    assert titles2.count("Legend") >= 1, "include_legend=True tidak menghasilkan legend"
    doc.close()


def test_saved_stamp_position_exports_bordered_text_at_position(tmp_path):
    source = tmp_path / "position-source.pdf"
    document = pymupdf.open()
    document.new_page(width=400, height=400)
    document.save(source)
    document.close()

    output = tmp_path / "position-stamp.pdf"
    result = {
        "image_path": str(source),
        "dpi": 72,
        "w": 400,
        "h": 400,
        "runs": [{"points": [[40, 60], [300, 60]], "group_id": "g1", "color": "#2563EB"}],
        "piping_ids": [],
        "manual_groups": [{
            "id": "g1",
            "name": "STAMP-01",
            "color": "#10B981",
            "kind": "circuit",
            "stampPosition": {"x": 60, "y": 80},
        }],
    }

    assert export_marked_pdf(result, str(output), mode="engineer") == 1

    exported = pymupdf.open(output)
    page = exported[0]
    stamp_rect = next(drawing["rect"] for drawing in page.get_drawings()
                      if drawing["type"] == "fs")
    assert abs(stamp_rect.x0 - 60) < 0.1
    assert abs(stamp_rect.y0 - 80) < 0.1
    assert "STAMP-01" in page.get_text()
    assert stamp_rect.width >= 68 and stamp_rect.height == 24
    assert stamp_rect in page.rect
    exported.close()
