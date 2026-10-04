"""Tenant (school / campus / training institution / company) model."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Column, Date
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

    #: WhatsApp contact of the institution PIC.
    contact_wa: str | None = Field(default=None, max_length=20)
    #: Instance validity end date — superadmin sets it (usually via a duration
    #: in months). Expiry only surfaces as a warning; blocking is deactivation.
    berlaku_sampai: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))

    #: Account/class quotas set by the superadmin; NULL = unlimited.
    max_guru: int | None = Field(default=None, ge=0)
    max_siswa: int | None = Field(default=None, ge=0)
    max_kelas: int | None = Field(default=None, ge=0)

    #: Reason recorded when the instance is deactivated (audit trail).
    deactivated_reason: str | None = Field(default=None, max_length=255)
