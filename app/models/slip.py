"""Simulated e-Bupot withholding slip model — one table for all form types.

BP21 (PPh 21, domestic recipients) and BP26 (PPh 26, foreign recipients) share
the same simulated slip structure and lifecycle; they differ only in
data values (recipient identity, rates, final vs non-final). ``slip_type`` is a
plain discriminator — every column is usable by every type.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import SlipSptFlag, SlipStatus, SlipTaxFacility, SlipTaxNature, SlipType


class WithholdingSlip(TimestampMixin, table=True):
    __tablename__ = "withholding_slips"

    id: int | None = Field(default=None, primary_key=True)
    slip_type: SlipType = Field(
        sa_column=Column(SAEnum(SlipType, name="slip_type"), nullable=False, index=True)
    )
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

    status: SlipStatus = Field(
        default=SlipStatus.draft,
        sa_column=Column(SAEnum(SlipStatus, name="slip_status"), nullable=False, index=True),
    )
    withholding_number: str | None = Field(default=None, max_length=40, unique=True, index=True)
    issued_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    invalid_reason: str | None = Field(default=None, max_length=500)
    spt_flag: SlipSptFlag | None = Field(
        default=None,
        sa_column=Column(SAEnum(SlipSptFlag, name="slip_spt_flag"), nullable=True, index=True),
    )

    tax_month: int = Field(ge=1, le=12, index=True)
    tax_year: int = Field(ge=2020, le=2100, index=True)
    electronic_signature_status: str = Field(default="not_signed", max_length=40)

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
    tax_nature: SlipTaxNature = Field(
        sa_column=Column(SAEnum(SlipTaxNature, name="slip_tax_nature"), nullable=False)
    )
    tax_facility: SlipTaxFacility = Field(
        default=SlipTaxFacility.none,
        sa_column=Column(SAEnum(SlipTaxFacility, name="slip_tax_facility"), nullable=False),
    )

    previous_gross_income: int = Field(default=0, ge=0)
    gross_income: int = Field(default=0, ge=0)
    dpp: int = Field(default=0, ge=0)
    dpp_rate_basis_points: int = Field(default=10000, ge=0)  # 10000 = 100.00%
    rate_basis_points: int = Field(default=0, ge=0)  # 500 = 5.00%
    income_tax: int = Field(default=0, ge=0)
    kap_kjs: str | None = Field(default=None, max_length=20)
    negara_treaty: str | None = Field(default=None, max_length=5)
    pasal_treaty: str | None = Field(default=None, max_length=20)
    nomor_skd: str | None = Field(default=None, max_length=100)
    tarif_treaty_basis_points: int | None = Field(default=None, ge=0, le=10000)

    document_type: str | None = Field(default=None, max_length=60)
    document_number: str | None = Field(default=None, max_length=60)
    document_date: date | None = Field(default=None)
    document_nitku: str | None = Field(default=None, max_length=32)

    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)
