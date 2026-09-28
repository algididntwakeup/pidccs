from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class LineListEntry(BaseModel):
    line_number: str = Field(..., description="Canonical line number tag (matches PipingID.pid)")
    design_pressure_barg: Optional[float] = Field(default=None, description="Design pressure in barg")
    design_temperature_c: Optional[float] = Field(default=None, description="Design temperature in C")
    operating_pressure_barg: Optional[float] = Field(default=None, description="Operating pressure in barg / psig")
    operating_temperature_c: Optional[float] = Field(default=None, description="Operating temperature in C / F")
    fluid_phase: Optional[str] = Field(default="", description="Fluid phase: Liquid (L), Gas (G), or 2-Phase (2P)")
    insulation: Optional[str] = Field(default="", description="Insulation flag or specification (e.g. YES, NO, IH)")
    notes: Optional[str] = Field(default="", description="Process or engineering notes / remarks")
    source_file: Optional[str] = Field(default="", description="Associated P&ID sheet / source file name")
    material: Optional[str] = Field(default="", description="Base material (e.g. CS, SS, LTCS)")
    corrosion_allowance_mm: Optional[float] = Field(default=None, description="Corrosion allowance in mm")
    corrosion_rate_mmpy: Optional[float] = Field(default=None, description="Estimated corrosion rate in mm/year")
    corrosion_loop: Optional[str] = Field(default="", description="Existing engineering corrosion loop / circuit tag")
    service_condition: Optional[str] = Field(default="", description="Continuous, intermittent, etc.")
    raw_attributes: Dict[str, Any] = Field(default_factory=dict, description="Original unparsed columns from sheet")


class LineListImportResult(BaseModel):
    status: str = Field(default="success", description="Status of import operation")
    entries_parsed: int = Field(default=0, description="Total entries successfully parsed from file")
    pids_enriched: int = Field(default=0, description="Number of piping IDs enriched across sheets")
    filename: str = Field(default="", description="Source file name")
    total_rows: int = Field(default=0, description="Total rows in line list")
    matched_pids: int = Field(default=0, description="Number of matched piping IDs")
    unmatched_pids: int = Field(default=0, description="Number of unmatched piping IDs")
    enriched_sheets: List[str] = Field(default_factory=list, description="IDs of sheets enriched")
    entries: List[LineListEntry] = Field(default_factory=list, description="Preview of parsed entries")


class LineListResponse(BaseModel):
    project_id: str
    filename: str
    total_rows: int
    entries: List[LineListEntry] = Field(default_factory=list)
