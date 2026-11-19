"""Auth request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import Role

# NOTE: plain `str` (not EmailStr) — email-validator rejects reserved TLDs like
# `.local`, which the seed data deliberately uses for non-routable dev accounts.


class LoginRequest(BaseModel):
    """``identifier`` is the login handle: email (admin/guru/superadmin) or
    username/NIK/NISN (siswa). Email and username are both globally unique,
    so no school code is needed to disambiguate."""

    identifier: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LearningSettingsRead(BaseModel):
    show_auto_check_details: bool = True
    allow_late_submission: bool = True
    allow_resubmission: bool = True


class MeResponse(BaseModel):
    id: int
    email: str
    username: str | None
    full_name: str
    role: Role
    tenant_id: int | None
    tenant_name: str | None
    is_active: bool
    must_change_password: bool
    enabled_modules: list[str] = Field(default_factory=list)
    settings: LearningSettingsRead = Field(default_factory=LearningSettingsRead)

