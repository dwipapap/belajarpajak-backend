"""Simulated e-Faktur Pajak Keluaran — invoice header plus its transaction lines.

Unlike e-Bupot slips, a faktur is not a single flat row: one header (Dokumen
Transaksi + Informasi Pembeli) owns many Detail Transaksi lines, and the tax is
PPN/PPnBM rather than PPh. Hence its own tables. Everything else follows the
slip conventions — whole-rupiah integers, basis-point rates, and the same
tenant/class/siswa ownership columns.

Header totals are never written by clients; they are recomputed from the lines
on every mutation so the two can't drift.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import (
    BuyerIdentityType,
    TaxInvoiceKind,
    TaxInvoiceLineType,
    TaxInvoiceStatus,
)


class TaxInvoice(TimestampMixin, table=True):
    __tablename__ = "tax_invoices"
    # Nomor Faktur serials run per taxpayer, so uniqueness is per tenant — two
    # tenants legitimately both start at serial 1.
    __table_args__ = (
        UniqueConstraint("tenant_id", "invoice_number", name="uq_tax_invoices_tenant_number"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    class_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("classes.id", ondelete="SET NULL"), nullable=True, index=True),
    )
    siswa_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    created_by_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    )

    status: TaxInvoiceStatus = Field(
        default=TaxInvoiceStatus.draft,
        sa_column=Column(
            SAEnum(TaxInvoiceStatus, name="tax_invoice_status"), nullable=False, index=True
        ),
    )
    invoice_number: str | None = Field(default=None, max_length=24, index=True)
    issued_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    invalid_reason: str | None = Field(default=None, max_length=500)
    electronic_signature_status: str = Field(default="not_signed", max_length=40)

    # --- Dokumen Transaksi ---
    is_advance_payment: bool = Field(default=False)  # Uang Muka
    is_settlement: bool = Field(default=False)  # Pelunasan
    transaction_code: str = Field(max_length=2, index=True)  # 01..10
    invoice_date: date
    invoice_kind: TaxInvoiceKind = Field(
        default=TaxInvoiceKind.normal,
        sa_column=Column(SAEnum(TaxInvoiceKind, name="tax_invoice_kind"), nullable=False),
    )
    tax_month: int = Field(ge=1, le=12, index=True)
    tax_year: int = Field(ge=2020, le=2100, index=True)
    reference: str | None = Field(default=None, max_length=255)

    # --- Penjual ---
    seller_npwp: str | None = Field(default=None, max_length=32)
    seller_name: str | None = Field(default=None, max_length=150)
    seller_address: str | None = Field(default=None, max_length=255)
    seller_idtku: str | None = Field(default=None, max_length=32)

    # --- Informasi Pembeli ---
    buyer_identity_type: BuyerIdentityType = Field(
        default=BuyerIdentityType.npwp,
        sa_column=Column(SAEnum(BuyerIdentityType, name="buyer_identity_type"), nullable=False),
    )
    buyer_identity_number: str = Field(max_length=32)
    buyer_country: str = Field(default="ID", max_length=2)
    buyer_document_number: str | None = Field(default=None, max_length=60)
    buyer_name: str = Field(max_length=150)
    buyer_address: str | None = Field(default=None, max_length=255)
    buyer_idtku: str | None = Field(default=None, max_length=32)
    buyer_email: str | None = Field(default=None, max_length=150)

    # --- Totals, recomputed from the lines (BigInteger: an invoice sums many lines) ---
    total_price: int = Field(default=0, ge=0, sa_type=BigInteger)
    total_discount: int = Field(default=0, ge=0, sa_type=BigInteger)
    total_dpp: int = Field(default=0, ge=0, sa_type=BigInteger)
    total_dpp_other: int = Field(default=0, ge=0, sa_type=BigInteger)
    total_ppn: int = Field(default=0, ge=0, sa_type=BigInteger)
    total_ppnbm: int = Field(default=0, ge=0, sa_type=BigInteger)

    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)


class TaxInvoiceLine(TimestampMixin, table=True):
    """One Detail Transaksi row.

    ``quantity_milli`` is the quantity times 1000 — the same trick basis points
    play for rates, so fractional quantities (0.5 bulan) stay exact without a
    float ever touching a stored amount.
    """

    __tablename__ = "tax_invoice_lines"

    id: int | None = Field(default=None, primary_key=True)
    invoice_id: int = Field(
        sa_column=Column(
            ForeignKey("tax_invoices.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    line_order: int = Field(default=0)

    line_type: TaxInvoiceLineType = Field(
        default=TaxInvoiceLineType.barang,
        sa_column=Column(SAEnum(TaxInvoiceLineType, name="tax_invoice_line_type"), nullable=False),
    )
    item_code: str | None = Field(default=None, max_length=20)
    item_name: str = Field(max_length=255)
    unit: str = Field(max_length=40)

    unit_price: int = Field(default=0, ge=0, sa_type=BigInteger)
    quantity_milli: int = Field(default=1000, ge=0, sa_type=BigInteger)
    total_price: int = Field(default=0, ge=0, sa_type=BigInteger)
    discount: int = Field(default=0, ge=0, sa_type=BigInteger)

    dpp: int = Field(default=0, ge=0, sa_type=BigInteger)
    use_dpp_other: bool = Field(default=False)
    dpp_other: int = Field(default=0, ge=0, sa_type=BigInteger)
    ppn_rate_basis_points: int = Field(default=1200, ge=0, le=10000)  # 1200 = 12.00%
    ppn: int = Field(default=0, ge=0, sa_type=BigInteger)
    ppnbm_rate_basis_points: int = Field(default=0, ge=0, le=10000)
    ppnbm: int = Field(default=0, ge=0, sa_type=BigInteger)
