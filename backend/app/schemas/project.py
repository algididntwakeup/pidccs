from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field, model_validator

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
    latest_job_id: Optional[str] = Field(
        default=None,
        description="ID of the most recent detection job for this sheet (used to resume in-flight observation after navigation)",
    )
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _derive_latest_job_id(cls, data: Any) -> Any:
        """Populate latest_job_id from the eagerly-loaded ORM `jobs`
        relationship (lazy="selectin", so no extra query). Lets the frontend
        RESUME observing an in-flight detection after a remount.
        """
        if isinstance(data, dict):
            return data
        jobs = getattr(data, "jobs", None)
        if jobs:
            latest = max(jobs, key=lambda j: j.created_at or datetime.min)  # noqa: DTZ901
            # Attach to the (transient) ORM instance; SheetResponse reads it via
            # from_attributes. Not a mapped column, so this is a plain attribute.
            data.latest_job_id = latest.id
        return data

    class Config:
        from_attributes = True


class ProjectResponse(ProjectBase):
    id: str = Field(..., description="Unique project UUID")
    created_at: datetime
    updated_at: datetime
    sheets: List[SheetResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True
