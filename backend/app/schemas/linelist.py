from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field


class LineListEntry(BaseModel):
    line_number: str = Field(..., description="Canonical line number tag (matches PipingID.pid)")
    source_file: Optional[str] = Field(default="", description="Associated P&ID sheet / source file name")
    material: Optional[str] = Field(default="", description="Base material (e.g. CS, SS, LTCS)")
    operating_pressure_barg: Optional[float] = Field(default=None, description="Operating pressure in barg / psig")
    operating_temperature_c: Optional[float] = Field(default=None, description="Operating temperature in C / F")
    design_pressure_barg: Optional[float] = Field(default=None, description="Design pressure in barg")
    design_temperature_c: Optional[float] = Field(default=None, description="Design temperature in C")
    fluid_phase: Optional[Literal["L", "G", "2P", ""]] = Field(default="", description="Fluid phase: Liquid, Gas, or 2-Phase")
    insulation: Optional[str] = Field(default="", description="Insulation flag or specification (e.g. YES, NO, IH)")
    corrosion_allowance_mm: Optional[float] = Field(default=None, description="Corrosion allowance in mm")
    corrosion_rate_mmpy: Optional[float] = Field(default=None, description="Estimated corrosion rate in mm/year")
    corrosion_loop: Optional[str] = Field(default="", description="Existing engineering corrosion loop / circuit tag")
    service_condition: Optional[str] = Field(default="", description="Continuous, intermittent, etc.")
    notes: Optional[str] = Field(default="", description="Process or engineering notes")
    raw_attributes: Dict[str, Any] = Field(default_factory=dict, description="Original unparsed columns from sheet")


class LineListImportResult(BaseModel):
    filename: str
    total_rows: int
    matched_pids: int
    unmatched_pids: int
    enriched_sheets: List[str] = Field(default_factory=list)
    entries: List[LineListEntry] = Field(default_factory=list)


class LineListResponse(BaseModel):
    project_id: str
    filename: str
    total_rows: int
    entries: List[LineListEntry] = Field(default_factory=list)
