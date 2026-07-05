"""Auth request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import Role

# NOTE: plain `str` (not EmailStr) — email-validator rejects reserved TLDs like
# `.local`, which the seed data deliberately uses for non-routable dev accounts.


class LoginRequest(BaseModel):
    email: str
    password: str
    tenant_slug: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: int
    email: str
    full_name: str
    role: Role
    tenant_id: int | None
    tenant_name: str | None
    is_active: bool
