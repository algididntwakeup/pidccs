"""
Deteksi OFF-PAGE CONNECTOR (OPC) / Continuation Flag pada P&ID — Fitur Phase C.

Off-Page Connector menghubungkan jalur pipa dari satu sheet gambar P&ID ke sheet gambar lain.
Bentuk fisik di gambar:
  - Simbol panah / pentagon / bendera di dekat batas/margin gambar (battery limit).
  - Teks referensi target drawing (e.g. 'TO DWG 605-42-PID-1-006', 'FROM DWG ...', 'SH. 6').
  - Kadang memuat nomor pipa sambungan atau line number lanjutan.
"""
from __future__ import annotations
import re
from typing import List, Dict, Any, Optional, Tuple


# Regex pola target drawing & continuation
_OPC_DWG_PATTERNS = [
    # Full P&ID drawing code (e.g. 605-42-PID-1-006 or BCD3-605-42-PID-1-006)
    re.compile(r'([A-Za-z0-9]{3,5}-\d{2,4}-PID-[\w\-.]+)', re.I),
    re.compile(r'(\d{2,4}-\d{2,4}-PID-[\w\-.]+)', re.I),
    # Prefixed drawing reference (e.g. TO DWG 1-006, FROM DWG NO. 1-001)
    re.compile(r'(?:TO|FROM|SEE|CONT(?:INUED)?(?:\s+ON)?)\s+(?:DWG\.?\s*(?:NO\.?)?\s*)?([A-Za-z0-9\-_./]+)', re.I),
    re.compile(r'DWG\.?\s*(?:NO\.?)?\s*([A-Za-z0-9\-_./]+)', re.I),
    re.compile(r'(?:PID|DWG)[-_\s]+(\d[-\w.]+)', re.I),
]
_SHEET_NUM_PAT = re.compile(r'(?:SH\.?|SHEET)[\s:]*(\d{1,4}[A-Za-z]?)', re.I)
_LINE_TAG_PAT = re.compile(r'(\d{2,4}-[\d/]+[\"\'\w\-]+)', re.I)


def _pts_of_run(run: Dict[str, Any]) -> List[List[float]]:
    """Ambil list titik [x, y] dari representasi run."""
    pts = run.get("points")
    if pts and len(pts) >= 2:
        return [[float(p[0]), float(p[1])] for p in pts]
    return [
        [float(run.get("x1", 0)), float(run.get("y1", 0))],
        [float(run.get("x2", 0)), float(run.get("y2", 0))]
    ]


def find_terminal_endpoints(
    runs: List[Dict[str, Any]],
    w: int,
    h: int,
    margin_ratio: float = 0.18,
) -> List[Tuple[int, List[float], str]]:
    """Cari ujung run pipa yang berakhir dekat tepi gambar (kandidat OPC).
    
    Return: list of (run_idx, [x, y], edge_orientation)
    edge_orientation in {'left', 'right', 'top', 'bottom'}
    """
    left_m = w * margin_ratio
    right_m = w * (1.0 - margin_ratio)
    top_m = h * margin_ratio
    bottom_m = h * (1.0 - margin_ratio)

    terminals = []
    for ri, r in enumerate(runs):
        pts = _pts_of_run(r)
        if len(pts) < 2:
            continue

        # Periksa kedua ujung polyline
        for pt_idx, pt in [(0, pts[0]), (-1, pts[-1])]:
            px, py = pt[0], pt[1]
            edge = None
            if px <= left_m:
                edge = 'left'
            elif px >= right_m:
                edge = 'right'
            elif py <= top_m:
                edge = 'top'
            elif py >= bottom_m:
                edge = 'bottom'

            if edge:
                terminals.append((ri, pt, edge))

    return terminals


