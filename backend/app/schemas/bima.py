from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BimaProjectRecord(BaseModel):
    bima_project_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = ""
    assigned_bima_user_ids: List[str] = Field(default_factory=list)


class BimaProjectSyncRequest(BaseModel):
    bima_access_token: Optional[str] = None


class BimaIdentity(BaseModel):
    bima_user_id: str
    email: str
    full_name: str
    role: str


class SyncedProject(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    bima_project_id: Optional[str]
    owner_id: Optional[str]
    name: str
    description: str

class BimaProjectSyncResponse(BaseModel):
    synced_count: int
    projects: List[SyncedProject]
