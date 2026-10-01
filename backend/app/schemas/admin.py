from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class AdminSheetSummary(BaseModel):
    id: str
    filename: str
    sheet_number: str


class AdminProjectSummary(BaseModel):
    id: str
    bima_project_id: Optional[str]
    name: str
    owner_name: Optional[str]
    owner_email: Optional[str]
    sheets: List[AdminSheetSummary]


class AdminActivitySummary(BaseModel):
    id: str
    user_id: str
    user_name: str
    user_email: str
    project_id: str
    project_name: str
    sheet_id: Optional[str]
    sheet_name: Optional[str]
    action_type: str
    payload_json: dict
    timestamp: datetime


class AdminJobErrorSummary(BaseModel):
    id: str
    project_id: str
    project_name: str
    sheet_id: str
    sheet_name: str
    status: str
    message: str
    error: str
    created_at: datetime


class AdminMonitoringResponse(BaseModel):
    projects: List[AdminProjectSummary]
    activities: List[AdminActivitySummary]
    recent_errors: List[AdminJobErrorSummary]