def detect_off_page_connectors(
    result: Dict[str, Any],
    tokens: Optional[List[Dict[str, Any]]] = None,
    dpi: int = 350,
) -> List[Dict[str, Any]]:
    """Deteksi Off-Page Connectors pada hasil digitasi drawing P&ID.
    
    Args:
        result: DigitizationResult dict (runs, piping_ids, w, h, symbols).
        tokens: Daftar token OCR lengkap (text, x1, y1, x2, y2).
        dpi: Resolusi rasterisasi drawing.

    Returns:
        Daftar dict OffPageConnector.
    """
    w = result.get("w", 3300)
    h = result.get("h", 2320)
    runs = result.get("runs", [])
    pids = result.get("piping_ids", [])

    # Map piping ID per run_idx
    pid_by_run = {}
    for p in pids:
        r_idx = p.get("run_idx", -1)
        if r_idx >= 0 and r_idx not in pid_by_run:
            pid_by_run[r_idx] = p.get("pid", "")
        for er in p.get("extra_runs", []):
            if er not in pid_by_run:
                pid_by_run[er] = p.get("pid", "")

    # 1. Identifikasi ujung-ujung pipa di dekat margin
    terminals = find_terminal_endpoints(runs, w, h, margin_ratio=0.18)
    if not terminals and not tokens:
        return []

    # Radius pencarian teks dan simbol di sekitar terminal (dalam pixel)
    search_radius = (dpi / 72.0) * 45.0  # ~45 pt radius (~220 px pada 350 DPI)

    opcs = []
    seen_runs = set()

    for ri, pt, edge in terminals:
        if ri in seen_runs:
            continue

        tx, ty = pt[0], pt[1]
        connected_pid = pid_by_run.get(ri, "")

        # Cari token teks di sekitar terminal
        nearby_texts = []
        target_drawing = ""
        target_sheet = ""
        target_line = ""
        direction = "outgoing" if edge in ('right', 'bottom') else "incoming"

        if tokens:
            for tok in tokens:
                text = str(tok.get("text") or tok.get("t") or "").strip()
                if not text:
                    continue
                bx = (tok.get("x1", 0) + tok.get("x2", 0)) / 2.0
                by = (tok.get("y1", 0) + tok.get("y2", 0)) / 2.0
                dist = ((bx - tx) ** 2 + (by - ty) ** 2) ** 0.5
                if dist <= search_radius:
                    nearby_texts.append(text)

        combined_text = " ".join(nearby_texts)

        # Cek kata kunci direction
        if re.search(r'\bFROM\b', combined_text, re.I):
            direction = "incoming"
        elif re.search(r'\bTO\b', combined_text, re.I):
            direction = "outgoing"

        # Ekstrak target drawing
        for pat in _OPC_DWG_PATTERNS:
            m = pat.search(combined_text)
            if m:
                cand = m.group(1).strip()
                # Bersihkan delimiter trailing
                cand = re.sub(r'[,;:\")]+$', '', cand)
                if len(cand) >= 3 and not cand.lower().startswith(('the', 'and', 'line')):
                    target_drawing = cand
                    break

        # Ekstrak nomor sheet target
        m_sh = _SHEET_NUM_PAT.search(combined_text)
        if m_sh:
            target_sheet = m_sh.group(1).strip()

        # Ekstrak continuation line jika berbeda dengan connected_pid
        m_line = _LINE_TAG_PAT.search(combined_text)
        if m_line:
            cand_line = m_line.group(1).strip()
            if cand_line != connected_pid:
                target_line = cand_line

        # Bounding box OPC di sekitar ujung pipa
        box_pad = (dpi / 72.0) * 15.0
        x1 = max(0.0, tx - box_pad)
        y1 = max(0.0, ty - box_pad)
        x2 = min(float(w), tx + box_pad)
        y2 = min(float(h), ty + box_pad)

        opc_id = f"opc-{len(opcs) + 1:03d}"
        opcs.append({
            "id": opc_id,
            "x1": round(x1, 1),
            "y1": round(y1, 1),
            "x2": round(x2, 1),
            "y2": round(y2, 1),
            "direction": direction,
            "target_drawing": target_drawing,
            "target_sheet_number": target_sheet,
            "target_line": target_line or None,
            "run_idx": ri,
            "piping_id": connected_pid or None,
            "confidence": 0.90 if target_drawing else 0.75,
            "manual": False,
        })
        seen_runs.add(ri)

    return opcs
