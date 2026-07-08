"""PTKP tiers and progressive income tax brackets."""

from __future__ import annotations

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class TierPtkp(SQLModel, table=True):
    __tablename__ = "tarif_ptkp"
    __table_args__ = (UniqueConstraint("status_kode", "tahun_pajak"),)

    id: int | None = Field(default=None, primary_key=True)
    status_kode: str = Field(max_length=10, index=True)
    jumlah_ptkp: int = Field(ge=0)
    tahun_pajak: int = Field(index=True)
    keterangan: str | None = Field(default=None, max_length=100)
    is_active: bool = Field(default=True)


class TarifProgresifPasal17(SQLModel, table=True):
    __tablename__ = "tarif_progresif"

    id: int | None = Field(default=None, primary_key=True)
    batas_bawah: int = Field(ge=0)
    batas_atas: int | None = Field(default=None, ge=0)
    persentase_basis_points: int = Field(ge=0, le=10000)
    tahun_pajak: int = Field(index=True)
    keterangan: str | None = Field(default=None, max_length=100)
    is_active: bool = Field(default=True)
