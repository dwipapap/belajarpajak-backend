"""Schemas for simulated e-Bupot BP21 workflows."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import Bp21Status, Bp21TaxFacility, Bp21TaxNature


class Bp21Base(BaseModel):
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

    tax_object_code: str = Field(max_length=30)
    income_type: str = Field(max_length=150)
    tax_nature: Bp21TaxNature = Bp21TaxNature.non_final
    tax_facility: Bp21TaxFacility = Bp21TaxFacility.none

    dpp: int = Field(ge=0)
    rate_percent: float = Field(ge=0, le=100)


class Bp21Create(Bp21Base):
    """Create a BP21 draft.

    For siswa, ``siswa_id`` is ignored and forced to the current user. Admin/guru may
    create on behalf of a siswa when that siswa is inside their allowed tenant/class scope.
    """


class Bp21Update(BaseModel):
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

    tax_object_code: str | None = Field(default=None, max_length=30)
    income_type: str | None = Field(default=None, max_length=150)
    tax_nature: Bp21TaxNature | None = None
    tax_facility: Bp21TaxFacility | None = None

    dpp: int | None = Field(default=None, ge=0)
    rate_percent: float | None = Field(default=None, ge=0, le=100)


class Bp21Review(BaseModel):
    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)


class Bp21Invalidate(BaseModel):
    invalid_reason: str = Field(min_length=1, max_length=500)


class Bp21Read(BaseModel):
    id: int
    tenant_id: int
    class_id: int | None
    siswa_id: int
    created_by_id: int

    status: Bp21Status
    withholding_number: str | None
    issued_at: datetime | None
    invalid_reason: str | None

    tax_month: int
    tax_year: int
    electronic_signature_status: str

    withholder_npwp: str | None
    withholder_name: str | None
    withholder_nitku: str | None

    recipient_identity_number: str
    recipient_name: str
    recipient_address: str | None

    tax_object_code: str
    income_type: str
    tax_nature: Bp21TaxNature
    tax_facility: Bp21TaxFacility

    dpp: int
    rate_percent: float
    income_tax: int

    score: int | None
    teacher_feedback: str | None
    created_at: datetime | None
    updated_at: datetime | None


class Bp21ListResponse(BaseModel):
    items: list[Bp21Read]
    total: int
    page: int
    size: int


class Bp21Summary(BaseModel):
    draft: int = 0
    issued: int = 0
    invalid: int = 0
    total: int = 0
