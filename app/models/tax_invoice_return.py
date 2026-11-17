"""Retur Pajak Masukan — a buyer-side return of (part of) a masukan faktur.

One header (Dokumen Transaksi) owns one row per returned Detail Transaksi.
Amounts are recomputed from the returned quantity on every mutation, never
written by clients. The seller sees an issued retur as Retur Pajak Keluaran
through the masukan's ``source_invoice_id`` (no separate keluaran-side row).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import TaxInvoiceStatus


class TaxInvoiceReturn(TimestampMixin, table=True):
    __tablename__ = "tax_invoice_returns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "siswa_id", "return_number", name="uq_tax_returns_number"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    class_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("classes.id", ondelete="SET NULL"), nullable=True, index=True),
    )
    #: The returning buyer (owner of the masukan faktur).
    siswa_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    created_by_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    )
    input_invoice_id: int = Field(
        sa_column=Column(
            ForeignKey("tax_input_invoices.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )

    status: TaxInvoiceStatus = Field(
        default=TaxInvoiceStatus.draft,
        sa_column=Column(
            SAEnum(TaxInvoiceStatus, name="tax_invoice_status", create_type=False),
            nullable=False,
            index=True,
        ),
    )
    return_number: str = Field(max_length=60, index=True)
    return_date: date
    #: Reporting period, derived from the return date.
    tax_month: int = Field(ge=1, le=12, index=True)
    tax_year: int = Field(ge=2020, le=2100, index=True)
    is_creditable: bool = Field(default=True)

    issued_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    electronic_signature_status: str = Field(default="not_signed", max_length=40)
    signer_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
    )
    signer_name: str | None = Field(default=None, max_length=150)

    total_dpp: int = Field(default=0, ge=0, sa_type=BigInteger)
    total_dpp_other: int = Field(default=0, ge=0, sa_type=BigInteger)
    total_ppn: int = Field(default=0, ge=0, sa_type=BigInteger)
    total_ppnbm: int = Field(default=0, ge=0, sa_type=BigInteger)


class TaxInvoiceReturnLine(TimestampMixin, table=True):
    __tablename__ = "tax_invoice_return_lines"

    id: int | None = Field(default=None, primary_key=True)
    return_id: int = Field(
        sa_column=Column(
            ForeignKey("tax_invoice_returns.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    input_line_id: int = Field(
        sa_column=Column(
            ForeignKey("tax_input_lines.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    line_order: int = Field(default=0)

    return_quantity_milli: int = Field(default=0, ge=0, sa_type=BigInteger)
    return_discount: int = Field(default=0, ge=0, sa_type=BigInteger)
    return_total_price: int = Field(default=0, ge=0, sa_type=BigInteger)
    dpp: int = Field(default=0, ge=0, sa_type=BigInteger)
    dpp_other: int = Field(default=0, ge=0, sa_type=BigInteger)
    ppn: int = Field(default=0, ge=0, sa_type=BigInteger)
    ppnbm: int = Field(default=0, ge=0, sa_type=BigInteger)
