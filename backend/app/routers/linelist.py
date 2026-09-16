from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..db.session import get_db
from ..models.project import Project
from ..models.sheet import Sheet
from ..schemas.linelist import LineListImportResult, LineListResponse, LineListEntry
from ..services.linelist_parser import LineListParser, canonical_line_key
from ..services.grouping_service import GroupingService
from ..services.project_service import ProjectService

router = APIRouter(prefix="/projects/{project_id}/linelist", tags=["linelist"])


@router.post("", response_model=LineListImportResult)
async def import_linelist_endpoint(
    project_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import Line List Excel or CSV spreadsheet and enrich all P&ID sheets in the project."""
    project = await ProjectService.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    content = await file.read()
    filename = file.filename or "linelist"
    ext = filename.split(".")[-1].lower()

    try:
        if ext in ("xlsx", "xls"):
            entries = LineListParser.parse_excel(content)
        elif ext in ("csv", "txt"):
            entries = LineListParser.parse_csv(content.decode("utf-8", errors="replace"))
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format '{ext}'. Please upload an .xlsx or .csv file."
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse line list file: {str(e)}")

    if not entries:
        raise HTTPException(status_code=400, detail="Line list contains no valid data rows.")

    # Save to project
    project.linelist_json = {
        "filename": filename,
        "total_rows": len(entries),
        "entries": [e.model_dump() for e in entries]
    }

    from sqlalchemy.orm.attributes import flag_modified

    # Index line list by canonical key for fast O(1) matching, merging rich data from duplicate rows
    linelist_index: Dict[str, LineListEntry] = {}
    for entry in entries:
        key = canonical_line_key(entry.line_number)
        if not key:
            continue
        if key not in linelist_index:
            linelist_index[key] = entry.model_copy()
        else:
            existing = linelist_index[key]
            if entry.corrosion_loop and not existing.corrosion_loop:
                existing.corrosion_loop = entry.corrosion_loop
            if entry.material and not existing.material:
                existing.material = entry.material
            if entry.fluid_phase and not existing.fluid_phase:
                existing.fluid_phase = entry.fluid_phase
            if entry.operating_temperature_c is not None and existing.operating_temperature_c is None:
                existing.operating_temperature_c = entry.operating_temperature_c
            if entry.operating_pressure_barg is not None and existing.operating_pressure_barg is None:
                existing.operating_pressure_barg = entry.operating_pressure_barg
            if entry.design_temperature_c is not None and existing.design_temperature_c is None:
                existing.design_temperature_c = entry.design_temperature_c
            if entry.design_pressure_barg is not None and existing.design_pressure_barg is None:
                existing.design_pressure_barg = entry.design_pressure_barg
            if entry.corrosion_allowance_mm is not None and existing.corrosion_allowance_mm is None:
                existing.corrosion_allowance_mm = entry.corrosion_allowance_mm

    # Enrich existing sheets in project
    stmt = select(Sheet).where(Sheet.project_id == project_id)
    result = await db.execute(stmt)
    sheets = result.scalars().all()

    matched_pids = 0
    unmatched_pids = 0
    enriched_sheets = []

    for sheet in sheets:
        if not sheet.result_json or not sheet.result_json.get("piping_ids"):
            continue

        pids = sheet.result_json.get("piping_ids", [])
        sheet_matched = 0

        for p in pids:
            p_key = canonical_line_key(p.get("pid", ""))
            entry = linelist_index.get(p_key)
            if entry:
                sheet_matched += 1
                matched_pids += 1
                # Enrich with operating data
                if entry.material:
                    p["material"] = entry.material
                if entry.fluid_phase:
                    p["fluid_phase"] = entry.fluid_phase
                if entry.operating_pressure_barg is not None:
                    p["operating_pressure"] = entry.operating_pressure_barg
                if entry.operating_temperature_c is not None:
                    p["operating_temperature"] = entry.operating_temperature_c
                if entry.design_pressure_barg is not None:
                    p["design_pressure"] = entry.design_pressure_barg
                if entry.design_temperature_c is not None:
                    p["design_temperature"] = entry.design_temperature_c
                if entry.insulation:
                    p["insulation"] = entry.insulation
                if entry.corrosion_allowance_mm is not None:
                    p["corrosion_allowance"] = entry.corrosion_allowance_mm
                if entry.corrosion_loop:
                    p["corrosion_loop"] = entry.corrosion_loop
            else:
                unmatched_pids += 1

        if sheet_matched > 0:
            # Recompute systems and circuits with new operating data
            sheet.systems_json = GroupingService.compute_circuits(sheet.result_json)
            flag_modified(sheet, "result_json")
            flag_modified(sheet, "systems_json")
            enriched_sheets.append(sheet.id)

    await db.commit()

    return LineListImportResult(
        filename=filename,
        total_rows=len(entries),
        matched_pids=matched_pids,
        unmatched_pids=unmatched_pids,
        enriched_sheets=enriched_sheets,
        entries=entries[:100]  # Return preview of first 100
    )


@router.get("", response_model=LineListResponse)
async def get_linelist_endpoint(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the currently active project Line List and operating parameters."""
    project = await ProjectService.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    if not project.linelist_json:
        return LineListResponse(
            project_id=project_id,
            filename="",
            total_rows=0,
            entries=[]
        )

    data = project.linelist_json
    entries = [LineListEntry(**e) for e in data.get("entries", [])]
    return LineListResponse(
        project_id=project_id,
        filename=data.get("filename", ""),
        total_rows=data.get("total_rows", len(entries)),
        entries=entries
    )
