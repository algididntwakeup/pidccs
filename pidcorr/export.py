"""
Export deliverable (Fitur 2) — dipanggil INTERAKTIF dari GUI (pilihan user, bukan otomatis):
  1. export_register_xlsx : line register Excel (sheet Line Register + Corrosion System +
     Corrosion Circuit, sel warna sesuai marking) — format kerja corrosion engineer.
  2. render_marked_png    : citra P&ID ter-marking (mode 'system'/'circuit') + LEGEND
     tercetak di gambar -> siap lampiran CCD/laporan.
  3. export_marked_pdf    : PDF dengan marking sebagai ANNOTATION (PolyLine) — jenis objek
     yang sama dgn tool Comment/Drawing Acrobat, jadi tiap garis bisa dipilih, digeser,
     diedit titiknya, diganti warna, atau dihapus user di Adobe Acrobat (workflow marking
     CCD industri). Input PDF -> halaman vektor asli dipertahankan; input JPG/PNG ->
     gambar dibungkus jadi halaman PDF.
"""
from __future__ import annotations
import os
import numpy as np
import cv2

from .systemize import systemize, circuitize, material_of


def _hex(rgb):
    return "%02X%02X%02X" % rgb


def export_register_xlsx(result, path, drawing_name=""):
    """Tulis line register .xlsx. Return jumlah baris line."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    systems = circuitize(result)
    pids = result.get("piping_ids", [])
    wb = Workbook()

    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="E2E8F0")

    def _head(ws, cols, widths):
        ws.append(cols)
        for c in range(1, len(cols) + 1):
            ws.cell(1, c).font = bold; ws.cell(1, c).fill = head_fill
            ws.column_dimensions[get_column_letter(c)].width = widths[c - 1]
        ws.freeze_panes = "A2"

    # ---- sheet 1: Line Register ----
    ws = wb.active; ws.title = "Line Register"
    _head(ws, ["No", "Drawing", "Line No", "Unit", "Size", "Process Fluid", "Piping Class",
               "Material", "Corrosion System", "Circuit", "Status Asosiasi"],
          [5, 30, 30, 8, 8, 13, 12, 10, 18, 10, 14])
    n = 0
    for s in systems:
        fill = PatternFill("solid", fgColor=_hex(tuple(s["color"])))
        for c in s["circuits"]:
            for pi in c["pid_idxs"]:
                p = pids[pi]; n += 1
                ws.append([n, drawing_name, p.get("pid", ""), p.get("unit", ""),
                           p.get("size", ""), s["fluid"], p.get("pclass", ""),
                           material_of(p.get("pclass")) or "—",
                           f"#{s['index']:02d} — {s['fluid']}", c["code"],
                           p.get("state", "")])
                ws.cell(n + 1, 9).fill = fill
                ws.cell(n + 1, 10).fill = PatternFill("solid", fgColor=_hex(tuple(c["color"])))

    # ---- sheet 2: Corrosion System ----
    ws2 = wb.create_sheet("Corrosion System")
    _head(ws2, ["System", "Process Fluid", "Jml Line", "Jml Pipa Ter-trace", "Jml Circuit",
                "Piping Class Terlibat"], [10, 14, 10, 16, 12, 30])
    for i, s in enumerate(systems, 2):
        classes = sorted({cl for c in s["circuits"] for cl in c["classes"]})
        ws2.append([f"#{s['index']:02d}", s["fluid"], len(s["pid_idxs"]), s["n_pipes"],
                    len(s["circuits"]), ", ".join(classes)])
        ws2.cell(i, 1).fill = PatternFill("solid", fgColor=_hex(tuple(s["color"])))

    # ---- sheet 3: Corrosion Circuit ----
    ws3 = wb.create_sheet("Corrosion Circuit")
    _head(ws3, ["Circuit", "System (Fluid)", "Material", "Piping Class", "Jml Line"],
          [10, 16, 10, 22, 10])
    r = 2
    for s in systems:
        for c in s["circuits"]:
            ws3.append([c["code"], s["fluid"], c["material"] or "—",
                        ", ".join(c["classes"]), len(c["pid_idxs"])])
            ws3.cell(r, 1).fill = PatternFill("solid", fgColor=_hex(tuple(c["color"])))
            r += 1

    for w in (ws, ws2, ws3):
        for row in w.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="center")
    wb.save(path)
    return n


def render_marked_png(img_bgr, result, mode="system"):
    """Render P&ID ter-marking + legend (mode 'system' = warna per fluid;
    'circuit' = warna per circuit/material). Return citra BGR siap disimpan."""
    runs = result.get("runs", [])
    vis = img_bgr.copy()
    entries = []                                  # (warna_rgb, teks)
    if mode == "system":
        for s in systemize(result):
            for ri in s["run_idxs"]:
                _poly(vis, runs[ri], s["color"])
            entries.append((s["color"], f"{s['fluid']}  ({s['n_pipes']} pipa)"))
    else:
        for s in circuitize(result):
            for c in s["circuits"]:
                for ri in c["run_idxs"]:
                    _poly(vis, runs[ri], c["color"])
                entries.append((c["color"],
                                f"{c['code']}  {s['fluid']}-{c['material'] or '-'}  ({len(c['pid_idxs'])} line)"))
    _legend(vis, entries,
            "CORROSION SYSTEM (per process fluid)" if mode == "system"
            else "CORROSION CIRCUIT (per fluid + material)")
    return vis


def _poly(vis, run, rgb):
    pts = run.get("points") or [[run["x1"], run["y1"]], [run["x2"], run["y2"]]]
    cv2.polylines(vis, [np.array(pts, np.int32).reshape(-1, 1, 2)], False,
                  (rgb[2], rgb[1], rgb[0]), 6)


# ------------------------------------------------------------ PDF (Acrobat-editable) --
def export_marked_pdf(result, out_path, mode="system"):
    """Tulis PDF ter-marking. Marking = PolyLine annotation per run pipa (bukan pixel),
    legend = Square+FreeText annotation -> semuanya editable/movable/deletable di Acrobat.
    Subject annotation diisi nama system/circuit supaya panel Comments Acrobat bisa
    sort-by-subject per corrosion system. Return jumlah annotation garis."""
    import fitz

    src = result.get("image_path", "")
    dpi = float(result.get("dpi", 350) or 350)
    runs = result.get("runs", [])
    pids = result.get("piping_ids", [])

    # run -> daftar piping ID yg menunjuknya (utk isi popup comment)
    run_pids = {}
    for p in pids:
        ri = p.get("run_idx", -1)
        if ri is not None and ri >= 0:
            run_pids.setdefault(int(ri), []).append(p.get("pid", ""))

    # (warna_rgb, subject, [run_idx], teks_legend)
    groups, title = [], ""
    if mode == "system":
        title = "CORROSION SYSTEM (per process fluid)"
        for si, s in enumerate(systemize(result), 1):
            subj = f"Corrosion System #{si:02d} - {s['fluid']}"
            groups.append((s["color"], subj, s["run_idxs"],
                           f"{s['fluid']}  ({s['n_pipes']} pipa)"))
    else:
        title = "CORROSION CIRCUIT (per fluid + material)"
        for s in circuitize(result):
            for c in s["circuits"]:
                subj = f"Circuit {c['code']} - {s['fluid']}-{c['material'] or '-'}"
                groups.append((c["color"], subj, c["run_idxs"],
                               f"{c['code']}  {s['fluid']}-{c['material'] or '-'}  ({len(c['pid_idxs'])} line)"))

    # --- halaman dasar: PDF asli (vektor, tetap tajam) atau gambar dibungkus PDF ---
    from .pipeline import rotate_bgr, unrotate_matrix
    rot = int(result.get("rot", 0)) % 360

    if src.lower().endswith(".pdf") and os.path.exists(src):
        # halaman asli dipertahankan (tetap vektor & tajam), jadi koordinatnya adalah
        # ruang SEBELUM rotasi manual -> rotasi itu harus dibatalkan di bawah
        doc = fitz.open(src)
        page = doc[0]
        unrot = unrotate_matrix(rot, int(result["w"]), int(result["h"]))
    else:
        # sumber berupa gambar: halaman dibuat seukuran gambar SETELAH diputar, jadi
        # gambar yang disisipkan harus ikut diputar — kalau tidak, gambarnya gepeng
        doc = fitz.open()
        w_pt = result["w"] * 72.0 / dpi
        h_pt = result["h"] * 72.0 / dpi
        page = doc.new_page(width=w_pt, height=h_pt)
        if rot:
            base = rotate_bgr(cv2.imdecode(np.fromfile(src, np.uint8), cv2.IMREAD_COLOR), rot)
            page.insert_image(page.rect, stream=cv2.imencode(".png", base)[1].tobytes())
        else:
            with open(src, "rb") as f:
                page.insert_image(page.rect, stream=f.read())
        unrot = fitz.Matrix(1, 0, 0, 1, 0, 0)      # halaman sudah sejajar gambar

    # pixel (hasil render dpi) -> koordinat halaman: batalkan rotasi manual, skala
    # 72/dpi, lalu de-rotasi halaman (get_pixmap menerapkan /Rotate; annotation
    # memakai ruang un-rotated).
    to_pdf = unrot * fitz.Matrix(72.0 / dpi, 72.0 / dpi) * page.derotation_matrix
    lw = 6 * 72.0 / dpi                            # samakan tebal dgn export PNG (6 px)

    n_annot = 0
    for rgb, subj, run_idxs, _txt in groups:
        col = (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
        for ri in run_idxs:
            run = runs[ri]
            pts = run.get("points") or [[run["x1"], run["y1"]], [run["x2"], run["y2"]]]
            fp = [fitz.Point(x, y) * to_pdf for x, y in pts]
            if len(fp) < 2:
                continue
            a = page.add_polyline_annot(fp)
            a.set_colors(stroke=col)
            a.set_border(width=lw)
            a.set_opacity(0.8)
            ids = ", ".join(run_pids.get(int(ri), [])) or "(tanpa piping ID terasosiasi)"
            a.set_info(title=subj, subject=subj, content=ids)
            a.set_flags(fitz.PDF_ANNOT_IS_PRINT)   # ikut tercetak, TIDAK locked
            a.update()
            n_annot += 1

    _legend_annots(page, title, [(g[0], g[3]) for g in groups])

    # save via tobytes + open(): file IO Python aman utk path non-ASCII Windows
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    with open(out_path, "wb") as f:
        f.write(data)
    return n_annot


def _legend_annots(page, title, entries):
    """Legend sebagai annotation (kotak warna + teks) di pojok kiri-atas halaman
    SEBAGAIMANA DITAMPILKAN — juga bisa digeser/dihapus di Acrobat kalau menutupi
    gambar. Halaman ber-/Rotate: posisi dihitung di ruang tampilan lalu ditransformasi
    ke ruang annotation (un-rotated) via derotation_matrix; teks FreeText diberi
    rotate=page.rotation supaya tampil tegak."""
    import fitz
    if not entries:
        return
    rot = page.rotation
    derot = page.derotation_matrix

    def dr(x0, y0, x1, y1):                        # rect ruang-tampilan -> ruang-annot
        p1 = fitz.Point(x0, y0) * derot
        p2 = fitz.Point(x1, y1) * derot
        return fitz.Rect(min(p1.x, p2.x), min(p1.y, p2.y),
                         max(p1.x, p2.x), max(p1.y, p2.y))

    x0, y0, pad, lh = 14.0, 14.0, 8.0, 15.0
    w = max(170.0, 7.5 * (max(len(t) for _, t in entries) if entries else 0) + 40)
    h = pad * 2 + lh * (len(entries) + 1)
    bg = page.add_rect_annot(dr(x0, y0, x0 + w, y0 + h))
    bg.set_colors(stroke=(0.25, 0.25, 0.25), fill=(1, 1, 1))
    bg.set_opacity(0.85); bg.set_border(width=0.8)
    bg.set_info(title="Legend", subject="Legend", content=title)
    bg.set_flags(fitz.PDF_ANNOT_IS_PRINT); bg.update()

    t = page.add_freetext_annot(
        dr(x0 + pad, y0 + pad - 3, x0 + w - pad, y0 + pad + 11),
        title, fontsize=8.5, text_color=(0.1, 0.1, 0.1), fill_color=None, rotate=rot)
    t.set_flags(fitz.PDF_ANNOT_IS_PRINT); t.update()

    y = y0 + pad + lh
    for rgb, txt in entries:
        col = (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
        b = page.add_rect_annot(dr(x0 + pad, y + 2, x0 + pad + 20, y + 11))
        b.set_colors(stroke=col, fill=col)
        b.set_info(title="Legend", subject="Legend", content=txt)
        b.set_flags(fitz.PDF_ANNOT_IS_PRINT); b.update()
        ft = page.add_freetext_annot(
            dr(x0 + pad + 26, y - 1, x0 + w - 4, y + 13),
            txt, fontsize=8, text_color=(0.1, 0.1, 0.1), fill_color=None, rotate=rot)
        ft.set_flags(fitz.PDF_ANNOT_IS_PRINT); ft.update()
        y += lh


def _legend(vis, entries, title):
    if not entries:
        return
    H, W = vis.shape[:2]
    S = max(0.6, W / 3300)                        # skala relatif ukuran gambar
    lh = int(46 * S); pad = int(16 * S)
    w = int(560 * S); h = pad * 2 + lh * (len(entries) + 1)
    x0, y0 = pad, pad
    ov = vis.copy()
    cv2.rectangle(ov, (x0, y0), (x0 + w, y0 + h), (255, 255, 255), -1)
    cv2.addWeighted(ov, 0.88, vis, 0.12, 0, vis)
    cv2.rectangle(vis, (x0, y0), (x0 + w, y0 + h), (60, 60, 60), max(2, int(3 * S)))
    f = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(vis, title, (x0 + pad, y0 + pad + int(26 * S)), f, 0.75 * S * 1.4,
                (30, 30, 30), max(2, int(3 * S)))
    y = y0 + pad + lh
    for rgb, txt in entries:
        cv2.rectangle(vis, (x0 + pad, y + int(6 * S)), (x0 + pad + int(52 * S), y + int(34 * S)),
                      (rgb[2], rgb[1], rgb[0]), -1)
        cv2.putText(vis, txt, (x0 + pad + int(66 * S), y + int(30 * S)), f, 0.7 * S * 1.4,
                    (30, 30, 30), max(2, int(2 * S)))
        y += lh
