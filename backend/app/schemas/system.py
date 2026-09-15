from typing import List, Tuple
from pydantic import BaseModel, Field


class CorrosionCircuit(BaseModel):
    code: str = Field(..., description="Hierarchical circuit identifier (e.g. '01.01')")
    material: str = Field(default="", description="Base material category derived from piping class")
    classes: List[str] = Field(default_factory=list, description="Piping class codes grouped in this circuit")
    color: List[int] = Field(..., description="RGB color tuple [r, g, b] with gradation")
    run_idxs: List[int] = Field(default_factory=list, description="Indices of pipe runs belonging to circuit")
    pid_idxs: List[int] = Field(default_factory=list, description="Indices of piping IDs belonging to circuit")


class CorrosionSystem(BaseModel):
    index: int = Field(..., description="1-based system index")
    fluid: str = Field(..., description="Process fluid code identifying this system")
    color: List[int] = Field(..., description="Global persistent RGB color [r, g, b]")
    run_idxs: List[int] = Field(default_factory=list, description="All pipe run indices belonging to system")
    pid_idxs: List[int] = Field(default_factory=list, description="All piping ID indices belonging to system")
    n_pipes: int = Field(default=0, description="Count of distinct pipe runs in this system")
    circuits: List[CorrosionCircuit] = Field(default_factory=list, description="Sub-circuits partitioned by material")
