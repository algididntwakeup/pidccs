from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

from .system import CircuitProvenance


class TopologyNode(BaseModel):
    """Node in the multi-page P&ID project graph (Sheet or Circuit)."""
    id: str = Field(..., description="Unique node identifier")
    type: Literal["sheet", "circuit"] = Field(..., description="Node classification")
    label: str = Field(..., description="Human-readable display label")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Custom node attributes")


class TopologyEdge(BaseModel):
    """Inter-sheet connection edge linking pipe runs across sheets via OPCs."""
    id: str = Field(..., description="Unique edge identifier")
    source_sheet_id: str = Field(..., description="Sheet ID where pipe originates or exits")
    target_sheet_id: str = Field(..., description="Sheet ID where pipe enters or continues")
    source_opc_id: str = Field(..., description="ID of source OffPageConnector")
    target_opc_id: Optional[str] = Field(default=None, description="ID of matching target OffPageConnector")
    piping_id: Optional[str] = Field(default=None, description="Piping line tag transitioning across sheets")
    fluid: Optional[str] = Field(default=None, description="Process fluid code")
    confidence: float = Field(default=0.9, description="Link confidence score")


class ProjectCircuit(BaseModel):
    """Unified API RP 970 corrosion circuit spanning across multiple sheets in a plant unit."""
    circuit_code: str = Field(..., description="Unified circuit identifier (e.g. '01.01')")
    fluid: str = Field(..., description="Process fluid identifier")
    material: str = Field(..., description="Base metallurgy of construction")
    fluid_phase: str = Field(default="", description="Operating fluid phase (L, G, 2P)")
    color: List[int] = Field(..., description="Standard RGB color tuple")
    sheet_ids: List[str] = Field(default_factory=list, description="All P&ID sheet IDs traversed by this circuit")
    member_pids: List[str] = Field(default_factory=list, description="Unique piping line IDs belonging to this circuit")
    total_pipes: int = Field(default=0, description="Total physical pipe runs across all sheets")
    operating_summary: Optional[Dict[str, Any]] = Field(default=None, description="Aggregated operating data")
    provenance: Optional[CircuitProvenance] = Field(default=None, description="Audit trail and decision rationale")


class ProjectTopologyResponse(BaseModel):
    """Full topological response of a P&ID project with inter-sheet continuum."""
    project_id: str = Field(..., description="Project workspace identifier")
    nodes: List[TopologyNode] = Field(default_factory=list, description="Graph nodes (drawings and circuits)")
    edges: List[TopologyEdge] = Field(default_factory=list, description="Inter-sheet connectivity edges")
    circuits: List[ProjectCircuit] = Field(default_factory=list, description="Aggregated multi-sheet corrosion circuits")
    summary: Dict[str, Any] = Field(default_factory=dict, description="High-level topological metrics")
