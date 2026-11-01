"""Tenant request/response schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.models.enums import TenantType


class TenantAdminCreate(BaseModel):
    """First admin account created together with a new institution."""

    email: str = Field(min_length=3, max_length=255)
    full_name: str = Field(min_length=1, max_length=150)
    #: Defaults to the shared bootstrap password; the admin is prompted to
    #: change it on first login (must_change_password starts true).
    password: str = Field(default="Password123!", min_length=8, max_length=100)


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    type: TenantType
    contact_wa: str | None = Field(default=None, max_length=20)
    #: Instance validity as a duration; stored as ``berlaku_sampai``.
    masa_berlaku_bulan: int = Field(default=12, ge=1, le=120)
    #: Quotas; 0/null = unlimited.
    max_guru: int | None = Field(default=None, ge=0)
    max_siswa: int | None = Field(default=None, ge=0)
    max_kelas: int | None = Field(default=None, ge=0)
    admin: TenantAdminCreate


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    type: TenantType | None = None
    contact_wa: str | None = Field(default=None, max_length=20)
    #: Setting this recomputes ``berlaku_sampai`` as
    #: ``max(berlaku_sampai_lama, today) + N bulan`` (kalender, jaga akhir-bulan).
    #: ``berlaku_dari`` is left untouched unless the period already expired
    #: (or was NULL), in which case a new period starts from today.
    masa_berlaku_bulan: int | None = Field(default=None, ge=1, le=120)
    max_guru: int | None = Field(default=None, ge=0)
    max_siswa: int | None = Field(default=None, ge=0)
    max_kelas: int | None = Field(default=None, ge=0)
    #: Explicit period override (YYYY-MM-DD). When either is sent, it wins
    #: over ``masa_berlaku_bulan``; the unsent side is kept as-is.
    berlaku_dari: date | None = None
    berlaku_sampai: date | None = None
    #: Slug is immutable — accepted by the schema only so the router can
    #: reject it explicitly with a clear 422 message.
    slug: str | None = Field(default=None, min_length=2, max_length=80)


class TenantStatusUpdate(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class TenantDeleteConfirm(BaseModel):
    confirm_code: str = Field(min_length=1, max_length=80)


class TenantRead(BaseModel):
    id: int
    name: str
    slug: str
    type: TenantType
    is_active: bool
    contact_wa: str | None
    berlaku_dari: date | None
    berlaku_sampai: date | None
    max_guru: int | None
    max_siswa: int | None
    max_kelas: int | None
    deactivated_reason: str | None

    model_config = {"from_attributes": True}


class TenantQuotaUsage(BaseModel):
    used: int
    limit: int | None
    full: bool


class TenantSummaryAdmin(BaseModel):
    id: int
    full_name: str
    email: str
    is_active: bool


class TenantSummary(BaseModel):
    tenant_id: int
    name: str
    slug: str
    type: TenantType
    is_active: bool
    berlaku_dari: date | None
    berlaku_sampai: date | None
    days_remaining: int | None
    expired: bool
    expired_soon: bool
    guru: TenantQuotaUsage
    siswa: TenantQuotaUsage
    kelas: TenantQuotaUsage
    admin: TenantSummaryAdmin | None
