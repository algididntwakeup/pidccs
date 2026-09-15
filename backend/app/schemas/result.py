from typing import List, Optional
from pydantic import BaseModel, Field

from .symbol import SymbolDetection
from .piping import PipingID
from .run import PipeRun
from .connpoint import ConnectionPoint


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
    furniture: List[List[int]] = Field(default_factory=list, description="Title blocks, notes, and tables bounding boxes [x1, y1, x2, y2]")
    _break_pairs: Optional[List[List[int]]] = None
    _split_done: Optional[bool] = None
