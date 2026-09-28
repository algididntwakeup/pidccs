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
import math
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


def _hex_to_rgb(hex_str):
    if not hex_str:
        return (37, 99, 235)  # #2563EB default
    s = str(hex_str).lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            pass
    return (37, 99, 235)


def render_marked_png(img_bgr, result, mode="engineer"):
    """Render P&ID ter-marking + legend (mode 'engineer' = warna per run pipa;
    'system' = warna per fluid; 'circuit' = warna per circuit/material). Return citra BGR siap disimpan."""
    runs = result.get("runs", [])
    vis = img_bgr.copy()
    entries = []                                  # (warna_rgb, teks)
    if mode == "engineer":
        from collections import defaultdict
        color_groups = defaultdict(list)
        for ri, r in enumerate(runs):
            rgb = _hex_to_rgb(r.get("color", "#2563EB"))
            _poly(vis, r, rgb)
            color_groups[rgb].append(ri)
        for rgb, r_idxs in color_groups.items():
            hex_label = f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
            entries.append((rgb, f"Piping Run {hex_label} ({len(r_idxs)} segmen)"))
        _legend(vis, entries, "PIPING RUN TRACE (Engineer Polyline Mode)")
    elif mode == "system":
        for s in systemize(result):
            for ri in s["run_idxs"]:
                _poly(vis, runs[ri], s["color"])
            entries.append((s["color"], f"{s['fluid']}  ({s['n_pipes']} pipa)"))
        _legend(vis, entries, "CORROSION SYSTEM (per process fluid)")
    else:
        for s in circuitize(result):
            for c in s["circuits"]:
                for ri in c["run_idxs"]:
                    _poly(vis, runs[ri], c["color"])
                entries.append((c["color"],
                                f"{c['code']}  {s['fluid']}-{c['material'] or '-'}  ({len(c['pid_idxs'])} line)"))
        _legend(vis, entries, "CORROSION CIRCUIT (per fluid + material)")
    return vis


def _poly(vis, run, rgb):
    pts = run.get("points") or [[run["x1"], run["y1"]], [run["x2"], run["y2"]]]
    cv2.polylines(vis, [np.array(pts, np.int32).reshape(-1, 1, 2)], False,
                  (rgb[2], rgb[1], rgb[0]), 6)


# ------------------------------------------------------------ PDF (Acrobat-editable) --
def export_marked_pdf(result, out_path, mode="engineer", include_legend=False):
    """Tulis PDF ter-marking. Marking = PolyLine annotation per run pipa (bukan pixel),
    legend = Square+FreeText annotation -> semuanya editable/movable/deletable di Acrobat.
    Subject annotation diisi nama system/circuit supaya panel Comments Acrobat bisa
    sort-by-subject per corrosion system. Return jumlah annotation garis.

    `include_legend=False` (default): TANPA kotak legenda di pojok kertas — sebagai
    gantinya tiap grup manual engineer diberi STEMPEL FreeText berisi nama grup di titik
    tengah run terpanjangnya (`_stamp_groups`). Set `True` untuk perilaku lama.
    """
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

    # Grup MANUAL (HITL) menang atas grup otomatis: run yang sudah di-assign ke grup
    # manual dikeluarkan dari grup mode (engineer/system/circuit) lalu dirender ulang
    # dengan warna + nama grup manual (lihat penambahan grup manual setelah cabang mode).
    manual_groups = result.get("manual_groups") or []
    manual_by_run = {}                             # run_idx -> indeks grup manual
    for gi, g in enumerate(manual_groups):
        for ri, r in enumerate(runs):
            if r.get("group_id") and r.get("group_id") == g.get("id"):
                manual_by_run[ri] = gi

    # (warna_rgb, subject, [run_idx], teks_legend, teks_stempel)
    groups, title = [], ""
    if mode == "engineer":
        title = "PIPING RUN TRACE (Engineer Polyline Mode)"
        from collections import defaultdict
        color_groups = defaultdict(list)
        for ri, r in enumerate(runs):
            if ri in manual_by_run:
                continue
            rgb = _hex_to_rgb(r.get("color", "#2563EB"))
            color_groups[rgb].append(ri)
        for rgb, r_idxs in color_groups.items():
            hex_label = f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
            subj = f"Piping Run {hex_label}"
            groups.append((rgb, subj, r_idxs,
                           f"Piping Run {hex_label} ({len(r_idxs)} segmen)", None))
    elif mode == "system":
        title = "CORROSION SYSTEM (per process fluid)"
        for si, s in enumerate(systemize(result), 1):
            subj = f"Corrosion System #{si:02d} - {s['fluid']}"
            idxs = [ri for ri in s["run_idxs"] if ri not in manual_by_run]
            if not idxs:
                continue
            groups.append((s["color"], subj, idxs,
                           f"{s['fluid']}  ({s['n_pipes']} pipa)", s["fluid"]))
    else:
        title = "CORROSION CIRCUIT (per fluid + material)"
        for s in circuitize(result):
            for c in s["circuits"]:
                subj = f"Circuit {c['code']} - {s['fluid']}-{c['material'] or '-'}"
                idxs = [ri for ri in c["run_idxs"] if ri not in manual_by_run]
                if not idxs:
                    continue
                groups.append((c["color"], subj, idxs,
                               f"{c['code']}  {s['fluid']}-{c['material'] or '-'}  "
                               f"({len(c['pid_idxs'])} line)", c["code"]))

    # Grup manual engineer: warna & nama grup menang atas mode; nama dipakai sebagai
    # stempel di kanvas PDF (pengganti legend).
    for gi, g in enumerate(manual_groups):
        idxs = sorted(ri for ri, mgi in manual_by_run.items() if mgi == gi)
        if not idxs:
            continue
        name = g.get("name") or f"Group {gi + 1}"
        groups.append((_hex_to_rgb(g.get("color") or "#F59E0B"), name, idxs, name, name))

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
    opacity = 0.8

    n_annot = 0
    for rgb, subj, run_idxs, _txt, _stamp in groups:
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
            a.set_opacity(opacity)
            ids = ", ".join(run_pids.get(int(ri), [])) or "(tanpa piping ID terasosiasi)"
            a.set_info(title=subj, subject=subj, content=ids)
            a.set_flags(fitz.PDF_ANNOT_IS_PRINT)   # ikut tercetak, TIDAK locked
            # WAJIB (Phase 3): `update()` membangun Appearance Stream (/AP) dari
            # properti di atas. Viewer berbasis PDFium (Chrome, Edge) TIDAK
            # mensintesis tampilan sendiri untuk anotasi tanpa /AP. Terukur:
            # tanpa update() -> /AP berisi default `1 w` + `1 0 0 RG` (merah,
            # 1 pt) sehingga garis tampak tipis & salah warna; dengan update() ->
            # `/H gs` + `6 w` + `0 .5 1 RG` + ExtGState /H <</CA .8/ca .8>>`.
            # Jangan hapus baris ini.
            a.update()
            n_annot += 1

    if include_legend:
        _legend_annots(page, title, [(g[0], g[3]) for g in groups])
    _stamp_groups(page, groups, runs, to_pdf, dpi)

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

