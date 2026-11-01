"""Pbk (pemindahbukuan) — pencatatan persetujuan antar-KAP, tanpa gerakan saldo.

Simplifikasi simulasi: Pbk di sini murni alur persetujuan
(diajukan → disetujui/ditolak). Tidak ada mutasi ``DepositMutation`` yang
dicatat saat persetujuan karena tidak ada pergerakan dana nyata.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import BigInteger, Column, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin


class PbkStatus(StrEnum):
    diajukan = "diajukan"
    disetujui = "disetujui"
    ditolak = "ditolak"


class PbkRequest(TimestampMixin, table=True):
    __tablename__ = "pbk_requests"

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    user_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    dari_kap: str = Field(max_length=20)
    ke_kap: str = Field(max_length=20)
    amount: int = Field(ge=1, sa_type=BigInteger)
    masa: str = Field(max_length=6)  # MMYYYY
    alasan: str = Field(max_length=500)
    status: PbkStatus = Field(
        default=PbkStatus.diajukan,
        sa_column=Column(SAEnum(PbkStatus, name="pbk_status"), nullable=False, index=True),
    )
    decided_by_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
