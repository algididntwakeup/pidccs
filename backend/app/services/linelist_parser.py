import csv
import io
import math
import re
from typing import List, Dict, Any, Optional, Union

import openpyxl
from ..schemas.linelist import LineListEntry


# Canonical field aliases and regex patterns for heuristic auto-mapping
CANONICAL_FIELDS = {
    "line_number",
    "design_pressure_barg",
    "design_temperature_c",
    "operating_pressure_barg",
    "operating_temperature_c",
    "fluid_phase",
    "insulation",
    "notes",
    "material",
    "corrosion_allowance_mm",
    "corrosion_rate_mmpy",
    "corrosion_loop",
    "service_condition",
    "source_file",
}

# Alias normalization for user-supplied mapping dictionaries
FIELD_ALIASES_MAP = {
    "line_number": "line_number",
    "line_no": "line_number",
    "line_num": "line_number",
    "piping_id": "line_number",
    "pipe_id": "line_number",
    "tag": "line_number",
    "line tag": "line_number",
    "operating_pressure": "operating_pressure_barg",
    "operating_pressure_barg": "operating_pressure_barg",
    "op_pressure": "operating_pressure_barg",
    "op_press": "operating_pressure_barg",
    "operating_temperature": "operating_temperature_c",
    "operating_temperature_c": "operating_temperature_c",
    "op_temperature": "operating_temperature_c",
    "op_temp": "operating_temperature_c",
    "design_pressure": "design_pressure_barg",
    "design_pressure_barg": "design_pressure_barg",
    "dsgn_pressure": "design_pressure_barg",
    "dsgn_press": "design_pressure_barg",
    "design_temperature": "design_temperature_c",
    "design_temperature_c": "design_temperature_c",
    "dsgn_temperature": "design_temperature_c",
    "dsgn_temp": "design_temperature_c",
    "fluid_phase": "fluid_phase",
    "phase": "fluid_phase",
    "insulation": "insulation",
    "insul": "insulation",
    "notes": "notes",
    "note": "notes",
    "remarks": "notes",
    "remark": "notes",
    "keterangan": "notes",
    "material": "material",
    "matl": "material",
    "corrosion_allowance": "corrosion_allowance_mm",
    "corrosion_allowance_mm": "corrosion_allowance_mm",
    "ca": "corrosion_allowance_mm",
    "corrosion_rate": "corrosion_rate_mmpy",
    "corrosion_rate_mmpy": "corrosion_rate_mmpy",
    "cr": "corrosion_rate_mmpy",
    "corrosion_loop": "corrosion_loop",
    "corr_loop": "corrosion_loop",
    "circuit": "corrosion_loop",
    "service_condition": "service_condition",
    "service": "service_condition",
    "source_file": "source_file",
    "dwg_no": "source_file",
}


def _clean_header(name: Any) -> str:
    """Normalize header text for alias and heuristic matching."""
    if not name:
        return ""
    text = str(name).strip().lower()
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _clean_str(val: Any) -> str:
    """Normalize string cell value, returning stripped string or empty."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("none", "nan", "null", "nil"):
        return ""
    return s


def _to_float(val: Any) -> Optional[float]:
    """Safely convert cell value to float, ignoring dashes, spaces, and engineering units."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)

    s = str(val).strip().replace(",", ".")
    if s.lower() in ("", "-", "--", "n/a", "na", "none", "null", "nil", "nan"):
        return None

    # Extract leading float value if text has units (e.g. '750.5 psig', '95.3 C', '10.2 barg')
    m = re.match(r"^[-+]?[0-9]*\.?[0-9]+", s)
    if m:
        try:
            return float(m.group(0))
        except (ValueError, TypeError):
            return None
    return None


def _normalize_phase(val: Any) -> str:
    """Normalize fluid phase representation to 'L', 'G', '2P', or cleaned string."""
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


