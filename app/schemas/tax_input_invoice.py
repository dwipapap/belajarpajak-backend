"""Schemas for Pajak Masukan (input-tax) — mirrors Pajak Keluaran.

Clients send human units — quantity as Decimal, rates as percentages —
and never send derived amounts. Totals are computed server-side.
credit_status is orthogonal to status (draft/issued/invalid).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import (
    BuyerIdentityType,
    TaxInputCreditStatus,
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

DPP_OTHER_CODES = frozenset({"04", "05"})


class TaxInputLineBase(BaseModel):
    line_type: TaxInvoiceLineType = TaxInvoiceLineType.barang
    item_code: str | None = Field(default=None, max_length=20)
    item_name: str = Field(min_length=1, max_length=255)
    unit: str = Field(min_length=1, max_length=40)

    unit_price: int = Field(default=0, ge=0)
    quantity: Decimal = Field(default=Decimal("1"), ge=0)
    discount: int = Field(default=0, ge=0)

    use_dpp_other: bool = False
    ppn_rate_percent: float = Field(default=12, ge=0, le=100)
    ppnbm_rate_percent: float = Field(default=0, ge=0, le=100)

    @field_validator("quantity")
    @classmethod
    def _reject_quantity_beyond_3_decimals(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -3:
            raise ValueError("Kuantitas maksimal 3 digit pecahan")
        return v


class TaxInputLineCreate(TaxInputLineBase):
    """A Detail Transaksi row for masukan."""


class TaxInputLineUpdate(BaseModel):
    line_type: TaxInvoiceLineType | None = None
    item_code: str | None = Field(default=None, max_length=20)
    item_name: str | None = Field(default=None, min_length=1, max_length=255)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    unit_price: int | None = Field(default=None, ge=0)
    quantity: Decimal | None = Field(default=None, ge=0)
    discount: int | None = Field(default=None, ge=0)
    use_dpp_other: bool | None = None
    ppn_rate_percent: float | None = Field(default=None, ge=0, le=100)
    ppnbm_rate_percent: float | None = Field(default=None, ge=0, le=100)

    @field_validator("quantity")
    @classmethod
    def _reject_quantity_beyond_3_decimals(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v.as_tuple().exponent < -3:
            raise ValueError("Kuantitas maksimal 3 digit pecahan")
        return v


class TaxInputLineRead(BaseModel):
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


class TaxInputBase(BaseModel):
    class_id: int | None = None
    siswa_id: int | None = None
    source_invoice_id: int | None = None

    invoice_number: str | None = Field(default=None, max_length=24, pattern=r"^\d{17}$")

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


class TaxInputCreate(TaxInputBase):
    """Create a masukan draft together with its Detail Transaksi rows.

    If source_invoice_id is set, header+lines are copied from the keluaran source
    (validated: same tenant, issued, readable). Lines may be omitted then (copied).
    """

    lines: list[TaxInputLineCreate] = Field(default_factory=list, max_length=100)


class TaxInputUpdate(BaseModel):
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

    lines: list[TaxInputLineCreate] | None = Field(default=None, max_length=100)


class TaxInputRead(BaseModel):
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

    credit_status: TaxInputCreditStatus
    credited_at: datetime | None
    source_invoice_id: int | None

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

    lines: list[TaxInputLineRead] = Field(default_factory=list)


class TaxInputListResponse(BaseModel):
    items: list[TaxInputRead]
    total: int
    page: int
    size: int


class TaxInputSummary(BaseModel):
    draft: int = 0
    credited: int = 0
    not_credited: int = 0
    invalid: int = 0
    total: int = 0


class TaxInputReview(BaseModel):
    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)


class TaxInputCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class TaxInputBulkCredit(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class TaxInputBulkCreditResult(BaseModel):
    credited: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class TaxInputBulkUncredit(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class TaxInputBulkUncreditResult(BaseModel):
    uncredited: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class TaxInputBulkDelete(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class TaxInputBulkDeleteResult(BaseModel):
    deleted: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
