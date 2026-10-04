"""Simulated kode billing — DJP payment request for a kurang-bayar SPT.

A billing is created server-side when an SPT with ``amount_due > 0`` is
submitted ("bayar dan lapor"). The 15-digit code format mirrors the real PDF:
``{KAP-KJS digits:6}{MM:2}{YY:2}{serial:5}``, serial per tenant per year under a
transaction advisory lock — the same pattern NSFP generation uses.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import BillingStatus


class BillingCode(TimestampMixin, table=True):
    __tablename__ = "billing_codes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_billing_tenant_code"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    siswa_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    spt_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("tax_returns.id", ondelete="SET NULL"), nullable=True, index=True
        ),
    )

    code: str = Field(max_length=15, unique=True, index=True)
    status: BillingStatus = Field(
        default=BillingStatus.unpaid,
        sa_column=Column(SAEnum(BillingStatus, name="billing_status"), nullable=False, index=True),
    )
    amount: int = Field(default=0, ge=0, sa_type=BigInteger)
    #: KAP-KJS of the billed object (e.g. "411121-100"), or "-" for multi-KJS totals.
    kap_kjs: str = Field(default="-", max_length=20)
    masa_pajak: str = Field(max_length=6)  # MMYYYY
    paid_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