def match_column_indices(headers: List[Any], mapping_dict: Optional[Dict[str, str]] = None) -> Dict[str, int]:
    """Map canonical schema field names to zero-based column indices.
    
    Supports:
    1. Explicit Dynamic Column Mapping via `mapping_dict`.
       Mapping can be target->source e.g. {"line_number": "Piping ID"}
       or source->target e.g. {"Piping ID": "line_number"}.
    2. Heuristic Auto-Mapping via regex and token matching for contractor variations.
    """
    col_map: Dict[str, int] = {}
    normalized_headers = [_clean_header(h) for h in headers]

    # 1. Process explicit user-supplied mapping_dict if present
    if mapping_dict:
        for k, v in mapping_dict.items():
            if not k or not v:
                continue
            k_clean = _clean_header(k)
            v_clean = _clean_header(v)

            # Check if key is canonical field or alias
            target_field = FIELD_ALIASES_MAP.get(k_clean)
            source_header = v_clean
            if not target_field:
                # Check if value is canonical field or alias
                target_field = FIELD_ALIASES_MAP.get(v_clean)
                source_header = k_clean

            if target_field:
                for idx, h in enumerate(normalized_headers):
                    if h == source_header or source_header in h:
                        col_map[target_field] = idx
                        break

    # Helper to check if a canonical field is already assigned
    def _is_unmapped(field: str) -> bool:
        return field not in col_map

    # 2. Heuristic Auto-Mapping for any remaining unmapped fields
    for idx, h in enumerate(normalized_headers):
        if not h:
            continue

        # Line number (Piping ID)
        if _is_unmapped("line_number"):
            if (
                re.search(r"line.*(?:no|num|tag|id)|piping.*id|pipe.*id|^tag$", h)
                or ("line" in h and ("no" in h or "num" in h or "tag" in h or "id" in h))
                or ("piping" in h and "id" in h)
                or h in ("piping id", "line number", "line no", "line no.", "pipe id", "tag")
            ):
                col_map["line_number"] = idx
                continue

        # Operating Pressure: header contains "oper"/"op" & "press"/"pres" (not design)
        if _is_unmapped("operating_pressure_barg"):
            if (
                ("oper" in h or "oprt" in h or re.search(r"\bop\b|\bop\.", h))
                and ("press" in h or "pres" in h)
                and not ("design" in h or "dsgn" in h)
            ):
                col_map["operating_pressure_barg"] = idx
                continue

        # Operating Temperature: header contains "oper"/"op" & "temp" (not design)
        if _is_unmapped("operating_temperature_c"):
            if (
                ("oper" in h or "oprt" in h or re.search(r"\bop\b|\bop\.", h))
                and "temp" in h
                and not ("design" in h or "dsgn" in h)
            ):
                col_map["operating_temperature_c"] = idx
                continue

        # Design Pressure: header contains "design"/"dsgn" & "press"/"pres"
        if _is_unmapped("design_pressure_barg"):
            if ("design" in h or "dsgn" in h) and ("press" in h or "pres" in h):
                col_map["design_pressure_barg"] = idx
                continue

        # Design Temperature: header contains "design"/"dsgn" & "temp"
        if _is_unmapped("design_temperature_c"):
            if ("design" in h or "dsgn" in h) and "temp" in h:
                col_map["design_temperature_c"] = idx
                continue

        # Fluid Phase: phase or state
        if _is_unmapped("fluid_phase"):
            if "fluid phase" in h or re.search(r"\bphase\b|\bstate\b", h):
                col_map["fluid_phase"] = idx
                continue

        # Insulation: insul or insulation
        if _is_unmapped("insulation"):
            if "insul" in h:
                col_map["insulation"] = idx
                continue

        # Notes / Remarks / Keterangan
        if _is_unmapped("notes"):
            if re.search(r"\bnotes?\b|\bremarks?\b|\bketerangan\b|\bcomments?\b", h):
                col_map["notes"] = idx
                continue

        # Material: material or matl (exclude color/group/spec)
        if _is_unmapped("material"):
            if re.search(r"\bmatl\b|\bmaterial\b|pipe\s*mat", h) and "spec" not in h and "color" not in h:
                col_map["material"] = idx
                continue

        # Corrosion Allowance: ca or corrosion allowance
        if _is_unmapped("corrosion_allowance_mm"):
            if re.search(r"corrosion\s*allow|\bca\b|\bca\(|\(ca\)|ca\s*\(mm\)", h):
                col_map["corrosion_allowance_mm"] = idx
                continue

        # Corrosion Rate: cr or corrosion rate
        if _is_unmapped("corrosion_rate_mmpy"):
            if re.search(r"corrosion\s*rate|\bcr\b|\bcr\(|\(cr\)|cr\s*\(mmpy\)", h):
                col_map["corrosion_rate_mmpy"] = idx
                continue

        # Corrosion Loop / Circuit
        if _is_unmapped("corrosion_loop"):
            if re.search(r"corr(?:osion)?\.?\s*loop|corrosion\s*circuit|\bcircuit\b|loop\s*tag", h):
                col_map["corrosion_loop"] = idx
                continue

        # Service Condition
        if _is_unmapped("service_condition"):
            if re.search(r"service\s*cond|service", h) and "phase" not in h:
                col_map["service_condition"] = idx
                continue

        # Source File / Drawing / P&ID
        if _is_unmapped("source_file"):
            if re.search(r"source\s*file|dwg\s*no|drawing|p&id", h):
                col_map["source_file"] = idx
                continue

    return col_map


