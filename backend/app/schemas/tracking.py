from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class GroundTruthLogCreate(BaseModel):
    project_id: str
    sheet_id: Optional[str] = None
    action_type: str = Field(min_length=1, max_length=64)
    payload_json: Dict[str, Any] = Field(default_factory=dict)


class GroundTruthLogAccepted(BaseModel):
    status: str = "accepted"
