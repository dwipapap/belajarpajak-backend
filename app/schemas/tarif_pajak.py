"""Schemas for PTKP and progressive tariff CRUD."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TierPtkpCreate(BaseModel):
    status_kode: str = Field(max_length=10)
    jumlah_ptkp: int = Field(ge=0)
    tahun_pajak: int = Field(ge=2020, le=2100)
    keterangan: str | None = Field(default=None, max_length=100)
    is_active: bool = True


class TierPtkpUpdate(BaseModel):
    status_kode: str | None = Field(default=None, max_length=10)
    jumlah_ptkp: int | None = Field(default=None, ge=0)
    tahun_pajak: int | None = Field(default=None, ge=2020, le=2100)
    keterangan: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class TierPtkpRead(BaseModel):
    id: int
    status_kode: str
    jumlah_ptkp: int
    tahun_pajak: int
    keterangan: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class TarifProgresifCreate(BaseModel):
    batas_bawah: int = Field(ge=0)
    batas_atas: int | None = Field(default=None, ge=0)
    persentase_basis_points: int = Field(ge=0, le=10000)
    tahun_pajak: int = Field(ge=2020, le=2100)
    keterangan: str | None = Field(default=None, max_length=100)
    is_active: bool = True


class TarifProgresifUpdate(BaseModel):
    batas_bawah: int | None = Field(default=None, ge=0)
    batas_atas: int | None = Field(default=None, ge=0)
    persentase_basis_points: int | None = Field(default=None, ge=0, le=10000)
    tahun_pajak: int | None = Field(default=None, ge=2020, le=2100)
    keterangan: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class TarifProgresifRead(BaseModel):
    id: int
    batas_bawah: int
    batas_atas: int | None
    persentase_basis_points: int
    tahun_pajak: int
    keterangan: str | None
    is_active: bool

    model_config = {"from_attributes": True}
