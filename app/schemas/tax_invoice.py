"""Schemas for the simulated e-Faktur Pajak Keluaran workflow.

Clients send human units — ``quantity`` as a decimal, rates as percentages —
and never send derived amounts. Every ``total_*``/``dpp``/``ppn`` value in a
read schema was computed server-side from the lines, so a student cannot submit
a faktur whose arithmetic disagrees with the tax rules being taught.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import (
    BuyerIdentityType,
    TaxInvoiceKind,
    TaxInvoiceLineType,
    TaxInvoiceStatus,
)

TRANSACTION_CODES = (
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
)

#: Kode transaksi whose DPP is always the "nilai lain" base (11/12 of the price).
DPP_OTHER_CODES = frozenset({"04", "05"})


class TaxInvoiceLineBase(BaseModel):
    line_type: TaxInvoiceLineType = TaxInvoiceLineType.barang
    item_code: str | None = Field(default=None, max_length=20)
    item_name: str = Field(min_length=1, max_length=255)
    unit: str = Field(min_length=1, max_length=40)

    unit_price: int = Field(default=0, ge=0)
    quantity: float = Field(default=1, ge=0)
    discount: int = Field(default=0, ge=0)

    use_dpp_other: bool = False
    ppn_rate_percent: float = Field(default=12, ge=0, le=100)
    ppnbm_rate_percent: float = Field(default=0, ge=0, le=100)


class TaxInvoiceLineCreate(TaxInvoiceLineBase):
    """A Detail Transaksi row as submitted by the form."""


class TaxInvoiceLineUpdate(BaseModel):
    line_type: TaxInvoiceLineType | None = None
    item_code: str | None = Field(default=None, max_length=20)
    item_name: str | None = Field(default=None, min_length=1, max_length=255)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    unit_price: int | None = Field(default=None, ge=0)
    quantity: float | None = Field(default=None, ge=0)
    discount: int | None = Field(default=None, ge=0)
    use_dpp_other: bool | None = None
    ppn_rate_percent: float | None = Field(default=None, ge=0, le=100)
    ppnbm_rate_percent: float | None = Field(default=None, ge=0, le=100)


class TaxInvoiceLineRead(BaseModel):
    id: int
    invoice_id: int
    line_order: int

    line_type: TaxInvoiceLineType
    item_code: str | None
    item_name: str
    unit: str

    unit_price: int
    quantity: float
    total_price: int
    discount: int

    dpp: int
    use_dpp_other: bool
    dpp_other: int
    ppn_rate_percent: float
    ppn: int
    ppnbm_rate_percent: float
    ppnbm: int


class TaxInvoiceBase(BaseModel):
    class_id: int | None = None
    siswa_id: int | None = None

    is_advance_payment: bool = False
    is_settlement: bool = False
    transaction_code: str = Field(pattern=r"^(0[1-9]|10)$")
    invoice_date: date
    invoice_kind: TaxInvoiceKind = TaxInvoiceKind.normal
    tax_month: int = Field(ge=1, le=12)
    tax_year: int = Field(ge=2020, le=2100)
    reference: str | None = Field(default=None, max_length=255)

    seller_npwp: str | None = Field(default=None, max_length=32)
    seller_name: str | None = Field(default=None, max_length=150)
    seller_address: str | None = Field(default=None, max_length=255)
    seller_idtku: str | None = Field(default=None, max_length=32)

    buyer_identity_type: BuyerIdentityType = BuyerIdentityType.npwp
    buyer_identity_number: str = Field(min_length=1, max_length=32)
    buyer_country: str = Field(default="ID", min_length=2, max_length=2)
    buyer_document_number: str | None = Field(default=None, max_length=60)
    buyer_name: str = Field(min_length=1, max_length=150)
    buyer_address: str | None = Field(default=None, max_length=255)
    buyer_idtku: str | None = Field(default=None, max_length=32)
    buyer_email: str | None = Field(default=None, max_length=150)


class TaxInvoiceCreate(TaxInvoiceBase):
    """Create a faktur draft together with its Detail Transaksi rows.

    For siswa, ``siswa_id`` is ignored and forced to the current user. Admin/guru
    may create on behalf of a siswa inside their allowed tenant/class scope.
    """

    lines: list[TaxInvoiceLineCreate] = Field(default_factory=list, max_length=100)


class TaxInvoiceUpdate(BaseModel):
    class_id: int | None = None
    siswa_id: int | None = None

    is_advance_payment: bool | None = None
    is_settlement: bool | None = None
    transaction_code: str | None = Field(default=None, pattern=r"^(0[1-9]|10)$")
    invoice_date: date | None = None
    invoice_kind: TaxInvoiceKind | None = None
    tax_month: int | None = Field(default=None, ge=1, le=12)
    tax_year: int | None = Field(default=None, ge=2020, le=2100)
    reference: str | None = Field(default=None, max_length=255)

    seller_npwp: str | None = Field(default=None, max_length=32)
    seller_name: str | None = Field(default=None, max_length=150)
    seller_address: str | None = Field(default=None, max_length=255)
    seller_idtku: str | None = Field(default=None, max_length=32)

    buyer_identity_type: BuyerIdentityType | None = None
    buyer_identity_number: str | None = Field(default=None, min_length=1, max_length=32)
    buyer_country: str | None = Field(default=None, min_length=2, max_length=2)
    buyer_document_number: str | None = Field(default=None, max_length=60)
    buyer_name: str | None = Field(default=None, min_length=1, max_length=150)
    buyer_address: str | None = Field(default=None, max_length=255)
    buyer_idtku: str | None = Field(default=None, max_length=32)
    buyer_email: str | None = Field(default=None, max_length=150)

    #: When present, replaces the whole Detail Transaksi set.
    lines: list[TaxInvoiceLineCreate] | None = Field(default=None, max_length=100)


class TaxInvoiceRead(BaseModel):
    id: int
    tenant_id: int
    class_id: int | None
    siswa_id: int
    created_by_id: int

    status: TaxInvoiceStatus
    invoice_number: str | None
    issued_at: datetime | None
    invalid_reason: str | None
    electronic_signature_status: str

    is_advance_payment: bool
    is_settlement: bool
    transaction_code: str
    invoice_date: date
    invoice_kind: TaxInvoiceKind
    tax_month: int
    tax_year: int
    reference: str | None

    seller_npwp: str | None
    seller_name: str | None
    seller_address: str | None
    seller_idtku: str | None

    buyer_identity_type: BuyerIdentityType
    buyer_identity_number: str
    buyer_country: str
    buyer_document_number: str | None
    buyer_name: str
    buyer_address: str | None
    buyer_idtku: str | None
    buyer_email: str | None

    total_price: int
    total_discount: int
    total_dpp: int
    total_dpp_other: int
    total_ppn: int
    total_ppnbm: int

    score: int | None
    teacher_feedback: str | None
    created_at: datetime | None
    updated_at: datetime | None

    lines: list[TaxInvoiceLineRead] = Field(default_factory=list)


class TaxInvoiceListResponse(BaseModel):
    items: list[TaxInvoiceRead]
    total: int
    page: int
    size: int


class TaxInvoiceSummary(BaseModel):
    draft: int = 0
    issued: int = 0
    invalid: int = 0
    total: int = 0


class TaxInvoiceReview(BaseModel):
    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)


class TaxInvoiceCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class TaxInvoiceBulkIssue(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class TaxInvoiceBulkIssueResult(BaseModel):
    issued: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
