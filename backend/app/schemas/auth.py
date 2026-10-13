from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BimaSSOCallback(BaseModel):
    token: Optional[str] = None
    client_id: Optional[str] = None
    bima_user_id: Optional[str] = Field(default=None, max_length=255)
    email: Optional[str] = Field(default=None, max_length=320)
    full_name: str = Field(default="", max_length=255)
    department: Optional[str] = Field(default=None, max_length=128)
    role: Optional[str] = Field(default=None, max_length=64)
    is_bima_superuser: bool = False

    @model_validator(mode="after")
    def require_sso_token_or_mock_identity(self):
        if not self.token and not (self.bima_user_id and self.email):
            raise ValueError("Provide a BIMA SSO token or mock bima_user_id and email")
        return self


class SudoLoginRequest(BaseModel):
    secret_key: str = Field(min_length=1, max_length=4096)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    bima_user_id: str
    email: str
    full_name: str
    is_active: bool
    department: str
    role: str
    is_superuser: bool

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SSOCallbackResponse(TokenResponse):
    user: UserResponse
