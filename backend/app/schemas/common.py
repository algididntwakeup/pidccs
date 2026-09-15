from typing import Optional
from pydantic import BaseModel, Field


class TenantUserBase(BaseModel):
    """Base schema providing forward-compatible multi-tenancy & multi-user fields.
    Defaulted to 'default_tenant' and 'default_user' during Phase A/B.
    """
    tenant_id: str = Field(default="default_tenant", description="Tenant identifier for multi-tenancy")
    user_id: str = Field(default="default_user", description="Owner user identifier")
