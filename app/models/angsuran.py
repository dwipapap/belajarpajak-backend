"""Angsuran billing — cicilan 2–12 kali atas satu billing yang belum dibayar.

Hanya instant-approval: pengajuan langsung berstatus ``berjalan``. Aturan
lunas-sekaligus TIDAK berlaku di sini (angsuran memang untuk mencicil), tetapi
deposit tidak bisa dipakai mencicil — angsuran selalu dibayar per cicilan via
``POST /cicilan/{id}/pay``.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Column, Date, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin


class AngsuranStatus(StrEnum):
    berjalan = "berjalan"
    lunas = "lunas"
    batal = "batal"


class CicilanStatus(StrEnum):
    belum = "belum"
    lunas = "lunas"


class Angsuran(TimestampMixin, table=True):
    __tablename__ = "angsuran"

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    user_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    billing_id: int = Field(
        sa_column=Column(
            ForeignKey("billing_codes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        )
    )
    n_cicilan: int = Field(ge=2, le=12)
    status: AngsuranStatus = Field(
        default=AngsuranStatus.berjalan,
        sa_column=Column(
            SAEnum(AngsuranStatus, name="angsuran_status"), nullable=False, index=True
        ),
    )


class Cicilan(TimestampMixin, table=True):
    __tablename__ = "cicilan"

    id: int | None = Field(default=None, primary_key=True)
    angsuran_id: int = Field(
        sa_column=Column(
            ForeignKey("angsuran.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    ke: int = Field(ge=1)
    jatuh_tempo: date = Field(sa_type=Date)
    amount: int = Field(ge=1, sa_type=BigInteger)
    status: CicilanStatus = Field(
        default=CicilanStatus.belum,
        sa_column=Column(SAEnum(CicilanStatus, name="cicilan_status"), nullable=False, index=True),
    )
    paid_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    kanal: str | None = Field(default=None, max_length=40)
