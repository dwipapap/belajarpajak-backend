"""Dompet deposit siswa — saldo prabayar untuk melunasi SPT sekaligus lunas.

Aturan lunas-sekaligus: deposit tidak mengenal cicilan; satu SPT dibayar penuh
atau tidak sama sekali. Saldo hanya bertambah dari sinkronisasi billing mandiri
KAP ``411618-100`` (kode setor deposit) yang sudah paid — lihat
``app/routers/deposit.py::sinkronisasi_deposit``.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import BigInteger, Column, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin


class DepositMutationKind(StrEnum):
    topup = "topup"
    pakai = "pakai"


class DepositAccount(TimestampMixin, table=True):
    __tablename__ = "deposit_accounts"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
        )
    )
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True),
    )
    saldo: int = Field(default=0, ge=0, sa_type=BigInteger)


class DepositMutation(TimestampMixin, table=True):
    """Riwayat dompet. Idempoten via (account_id, ref_type, ref_id).

    ``ref_type='billing'`` menandai top-up hasil sinkronisasi; ``ref_type='spt'``
    menandai pemakaian untuk bayar-dan-lapor via deposit.
    """

    __tablename__ = "deposit_mutations"
    __table_args__ = (
        UniqueConstraint("account_id", "ref_type", "ref_id", name="uq_deposit_mutation_ref"),
    )

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(
        sa_column=Column(
            ForeignKey("deposit_accounts.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    jenis: DepositMutationKind = Field(
        sa_column=Column(SAEnum(DepositMutationKind, name="deposit_mutation_kind"), nullable=False)
    )
    amount: int = Field(ge=1, sa_type=BigInteger)
    ref_type: str | None = Field(default=None, max_length=20)
    ref_id: int | None = Field(default=None)
    note: str | None = Field(default=None, max_length=200)
