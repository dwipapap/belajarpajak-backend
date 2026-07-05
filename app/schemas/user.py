"""User request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import Role

# Plain `str` + lightweight shape check instead of EmailStr: email-validator rejects
# reserved TLDs like `.local`, which dev/seed accounts use. Accounts are provisioned
# by admins, so strict RFC validation adds friction without real value here.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserCreate(BaseModel):
    email: str = Field(pattern=_EMAIL_PATTERN, max_length=255)
    password: str
    full_name: str
    role: Role
    # Only meaningful for superadmin (who may target any tenant). For admin the server
    # forces the caller's own tenant regardless of what is sent.
    tenant_id: int | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    password: str | None = None
    is_active: bool | None = None


class UserRead(BaseModel):
    id: int
    email: str
    full_name: str
    role: Role
    tenant_id: int | None
    is_active: bool

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    size: int
