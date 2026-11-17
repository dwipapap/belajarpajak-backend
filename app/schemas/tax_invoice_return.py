"""Schemas for Retur Pajak Masukan / Retur Pajak Keluaran.

Clients send only the returned quantity (and optional returned discount) per
original line; DPP, PPN and PPnBM of the return are derived server-side.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import BuyerIdentityType, TaxInvoiceLineType, TaxInvoiceStatus
from app.services.faktur_status import StatusFaktur


class TaxReturnLineInput(BaseModel):
    input_line_id: int
    return_quantity: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=3)
    return_discount: int = Field(default=0, ge=0)


class TaxReturnCreate(BaseModel):
    input_invoice_id: int
    return_number: str = Field(min_length=1, max_length=60)
    return_date: date
    #: Defaults to "yes" when the masukan faktur is credited.
    is_creditable: bool | None = None
    lines: list[TaxReturnLineInput] = Field(min_length=1)


class TaxReturnUpdate(BaseModel):
    return_number: str | None = Field(default=None, min_length=1, max_length=60)
    return_date: date | None = None
    is_creditable: bool | None = None
    lines: list[TaxReturnLineInput] | None = Field(default=None, min_length=1)


class TaxReturnIssue(BaseModel):
    signer_name: str | None = Field(default=None, max_length=150)
    kode_otorisasi: str | None = Field(default=None, max_length=100)


class TaxReturnLineRead(BaseModel):
    id: int
    input_line_id: int
    line_order: int

    # Original line, for the "Detail Transaksi" grid.
    line_type: TaxInvoiceLineType
    item_code: str | None
    item_name: str
    unit: str
    unit_price: int
    original_quantity: float
    original_discount: int
    ppn_rate_percent: float
    ppnbm_rate_percent: float
    use_dpp_other: bool
    #: Quantity already covered by other issued returns of the same line.
    already_returned_quantity: float
    max_return_quantity: float

    return_quantity: float
    return_discount: int
    return_total_price: int
    dpp: int
    dpp_other: int
    ppn: int
    ppnbm: int


class TaxReturnRead(BaseModel):
    id: int
    tenant_id: int
    class_id: int | None
    siswa_id: int
    created_by_id: int
    input_invoice_id: int

    status: TaxInvoiceStatus
    status_faktur: StatusFaktur
    return_number: str
    return_date: date
    tax_month: int
    tax_year: int
    is_creditable: bool
    issued_at: datetime | None
    electronic_signature_status: str
    signer_name: str | None

    # Original masukan faktur
    invoice_number: str | None
    invoice_date: date
    transaction_code: str
    seller_npwp: str | None
    seller_name: str | None
    buyer_identity_type: BuyerIdentityType
    buyer_identity_number: str
    buyer_name: str
    credit_status: str

    total_dpp: int
    total_dpp_other: int
    total_ppn: int
    total_ppnbm: int

    lines: list[TaxReturnLineRead]


class TaxReturnListResponse(BaseModel):
    items: list[TaxReturnRead]
    total: int
    page: int
    size: int
