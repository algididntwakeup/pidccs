from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

from .common import TenantUserBase


class ProjectBase(TenantUserBase):
    name: str = Field(..., max_length=255, description="Project or plant unit name (e.g. Unit 605 Amine Treating)")
    description: Optional[str] = Field(default="", description="Optional project description / metadata")


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class SheetResponse(TenantUserBase):
    id: str = Field(..., description="Unique sheet UUID")
    project_id: str = Field(..., description="Parent project UUID")
    filename: str = Field(..., description="Original filename (e.g. BCD3-605-42-PID-1-001.pdf)")
    sheet_number: str = Field(default="", description="Drawing sheet code (e.g. 001-01)")
    file_path: str = Field(..., description="Storage relative path")
    status: str = Field(default="uploaded", description="Status: uploaded, detecting, detected, error")
    dpi: int = Field(default=350)
    rot: int = Field(default=0)
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectResponse(ProjectBase):
    id: str = Field(..., description="Unique project UUID")
    created_at: datetime
    updated_at: datetime
    sheets: List[SheetResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True
