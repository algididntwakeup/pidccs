from typing import Literal, List, Optional
from pydantic import BaseModel, Field


class PipeRun(BaseModel):
    id: Optional[str] = Field(default=None, description="Unique run ID (e.g. run-0)")
    points: List[List[int]] = Field(..., description="Polyline vertices in [[x, y], ...] pixel coordinates")
    axis: Literal["h", "v", "d", "poly"] = Field(default="poly", description="Orientation axis (horizontal, vertical, diagonal, poly)")
    x1: int = Field(..., description="Start point x")
    y1: int = Field(..., description="Start point y")
    x2: int = Field(..., description="End point x")
    y2: int = Field(..., description="End point y")
    underline: bool = Field(default=False, description="True if detected as title/tag text underline rather than process pipe")
    color: str = Field(default="#2563EB", description="Display and export stroke color hex")
    label: str = Field(default="", description="Piping line label or tag (e.g. 605-6\"-GR-BDA-029-H50)")
    manual: bool = Field(default=False, description="True if manually edited or created by engineer")
    equipment_outline: bool = Field(default=False, description="True if run is an equipment (vessel/tank) outline contour, not a process pipe")
