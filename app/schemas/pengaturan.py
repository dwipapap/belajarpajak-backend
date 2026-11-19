"""Schemas for Pengaturan Instansi (per-school module toggles and learning settings)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.models.enums import TenantType
from app.schemas.auth import LearningSettingsRead
from app.schemas.tenant import TenantQuotaUsage


class ModuleCatalogItem(BaseModel):
    key: str
    label: str
    group: str
    depends_on: list[str] = Field(default_factory=list)
    dependency_hint: str | None = None


class PengaturanQuotas(BaseModel):
    guru: TenantQuotaUsage
    siswa: TenantQuotaUsage
    kelas: TenantQuotaUsage


class PengaturanProfile(BaseModel):
    tenant_id: int
    name: str
    slug: str
    type: TenantType
    is_active: bool
    contact_wa: str | None
    berlaku_dari: date | None
    berlaku_sampai: date | None
    days_remaining: int | None
    expired: bool
    expired_soon: bool
    quotas: PengaturanQuotas


class PengaturanRead(BaseModel):
    profile: PengaturanProfile
    module_catalog: list[ModuleCatalogItem]
    allowed_modules: list[str]
    enabled_modules: list[str]
    effective: list[str]
    settings: LearningSettingsRead


class LearningSettingsUpdate(BaseModel):
    show_auto_check_details: bool | None = None
    allow_late_submission: bool | None = None
    allow_resubmission: bool | None = None


class PengaturanUpdate(BaseModel):
    enabled_modules: list[str] | None = None
    settings: LearningSettingsUpdate | None = None


class PaketModulUpdate(BaseModel):
    allowed_modules: list[str] | None = None
