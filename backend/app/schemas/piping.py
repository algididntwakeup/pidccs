from typing import Literal, Optional, List
from pydantic import BaseModel, Field


class PipingID(BaseModel):
    pid: str = Field(..., description="Canonical line number tag (e.g. 695-6\"-GF-CCB-026)")
    x1: float = Field(..., description="Top-left x in image pixels")
    y1: float = Field(..., description="Top-left y in image pixels")
    x2: float = Field(..., description="Bottom-right x in image pixels")
    y2: float = Field(..., description="Bottom-right y in image pixels")
    unit: str = Field(default="", description="Plant unit/area number (e.g. 605, 695)")
    size: str = Field(default="", description="Nominal pipe diameter with inch-mark (e.g. 6\", 1 1/2\")")
    fluid: str = Field(default="", description="Process fluid code for API RP 970 systemization (e.g. GF, LO)")
    pclass: str = Field(default="", description="Piping class specification code (e.g. CCB, CPA, A1A2)")
    seq: str = Field(default="", description="Piping line sequence number (e.g. 026, 201A)")
    conf: int = Field(default=0, description="Voting confidence score from multi-pass OCR")
    run_idx: int = Field(default=-1, description="Primary pipe run index in runs[] array (-1 if unlinked)")
    extra_runs: List[int] = Field(default_factory=list, description="Additional pipe run indices for multi-label lines")
    state: Literal["attached", "leader", "none", "manual", "propagated"] = Field(
        default="none", description="Association linkage mechanism"
    )
    manual: bool = Field(default=False, description="True if manually created or adjusted by engineer")


class PipingIDPatch(BaseModel):
    pid: Optional[str] = None
    x1: Optional[float] = None
    y1: Optional[float] = None
    x2: Optional[float] = None
    y2: Optional[float] = None
    unit: Optional[str] = None
    size: Optional[str] = None
    fluid: Optional[str] = None
    pclass: Optional[str] = None
    seq: Optional[str] = None
    run_idx: Optional[int] = None
    extra_runs: Optional[List[int]] = None
    state: Optional[Literal["attached", "leader", "none", "manual", "propagated"]] = None
    manual: Optional[bool] = True
