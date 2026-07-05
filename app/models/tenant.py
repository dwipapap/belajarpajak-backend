"""Tenant (school / campus / training institution) model."""

from __future__ import annotations

from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import TenantType


class Tenant(TimestampMixin, table=True):
    __tablename__ = "tenants"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=150, index=True)
    slug: str = Field(max_length=80, unique=True, index=True)
    type: TenantType = Field(
        sa_column=Column(SAEnum(TenantType, name="tenant_type"), nullable=False)
    )
    is_active: bool = Field(default=True, nullable=False)
