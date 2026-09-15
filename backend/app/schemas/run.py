from typing import Literal, List
from pydantic import BaseModel, Field


class PipeRun(BaseModel):
    points: List[List[int]] = Field(..., description="Polyline vertices in [[x, y], ...] pixel coordinates")
    axis: Literal["h", "v", "d", "poly"] = Field(default="poly", description="Orientation axis (horizontal, vertical, diagonal, poly)")
    x1: int = Field(..., description="Start point x")
    y1: int = Field(..., description="Start point y")
    x2: int = Field(..., description="End point x")
    y2: int = Field(..., description="End point y")
    underline: bool = Field(default=False, description="True if detected as title/tag text underline rather than process pipe")
