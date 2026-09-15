from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


class JobResponse(BaseModel):
    job_id: str = Field(..., description="Unique Celery/ARQ task UUID")
    sheet_id: str = Field(..., description="Associated P&ID sheet UUID")
    status: Literal["queued", "processing", "completed", "failed"] = Field(
        default="queued", description="Current execution state"
    )
    progress_pct: int = Field(default=0, ge=0, le=100, description="Overall completion percentage (0 - 100)")
    step: str = Field(default="", description="Active pipeline stage (e.g. ocr_tiled, yolo_detect, tracing)")
    message: str = Field(default="", description="Human-readable progress status")
    error: Optional[str] = Field(default=None, description="Error message if task failed")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class ProgressEvent(BaseModel):
    type: str = "progress"
    job_id: str
    step: str
    current: int = 0
    total: int = 100
    pct: int = 0
    message: str = ""
