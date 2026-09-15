from typing import Literal, Optional, List
from pydantic import BaseModel, Field


class ConnectionPoint(BaseModel):
    codes: List[str] = Field(..., description="Pair of piping class codes at spec break (e.g. ['BCB', 'CCB'])")
    orient: Literal["h", "v"] = Field(default="h", description="Orientation of spec break pair (horizontal or vertical)")
    x: float = Field(..., description="Break coordinate x in image pixels")
    y: float = Field(..., description="Break coordinate y in image pixels")
    divider: bool = Field(default=False, description="True if divider stroke verified between token pair")
    in_vocab: bool = Field(default=True, description="True if both codes belong to known piping classes vocabulary")
    conf: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence of connection point detection")
    run_idx: int = Field(default=-1, description="Index of pipe run snapped to (-1 if unattached)")
    dist: Optional[float] = Field(default=None, description="Distance from text midpoint to snapped pipe polyline")
    side_a: str = Field(default="", description="Position side of first code (e.g. 'left' or 'top')")
    side_b: str = Field(default="", description="Position side of second code (e.g. 'right' or 'bottom')")
