from typing import Literal, Optional
from pydantic import BaseModel, Field


class SymbolDetection(BaseModel):
    coarse: Literal["equipment", "instrument", "valve", "other"] = Field(
        ..., description="Coarse classification category"
    )
    cls: str = Field(..., description="Detailed model class or subtype name")
    conf: float = Field(default=1.0, ge=0.0, le=1.0, description="Detection confidence score (0.0 - 1.0)")
    x1: float = Field(..., description="Bounding box top-left x in original image pixels")
    y1: float = Field(..., description="Bounding box top-left y in original image pixels")
    x2: float = Field(..., description="Bounding box bottom-right x in original image pixels")
    y2: float = Field(..., description="Bounding box bottom-right y in original image pixels")
    subtype: Optional[str] = Field(default="", description="Refined engineering subtype (e.g. Ball Valve, Pump)")
    tag: Optional[str] = Field(default="", description="Engineering equipment or instrument tag (e.g. 605-V-201)")
    desc: Optional[str] = Field(default="", description="Human-readable description / function")
    manual: bool = Field(default=False, description="True if manually drawn/adjusted by engineer in GUI")


class SymbolPatch(BaseModel):
    id: Optional[int] = Field(None, description="Index in symbols array")
    coarse: Optional[Literal["equipment", "instrument", "valve", "other"]] = None
    cls: Optional[str] = None
    conf: Optional[float] = None
    x1: Optional[float] = None
    y1: Optional[float] = None
    x2: Optional[float] = None
    y2: Optional[float] = None
    subtype: Optional[str] = None
    tag: Optional[str] = None
    desc: Optional[str] = None
    manual: Optional[bool] = True
