from datetime import datetime
from typing import Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import Role


class UserLoginRequest(BaseModel):
    login: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: Role
    odoo_user_id: int
    name: str
    is_portal: bool = False
    partner_id: Optional[int] = None


class MagicLinkRequest(BaseModel):
    email: Optional[str] = None
    partner_id: Optional[int] = None
    deal_id: Optional[uuid.UUID] = None


class MagicLinkResponse(BaseModel):
    message: str
    token: Optional[str] = None
    portal_url: Optional[str] = None


class MagicLinkVerifyRequest(BaseModel):
    token: str = Field(..., min_length=1)


class UserRead(BaseModel):
    odoo_user_id: int
    login: str
    name: str
    role: Role
    sales_team_odoo_id: Optional[int] = None
    is_portal: bool
    partner_id: Optional[int] = None
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserUpdate(BaseModel):
    role: Optional[Role] = None
    is_active: Optional[bool] = None
    sales_team_odoo_id: Optional[int] = None
