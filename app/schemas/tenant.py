"""Tenant request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import TenantType


class TenantCreate(BaseModel):
    name: str
    slug: str
    type: TenantType


class TenantUpdate(BaseModel):
    name: str | None = None
    type: TenantType | None = None
    is_active: bool | None = None


class TenantRead(BaseModel):
    id: int
    name: str
    slug: str
    type: TenantType
    is_active: bool

    model_config = {"from_attributes": True}
