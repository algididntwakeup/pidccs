from typing import List, Optional, Literal
from pydantic import BaseModel, Field

from .symbol import SymbolDetection
from .piping import PipingID
from .run import PipeRun
from .connpoint import ConnectionPoint


class OffPageConnector(BaseModel):
    """Off-Page Connector (OPC) continuation marker linking piping across drawing sheets."""
    id: str = Field(..., description="Unique OPC identifier")
    x1: float = Field(..., description="Bounding box top-left X")
    y1: float = Field(..., description="Bounding box top-left Y")
    x2: float = Field(..., description="Bounding box bottom-right X")
    y2: float = Field(..., description="Bounding box bottom-right Y")
    direction: Literal["incoming", "outgoing", "bidirectional"] = Field(
        default="outgoing",
        description="Flow or continuation direction relative to this drawing"
    )
    target_drawing: str = Field(
        default="",
        description="Referenced target drawing identifier (e.g. 'BCD3-605-42-PID-1-006' or '006')"
    )
    target_sheet_number: str = Field(
        default="",
        description="Referenced sheet number if explicitly stated (e.g. '006' or '6')"
    )
    target_line: Optional[str] = Field(
        default=None,
        description="Target continuation line number if stated adjacent to the connector"
    )
    run_idx: int = Field(
        default=-1,
        description="Index into sheet runs[] of the pipe terminating at this connector"
    )
    piping_id: Optional[str] = Field(
        default=None,
        description="Piping ID tag of the pipe connected to this connector"
    )
    confidence: float = Field(
        default=0.9,
        description="Detection confidence score"
    )
    manual: bool = Field(
        default=False,
        description="True if manually added or verified by engineer"
    )


class DigitizationResult(BaseModel):
    image_path: str = Field(default="", description="Path or reference identifier of base drawing")
    dpi: int = Field(default=350, description="Rasterization resolution DPI")
    rot: int = Field(default=0, description="Manual rotation angle (0, 90, 180, 270)")
    w: int = Field(..., description="Image width in pixels")
    h: int = Field(..., description="Image height in pixels")
    symbols: List[SymbolDetection] = Field(default_factory=list, description="Detected symbols (equipment, valves, instruments)")
    runs: List[PipeRun] = Field(default_factory=list, description="Traced pipe run polylines")
    piping_ids: List[PipingID] = Field(default_factory=list, description="Detected piping line numbers")
    conn_points: List[ConnectionPoint] = Field(default_factory=list, description="Detected spec break connection points")
    opcs: List[OffPageConnector] = Field(default_factory=list, description="Detected off-page continuation connectors")
    furniture: List[List[int]] = Field(default_factory=list, description="Title blocks, notes, and tables bounding boxes [x1, y1, x2, y2]")
    _break_pairs: Optional[List[List[int]]] = None
    _split_done: Optional[bool] = None