class LineListParser:
    @staticmethod
    def parse_rows(rows_iter, mapping_dict: Optional[Dict[str, str]] = None) -> List[LineListEntry]:
        """Parse raw row iterable into structured LineListEntry list."""
        try:
            headers = next(rows_iter)
        except StopIteration:
            return []

        col_map = match_column_indices(list(headers), mapping_dict=mapping_dict)
        if "line_number" not in col_map:
            raise ValueError("Line list must contain a 'Piping ID' or 'Line Number' column.")

        entries: List[LineListEntry] = []
        for row in rows_iter:
            if not row or all(c is None for c in row):
                continue

            line_no_idx = col_map["line_number"]
            if line_no_idx >= len(row) or row[line_no_idx] is None:
                continue

            line_num = str(row[line_no_idx]).strip()
            if not line_num or line_num.lower() in ("none", "nan", "null"):
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
                design_pressure_barg=_to_float(_get("design_pressure_barg")),
                design_temperature_c=_to_float(_get("design_temperature_c")),
                operating_pressure_barg=_to_float(_get("operating_pressure_barg")),
                operating_temperature_c=_to_float(_get("operating_temperature_c")),
                fluid_phase=_normalize_phase(_get("fluid_phase")),
                insulation=_clean_str(_get("insulation")).upper(),
                notes=_clean_str(_get("notes")),
                source_file=_clean_str(_get("source_file")),
                material=_clean_str(_get("material")).upper(),
                corrosion_allowance_mm=_to_float(_get("corrosion_allowance_mm")),
                corrosion_rate_mmpy=_to_float(_get("corrosion_rate_mmpy")),
                corrosion_loop=_clean_str(_get("corrosion_loop")),
                service_condition=_clean_str(_get("service_condition")),
                raw_attributes=raw_attrs,
            )
            entries.append(entry)

        return entries

    @classmethod
    def parse_excel(cls, file_bytes: bytes, mapping_dict: Optional[Dict[str, str]] = None) -> List[LineListEntry]:
        """Parse in-memory Excel spreadsheet (.xlsx or .xls)."""
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        all_entries: List[LineListEntry] = []

        # Iterate all sheets or active sheet
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = ws.iter_rows(values_only=True)
            try:
                entries = cls.parse_rows(rows, mapping_dict=mapping_dict)
                all_entries.extend(entries)
            except ValueError:
                # Sheet may be notes / reference without line tags, continue to other sheets
                continue

        wb.close()
        if not all_entries:
            raise ValueError("No valid line list rows with line numbers found in Excel workbook.")
        return all_entries

    @classmethod
    def parse_csv(cls, content: Union[str, bytes], mapping_dict: Optional[Dict[str, str]] = None) -> List[LineListEntry]:
        """Parse CSV content provided as text string or raw bytes."""
        if isinstance(content, bytes):
            try:
                text_content = content.decode("utf-8")
            except UnicodeDecodeError:
                text_content = content.decode("latin-1", errors="replace")
        else:
            text_content = content

        reader = csv.reader(io.StringIO(text_content))
        return cls.parse_rows(reader, mapping_dict=mapping_dict)

    @classmethod
    def parse_file(
        cls,
        file_bytes: bytes,
        filename: str = "",
        mapping_dict: Optional[Dict[str, str]] = None,
    ) -> List[LineListEntry]:
        """Parse file content (Excel .xlsx/.xls or CSV .csv/.txt) with dynamic column mapping."""
        ext = filename.split(".")[-1].lower() if "." in filename else ""

        if ext in ("xlsx", "xls"):
            return cls.parse_excel(file_bytes, mapping_dict=mapping_dict)
        elif ext in ("csv", "txt"):
            return cls.parse_csv(file_bytes, mapping_dict=mapping_dict)
        else:
            # Fallback autodetection: attempt Excel first, then CSV
            try:
                return cls.parse_excel(file_bytes, mapping_dict=mapping_dict)
            except Exception:
                return cls.parse_csv(file_bytes, mapping_dict=mapping_dict)
