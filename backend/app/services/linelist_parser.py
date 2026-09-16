import csv
import io
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import openpyxl
from ..schemas.linelist import LineListEntry


HEADER_ALIASES = {
    "line_number": [
        "piping id", "line number", "line no", "line no.", "line tag",
        "pipe id", "piping line no", "tag", "line_number", "line_id"
    ],
    "material": [
        "material", "matl", "pipe mat", "pipe material", "mat", "material spec"
    ],
    "operating_pressure": [
        "oprt. press (psig)", "oprt. press", "operating pressure",
        "press (psig)", "op press", "operating_pressure", "op pressure (barg)",
        "op. press", "operating press"
    ],
    "operating_temperature": [
        "oprt. temp (°f)", "oprt. temp (f)", "oprt. temp", "operating temp",
        "temp (°f)", "temp (f)", "temp (c)", "operating_temperature",
        "op. temp", "operating temp"
    ],
    "design_pressure": [
        "dsgn. press", "design press", "design pressure", "design_pressure", "dsgn press"
    ],
    "design_temperature": [
        "dsgn. temp", "design temp", "design temperature", "design_temperature", "dsgn temp"
    ],
    "fluid_phase": [
        "fluid phase", "phase", "fluid_phase", "state", "service phase"
    ],
    "insulation": [
        "insulation (yes/no)", "insulation", "insul", "insulation spec"
    ],
    "corrosion_allowance": [
        "ca (mm)", "ca", "corrosion allowance", "corrosion_allowance", "ca_mm"
    ],
    "corrosion_rate": [
        "cr (mmpy)", "cr", "corrosion rate", "corrosion_rate", "cr_mmpy"
    ],
    "corrosion_loop": [
        "corr. loop", "corrosion loop", "corrosion circuit", "circuit", "loop tag", "corrosion_loop"
    ],
    "service_condition": [
        "service condition", "service", "service_condition"
    ],
    "source_file": [
        "source file", "source", "dwg no", "drawing", "p&id", "source_file", "drawing no"
    ],
}


def _clean_header(name: Any) -> str:
    """Normalize header text for alias matching."""
    if not name:
        return ""
    text = str(name).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _to_float(val: Any) -> Optional[float]:
    """Safely convert cell value to float, ignoring dashes, spaces, and units."""
    if val is None:
        return None
    s = str(val).strip().replace(",", ".")
    if s in ("", "-", "n/a", "none", "null"):
        return None
    # Extract leading float value if text has units (e.g. '750.5 psig')
    m = re.match(r"^[-+]?[0-9]*\.?[0-9]+", s)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


def _normalize_phase(val: Any) -> str:
    """Normalize fluid phase representation to 'L', 'G', '2P', or empty string."""
    if not val:
        return ""
    s = str(val).strip().upper()
    if s in ("L", "LIQ", "LIQUID"):
        return "L"
    if s in ("G", "GAS", "V", "VAP", "VAPOR", "STEAM"):
        return "G"
    if s in ("2P", "2-PHASE", "2 PHASE", "TWO PHASE", "MIX", "MIXED"):
        return "2P"
    return s


def canonical_line_key(pid: str) -> str:
    """Normalize line ID for robust cross-table matching."""
    return re.sub(r'[\s"\'\-_]', "", str(pid or "")).upper()


def match_column_indices(headers: List[Any]) -> Dict[str, int]:
    """Map canonical schema field names to zero-based column indices."""
    col_map: Dict[str, int] = {}
    normalized_headers = [_clean_header(h) for h in headers]

    for field, aliases in HEADER_ALIASES.items():
        for idx, h in enumerate(normalized_headers):
            if not h:
                continue
            if h in aliases or any(alias in h for alias in aliases):
                if field not in col_map:
                    col_map[field] = idx
                    break

    return col_map


class LineListParser:
    @staticmethod
    def parse_rows(rows_iter) -> List[LineListEntry]:
        """Parse raw row iterable into structured LineListEntry list."""
        try:
            headers = next(rows_iter)
        except StopIteration:
            return []

        col_map = match_column_indices(list(headers))
        if "line_number" not in col_map:
            raise ValueError("Line list must contain a 'Piping ID' or 'Line Number' column.")

        entries: List[LineListEntry] = []
        for row in rows_iter:
            if not row or all(c is None for c in row):
                continue

            line_no_idx = col_map["line_number"]
            if line_no_idx >= len(row) or not row[line_no_idx]:
                continue

            line_num = str(row[line_no_idx]).strip()
            if not line_num:
                continue

            def _get(field: str) -> Any:
                idx = col_map.get(field)
                return row[idx] if idx is not None and idx < len(row) else None

            # Collect raw attributes from extra columns
            raw_attrs = {}
            for col_idx, col_name in enumerate(headers):
                if col_name and col_idx < len(row):
                    raw_attrs[str(col_name)] = row[col_idx]

            entry = LineListEntry(
                line_number=line_num,
                source_file=str(_get("source_file") or "").strip(),
                material=str(_get("material") or "").strip().upper(),
                operating_pressure_barg=_to_float(_get("operating_pressure")),
                operating_temperature_c=_to_float(_get("operating_temperature")),
                design_pressure_barg=_to_float(_get("design_pressure")),
                design_temperature_c=_to_float(_get("design_temperature")),
                fluid_phase=_normalize_phase(_get("fluid_phase")),
                insulation=str(_get("insulation") or "").strip().upper(),
                corrosion_allowance_mm=_to_float(_get("corrosion_allowance")),
                corrosion_rate_mmpy=_to_float(_get("corrosion_rate")),
                corrosion_loop=str(_get("corrosion_loop") or "").strip(),
                service_condition=str(_get("service_condition") or "").strip(),
                raw_attributes=raw_attrs,
            )
            entries.append(entry)

        return entries

    @classmethod
    def parse_excel(cls, file_bytes: bytes) -> List[LineListEntry]:
        """Parse in-memory Excel spreadsheet (.xlsx)."""
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        all_entries: List[LineListEntry] = []

        # Iterate all sheets or active sheet
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = ws.iter_rows(values_only=True)
            try:
                entries = cls.parse_rows(rows)
                all_entries.extend(entries)
            except ValueError:
                # Sheet may be notes / reference, continue to other sheets
                continue

        wb.close()
        if not all_entries:
            raise ValueError("No valid line list rows with line numbers found in Excel workbook.")
        return all_entries

    @classmethod
    def parse_csv(cls, text_content: str) -> List[LineListEntry]:
        """Parse CSV text content."""
        reader = csv.reader(io.StringIO(text_content))
        return cls.parse_rows(reader)
