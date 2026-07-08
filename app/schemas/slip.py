"""Schemas for simulated e-Bupot withholding slip workflows (all slip types).

One shared field set: BP21 and BP26 forms are structurally identical in Coretax,
so both endpoints accept and return every field; a field the form doesn't use is
simply null.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import SlipSptFlag, SlipStatus, SlipTaxFacility, SlipTaxNature


class SlipBase(BaseModel):
    class_id: int | None = None
    siswa_id: int | None = None
    tax_month: int = Field(ge=1, le=12)
    tax_year: int = Field(ge=2020, le=2100)

    withholder_npwp: str | None = Field(default=None, max_length=32)
    withholder_name: str | None = Field(default=None, max_length=150)
    withholder_nitku: str | None = Field(default=None, max_length=32)

    recipient_identity_number: str = Field(max_length=32)
    recipient_name: str = Field(max_length=150)
    recipient_address: str | None = Field(default=None, max_length=255)
    recipient_nitku: str | None = Field(default=None, max_length=32)

    ptkp_status: str | None = Field(default=None, max_length=10)

    tax_type: str | None = Field(default=None, max_length=30)
    tax_object_code: str = Field(max_length=30)
    tax_object_name: str | None = Field(default=None, max_length=150)
    income_type: str | None = Field(default=None, max_length=150)
    tax_nature: SlipTaxNature = SlipTaxNature.non_final
    tax_facility: SlipTaxFacility = SlipTaxFacility.none

    previous_gross_income: int = Field(default=0, ge=0)
    gross_income: int = Field(ge=0)
    dpp_percent: float = Field(ge=0, le=100)
    rate_percent: float = Field(ge=0, le=100)
    kap_kjs: str | None = Field(default=None, max_length=20)
    negara_treaty: str | None = Field(default=None, max_length=5)
    pasal_treaty: str | None = Field(default=None, max_length=20)
    nomor_skd: str | None = Field(default=None, max_length=100)
    tarif_treaty_basis_points: int | None = Field(default=None, ge=0, le=10000)

    document_type: str | None = Field(default=None, max_length=60)
    document_number: str | None = Field(default=None, max_length=60)
    document_date: date | None = Field(default=None)
    document_nitku: str | None = Field(default=None, max_length=32)


class SlipCreate(SlipBase):
    """Create a slip draft.

    For siswa, ``siswa_id`` is ignored and forced to the current user. Admin/guru may
    create on behalf of a siswa when that siswa is inside their allowed tenant/class scope.
    """


class SlipUpdate(BaseModel):
    class_id: int | None = None
    siswa_id: int | None = None
    tax_month: int | None = Field(default=None, ge=1, le=12)
    tax_year: int | None = Field(default=None, ge=2020, le=2100)

    withholder_npwp: str | None = Field(default=None, max_length=32)
    withholder_name: str | None = Field(default=None, max_length=150)
    withholder_nitku: str | None = Field(default=None, max_length=32)

    recipient_identity_number: str | None = Field(default=None, max_length=32)
    recipient_name: str | None = Field(default=None, max_length=150)
    recipient_address: str | None = Field(default=None, max_length=255)
    recipient_nitku: str | None = Field(default=None, max_length=32)

    ptkp_status: str | None = Field(default=None, max_length=10)

    tax_type: str | None = Field(default=None, max_length=30)
    tax_object_code: str | None = Field(default=None, max_length=30)
    tax_object_name: str | None = Field(default=None, max_length=150)
    income_type: str | None = Field(default=None, max_length=150)
    tax_nature: SlipTaxNature | None = None
    tax_facility: SlipTaxFacility | None = None

    previous_gross_income: int | None = Field(default=None, ge=0)
    gross_income: int | None = Field(default=None, ge=0)
    dpp_percent: float | None = Field(default=None, ge=0, le=100)
    rate_percent: float | None = Field(default=None, ge=0, le=100)
    kap_kjs: str | None = Field(default=None, max_length=20)
    negara_treaty: str | None = Field(default=None, max_length=5)
    pasal_treaty: str | None = Field(default=None, max_length=20)
    nomor_skd: str | None = Field(default=None, max_length=100)
    tarif_treaty_basis_points: int | None = Field(default=None, ge=0, le=10000)

    document_type: str | None = Field(default=None, max_length=60)
    document_number: str | None = Field(default=None, max_length=60)
    document_date: date | None = Field(default=None)
    document_nitku: str | None = Field(default=None, max_length=32)


class SlipReview(BaseModel):
    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)


class SlipInvalidate(BaseModel):
    invalid_reason: str = Field(min_length=1, max_length=500)


class SlipCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class SlipSptFlagUpdate(BaseModel):
    spt_flag: SlipSptFlag | None = None


class SlipBulkIssue(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class SlipRead(BaseModel):
    id: int
    tenant_id: int
    class_id: int | None
    siswa_id: int
    created_by_id: int

    status: SlipStatus
    withholding_number: str | None
    issued_at: datetime | None
    invalid_reason: str | None
    spt_flag: SlipSptFlag | None

    tax_month: int
    tax_year: int
    electronic_signature_status: str

    withholder_npwp: str | None
    withholder_name: str | None
    withholder_nitku: str | None

    recipient_identity_number: str
    recipient_name: str
    recipient_address: str | None
    recipient_nitku: str | None

    ptkp_status: str | None

    tax_type: str | None
    tax_object_code: str
    tax_object_name: str | None
    income_type: str | None
    tax_nature: SlipTaxNature
    tax_facility: SlipTaxFacility

    previous_gross_income: int
    gross_income: int
    dpp: int
    dpp_percent: float
    rate_percent: float
    income_tax: int
    kap_kjs: str | None
    negara_treaty: str | None
    pasal_treaty: str | None
    nomor_skd: str | None
    tarif_treaty_basis_points: int | None

    document_type: str | None
    document_number: str | None
    document_date: date | None
    document_nitku: str | None

    score: int | None
    teacher_feedback: str | None
    created_at: datetime | None
    updated_at: datetime | None


class SlipListResponse(BaseModel):
    items: list[SlipRead]
    total: int
    page: int
    size: int


class SlipSummary(BaseModel):
    draft: int = 0
    issued: int = 0
    invalid: int = 0
    total: int = 0


class SlipImportRowResult(BaseModel):
    row: int
    success: bool
    id: int | None = None
    error: str | None = None


class SlipImportResult(BaseModel):
    total_rows: int
    imported: int
    failed: int
    results: list[SlipImportRowResult]


class SlipBulkIssueResult(BaseModel):
    issued: int
    failed: int
    results: list[SlipImportRowResult]
