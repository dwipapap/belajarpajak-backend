"""Simulated e-Bupot BP26 withholding slip model."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import Bp26SptFlag, Bp26Status, Bp26TaxFacility, Bp26TaxNature


class Bp26WithholdingSlip(TimestampMixin, table=True):
    """Draft/issued BP26 document for the learning simulator.

    BP26 represents a withholding slip for PPh 26 (income paid to foreign tax
    subjects). The workflow covers: Belum Terbit (draft),
    Telah Terbit (issued), and Tidak Valid (invalid). Issued slips additionally
    carry SPT/objection lifecycle flags shown in the 'Telah Terbit' tab.
    """

    __tablename__ = "bp26_withholding_slips"

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

    status: Bp26Status = Field(
        default=Bp26Status.draft,
        sa_column=Column(SAEnum(Bp26Status, name="bp26_status"), nullable=False, index=True),
    )
    withholding_number: str | None = Field(default=None, max_length=40, unique=True, index=True)
    issued_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    invalid_reason: str | None = Field(default=None, max_length=500)
    spt_flag: Bp26SptFlag | None = Field(
        default=None,
        sa_column=Column(SAEnum(Bp26SptFlag, name="bp26_spt_flag"), nullable=True, index=True),
    )

    tax_month: int = Field(ge=1, le=12, index=True)
    tax_year: int = Field(ge=2020, le=2100, index=True)
    electronic_signature_status: str = Field(default="not_signed", max_length=40)

    recipient_identity_number: str = Field(max_length=32)
    recipient_name: str = Field(max_length=150)

    tax_type: str = Field(default="PPh 26", max_length=30)
    tax_object_code: str = Field(max_length=30)
    tax_object_name: str = Field(max_length=150)
    tax_nature: Bp26TaxNature = Field(
        sa_column=Column(SAEnum(Bp26TaxNature, name="bp26_tax_nature"), nullable=False)
    )
    tax_facility: Bp26TaxFacility = Field(
        default=Bp26TaxFacility.none,
        sa_column=Column(SAEnum(Bp26TaxFacility, name="bp26_tax_facility"), nullable=False),
    )

    gross_income: int = Field(default=0, ge=0)
    dpp: int = Field(default=0, ge=0)
    dpp_rate_basis_points: int = Field(default=10000, ge=0)  # 10000 = 100.00%
    rate_basis_points: int = Field(default=0, ge=0)  # 2000 = 20.00%
    income_tax: int = Field(default=0, ge=0)
    kap_kjs: str | None = Field(default=None, max_length=20)

    document_type: str | None = Field(default=None, max_length=60)
    document_number: str | None = Field(default=None, max_length=60)
    document_date: date | None = Field(default=None)
    document_nitku: str | None = Field(default=None, max_length=32)

    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)
