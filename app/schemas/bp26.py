"""Schemas for simulated e-Bupot BP26 workflows."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import Bp26SptFlag, Bp26Status, Bp26TaxFacility, Bp26TaxNature


class Bp26Base(BaseModel):
    class_id: int | None = None
    siswa_id: int | None = None
    tax_month: int = Field(ge=1, le=12)
    tax_year: int = Field(ge=2020, le=2100)

    recipient_identity_number: str = Field(max_length=32)
    recipient_name: str = Field(max_length=150)

    tax_type: str = Field(default="PPh 26", max_length=30)
    tax_object_code: str = Field(max_length=30)
    tax_object_name: str = Field(max_length=150)
    tax_nature: Bp26TaxNature = Bp26TaxNature.final
    tax_facility: Bp26TaxFacility = Bp26TaxFacility.none

    gross_income: int = Field(ge=0)
    dpp_percent: float = Field(ge=0, le=100)
    rate_percent: float = Field(ge=0, le=100)
    kap_kjs: str | None = Field(default=None, max_length=20)

    document_type: str | None = Field(default=None, max_length=60)
    document_number: str | None = Field(default=None, max_length=60)
    document_date: date | None = Field(default=None)
    document_nitku: str | None = Field(default=None, max_length=32)


class Bp26Create(Bp26Base):
    """Create a BP26 draft.

    For siswa, ``siswa_id`` is ignored and forced to the current user. Admin/guru may
    create on behalf of a siswa when that siswa is inside their allowed tenant/class scope.
    """


class Bp26Update(BaseModel):
    class_id: int | None = None
    siswa_id: int | None = None
    tax_month: int | None = Field(default=None, ge=1, le=12)
    tax_year: int | None = Field(default=None, ge=2020, le=2100)

    recipient_identity_number: str | None = Field(default=None, max_length=32)
    recipient_name: str | None = Field(default=None, max_length=150)

    tax_type: str | None = Field(default=None, max_length=30)
    tax_object_code: str | None = Field(default=None, max_length=30)
    tax_object_name: str | None = Field(default=None, max_length=150)
    tax_nature: Bp26TaxNature | None = None
    tax_facility: Bp26TaxFacility | None = None

    gross_income: int | None = Field(default=None, ge=0)
    dpp_percent: float | None = Field(default=None, ge=0, le=100)
    rate_percent: float | None = Field(default=None, ge=0, le=100)
    kap_kjs: str | None = Field(default=None, max_length=20)

    document_type: str | None = Field(default=None, max_length=60)
    document_number: str | None = Field(default=None, max_length=60)
    document_date: date | None = Field(default=None)
    document_nitku: str | None = Field(default=None, max_length=32)


class Bp26Review(BaseModel):
    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)


class Bp26Invalidate(BaseModel):
    invalid_reason: str = Field(min_length=1, max_length=500)


class Bp26Cancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class Bp26SptFlagUpdate(BaseModel):
    spt_flag: Bp26SptFlag | None = None


class Bp26BulkIssue(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class Bp26Read(BaseModel):
    id: int
    tenant_id: int
    class_id: int | None
    siswa_id: int
    created_by_id: int

    status: Bp26Status
    withholding_number: str | None
    issued_at: datetime | None
    invalid_reason: str | None
    spt_flag: Bp26SptFlag | None

    tax_month: int
    tax_year: int
    electronic_signature_status: str

    recipient_identity_number: str
    recipient_name: str

    tax_type: str
    tax_object_code: str
    tax_object_name: str
    tax_nature: Bp26TaxNature
    tax_facility: Bp26TaxFacility

    gross_income: int
    dpp: int
    dpp_percent: float
    rate_percent: float
    income_tax: int
    kap_kjs: str | None

    document_type: str | None
    document_number: str | None
    document_date: date | None
    document_nitku: str | None

    score: int | None
    teacher_feedback: str | None
    created_at: datetime | None
    updated_at: datetime | None


class Bp26ListResponse(BaseModel):
    items: list[Bp26Read]
    total: int
    page: int
    size: int


class Bp26Summary(BaseModel):
    draft: int = 0
    issued: int = 0
    invalid: int = 0
    total: int = 0


class Bp26ImportRowResult(BaseModel):
    row: int
    success: bool
    id: int | None = None
    error: str | None = None


class Bp26ImportResult(BaseModel):
    total_rows: int
    imported: int
    failed: int
    results: list[Bp26ImportRowResult]


class Bp26BulkIssueResult(BaseModel):
    issued: int
    failed: int
    results: list[Bp26ImportRowResult]
