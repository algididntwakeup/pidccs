from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class CircuitProvenance(BaseModel):
    circuit_code: str = Field(..., description="Target circuit identifier code (e.g. '01.01')")
    rule: str = Field(..., description="Engineering standard rule applied (e.g. 'API_RP_970_PHASE_BOUNDARY')")
    evidence: str = Field(..., description="Human-readable factual justification")
    source: Literal["linelist", "material_spec", "piping_class", "heuristic", "user_override"] = Field(
        default="heuristic", description="Provenance origin of grouping boundary"
    )
    confidence: float = Field(default=1.0, description="Rule match confidence (0.0 to 1.0)")
    timestamp: Optional[str] = Field(default=None, description="ISO timestamp of circuit calculation")


class CorrosionCircuit(BaseModel):
    code: str = Field(..., description="Hierarchical circuit identifier (e.g. '01.01')")
    material: str = Field(default="", description="Base material category derived from line list or piping class")
    classes: List[str] = Field(default_factory=list, description="Piping class codes grouped in this circuit")
    fluid_phase: str = Field(default="", description="Phase boundary if specified (L, G, 2P)")
    color: List[int] = Field(..., description="RGB color tuple [r, g, b] with gradation")
    run_idxs: List[int] = Field(default_factory=list, description="Indices of pipe runs belonging to circuit")
    pid_idxs: List[int] = Field(default_factory=list, description="Indices of piping IDs belonging to circuit")
    operating_summary: Optional[Dict[str, Any]] = Field(
        default=None, description="Summary statistics of operating P, T, and CA across circuit members"
    )
    provenance: Optional[CircuitProvenance] = Field(
        default=None, description="API RP 970 audit trail documenting rule justification"
    )


class CorrosionSystem(BaseModel):
    index: int = Field(..., description="1-based system index")
    fluid: str = Field(..., description="Process fluid code identifying this system")
    color: List[int] = Field(..., description="Global persistent RGB color [r, g, b]")
    run_idxs: List[int] = Field(default_factory=list, description="All pipe run indices belonging to system")
    pid_idxs: List[int] = Field(default_factory=list, description="All piping ID indices belonging to system")
    n_pipes: int = Field(default=0, description="Count of distinct pipe runs in this system")
    circuits: List[CorrosionCircuit] = Field(default_factory=list, description="Sub-circuits partitioned by material")
