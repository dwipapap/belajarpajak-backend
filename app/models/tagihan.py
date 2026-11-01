"""Tagihan SKP/STP — ketetapan yang diterbitkan guru/admin untuk siswa.

Alur: guru/admin menerbitkan tagihan (``belum``) → ``buat-billing`` menerbitkan
satu ``BillingCode`` unpaid dan memarkir tagihan ke ``dibilling`` → siswa membayar
billing lewat endpoint pay yang sudah ada → frontend memanggil
``lunaskan-dari-billing`` yang memverifikasi billing tersebut paid lalu menandai
tagihan ``lunas``.

Catatan kontrak: billing diasumsikan punya ``tagihan_id``; karena tabel billing
milik jalur lain dan tidak boleh diubah, relasi disimpan dari sisi sini lewat
``Tagihan.billing_id``.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import BigInteger, Column, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin


class TagihanJenis(StrEnum):
    skp = "skp"
    stp = "stp"


class TagihanStatus(StrEnum):
    belum = "belum"
    dibilling = "dibilling"
    lunas = "lunas"


class Tagihan(TimestampMixin, table=True):
    __tablename__ = "tagihan"

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    user_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    jenis: TagihanJenis = Field(
        sa_column=Column(SAEnum(TagihanJenis, name="tagihan_jenis"), nullable=False)
    )
    kap_kjs: str = Field(max_length=20)
    masa: str = Field(max_length=6)  # MMYYYY
    amount: int = Field(ge=1, sa_type=BigInteger)
    status: TagihanStatus = Field(
        default=TagihanStatus.belum,
        sa_column=Column(SAEnum(TagihanStatus, name="tagihan_status"), nullable=False, index=True),
    )
    billing_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("billing_codes.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
            index=True,
        ),
    )