def _run_len(run):
    """Panjang arc-length polyline `run` (dict result_json) dalam px."""
    pts = run.get("points") or []
    if len(pts) < 2:
        return 0.0
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))

def _run_midpoint(run):
    """Titik tengah MENURUT PANJANG polyline (bukan tengah bbox) — interpolasi linear."""
    pts = run.get("points") or []
    if len(pts) < 2:
        return None
    total = _run_len(run)
    if total <= 0:
        return None
    half, walked = total / 2.0, 0.0
    for i in range(len(pts) - 1):
        x0, y0 = float(pts[i][0]), float(pts[i][1])
        x1, y1 = float(pts[i + 1][0]), float(pts[i + 1][1])
        seg = math.hypot(x1 - x0, y1 - y0)
        if walked + seg >= half:
            t = 0.0 if seg <= 0 else (half - walked) / seg
            return x0 + t * (x1 - x0), y0 + t * (y1 - y0)
        walked += seg
    return float(pts[-1][0]), float(pts[-1][1])

def _stamp_groups(page, groups, runs, to_pdf, dpi):
    """Stempel nama grup (FreeText annotation) di titik tengah run TERPANJANG tiap grup.

    Pengganti kotak legend: label menempel pada pipa yang di-marking, jadi engineer
    langsung tahu grup mana milik garis mana tanpa mencari ke pojok kertas. Stempel
    TIDAK dihitung sebagai annotation garis (return `export_marked_pdf` tetap jumlah
    PolyLine). Warna teks = warna grup; tanpa fill/border (gaya sama dgn legend).
    """
    import fitz

    for rgb, _subj, run_idxs, _txt, stamp in groups:
        if not stamp or not run_idxs:
            continue
        longest = max((runs[ri] for ri in run_idxs if 0 <= ri < len(runs)),
                      key=_run_len, default=None)
        if longest is None or _run_len(longest) <= 0:
            continue
        mid = _run_midpoint(longest)
        if mid is None:
            continue
        pts = longest.get("points") or []
        # Teks horizontal di ATAS pipa horizontal; di SAMPING KANAN pipa vertikal
        # (kalau di atas, label vertikal menabrak garis di sebelahnya).
        vertical = len(pts) >= 2 and abs(pts[-1][0] - pts[0][0]) < abs(pts[-1][1] - pts[0][1])
        ax, ay = (mid[0] + 14.0, mid[1]) if vertical else (mid[0], mid[1] - 8.0)
        # Rect dibangun di ruang TAMPILAN (pt) lalu dipetakan ke ruang annotation
        # (un-rotated) lewat derotation_matrix — pola sama dengan `_legend_annots`.
        # Membangun rect langsung di ruang annotation membuat stempel ikut terputar
        # 90° dan keluar halaman pada lembar ber-/Rotate 90/270.
        dp = fitz.Point(ax, ay) * to_pdf * page.rotation_matrix
        q1 = fitz.Point(dp.x, dp.y - 13.0) * page.derotation_matrix
        q2 = fitz.Point(dp.x + 6.2 * len(stamp) + 6.0, dp.y) * page.derotation_matrix
        rect = fitz.Rect(min(q1.x, q2.x), min(q1.y, q2.y),
                         max(q1.x, q2.x), max(q1.y, q2.y))
        col = (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
        a = page.add_freetext_annot(rect, stamp, fontsize=9.5, text_color=col,
                                    fill_color=None, rotate=page.rotation,
                                    align=fitz.TEXT_ALIGN_LEFT)
        a.set_info(title=stamp, subject="Group Stamp", content=stamp)
        a.set_flags(fitz.PDF_ANNOT_IS_PRINT)
        a.update()


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
