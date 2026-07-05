"""Simulated e-Bupot BP21 withholding slip model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import Bp21Status, Bp21TaxFacility, Bp21TaxNature


class Bp21WithholdingSlip(TimestampMixin, table=True):
    """Draft/issued BP21 document for the learning simulator.

    BP21 represents a withholding slip for PPh 21 recipients other than permanent
    employees. The simulator keeps the workflow intentionally small but aligned to
    Coretax surfaces: draft/not issued, issued, and invalid.
    """

    __tablename__ = "bp21_withholding_slips"

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

    status: Bp21Status = Field(
        default=Bp21Status.draft,
        sa_column=Column(SAEnum(Bp21Status, name="bp21_status"), nullable=False, index=True),
    )
    withholding_number: str | None = Field(default=None, max_length=40, unique=True, index=True)
    issued_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    invalid_reason: str | None = Field(default=None, max_length=500)

    tax_month: int = Field(ge=1, le=12, index=True)
    tax_year: int = Field(ge=2020, le=2100, index=True)
    electronic_signature_status: str = Field(default="not_signed", max_length=40)

    withholder_npwp: str | None = Field(default=None, max_length=32)
    withholder_name: str | None = Field(default=None, max_length=150)
    withholder_nitku: str | None = Field(default=None, max_length=32)

    recipient_identity_number: str = Field(max_length=32)
    recipient_name: str = Field(max_length=150)
    recipient_address: str | None = Field(default=None, max_length=255)

    tax_object_code: str = Field(max_length=30)
    income_type: str = Field(max_length=150)
    tax_nature: Bp21TaxNature = Field(
        sa_column=Column(SAEnum(Bp21TaxNature, name="bp21_tax_nature"), nullable=False)
    )
    tax_facility: Bp21TaxFacility = Field(
        default=Bp21TaxFacility.none,
        sa_column=Column(SAEnum(Bp21TaxFacility, name="bp21_tax_facility"), nullable=False),
    )

    dpp: int = Field(default=0, ge=0)
    rate_basis_points: int = Field(default=0, ge=0)  # 500 = 5.00%
    income_tax: int = Field(default=0, ge=0)

    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)
