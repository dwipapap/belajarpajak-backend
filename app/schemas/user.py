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
    password: str = Field(min_length=8)
    full_name: str
    role: Role
    #: Siswa-only login handle (NIK/NISN), globally unique.
    username: str | None = Field(default=None, max_length=32, pattern=r"^[a-zA-Z0-9]+$")
    # Only meaningful for superadmin (who may target any tenant). For admin the server
    # forces the caller's own tenant regardless of what is sent.
    tenant_id: int | None = None


class UserBulkItem(BaseModel):
    """One row of a bulk account creation."""

    full_name: str = Field(min_length=1, max_length=150)
    #: Required for guru; optional for siswa (auto-generated placeholder).
    email: str | None = Field(default=None, pattern=_EMAIL_PATTERN, max_length=255)
    #: Required for siswa (NIK/NISN); ignored for guru.
    username: str | None = Field(default=None, max_length=32, pattern=r"^[a-zA-Z0-9]+$")
    #: Omitted rows get the shared bootstrap password and must change it at login.
    password: str | None = Field(default=None, min_length=8, max_length=100)


class UserBulkCreate(BaseModel):
    role: Role
    accounts: list[UserBulkItem] = Field(max_length=200)
    #: Only meaningful for superadmin; admin is forced to their own tenant.
    tenant_id: int | None = None


class UserBulkResultRow(BaseModel):
    index: int
    ok: bool
    detail: str
    user_id: int | None = None
    email: str | None = None
    username: str | None = None


class UserBulkResult(BaseModel):
    created: int
    failed: int
    results: list[UserBulkResultRow]


class UserUpdate(BaseModel):
    full_name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    is_active: bool | None = None


class UserRead(BaseModel):
    id: int
    email: str
    username: str | None
    full_name: str
    role: Role
    tenant_id: int | None
    is_active: bool
    must_change_password: bool

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    size: int
