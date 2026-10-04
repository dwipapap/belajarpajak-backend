"""Simulated SPT Masa — tax return header plus its aggregated detail lines.

One header row per tenant per form type per period. A pembetulan does not
create a new row (unlike bupot) — it bumps ``correction_no`` on the same row and
resets it to draft, so the overpayment-carryover chain stays a simple query.

Header amounts are never written by clients; the posting service recomputes
them from faktur/slip sources every time. ``tax_return_lines`` snapshots the
aggregation per Bagian/KJS so a reported SPT keeps its shape even if the
underlying documents are later edited.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import (
    SignerCapacity,
    TaxReturnKind,
    TaxReturnStatus,
    TaxReturnType,
)


class TaxReturn(TimestampMixin, table=True):
    __tablename__ = "tax_returns"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "siswa_id",
            "return_type",
            "tax_year",
            "tax_month",
            name="uq_spt_tenant_siswa_type_period",
        ),
    )

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

    return_type: TaxReturnType = Field(
        sa_column=Column(SAEnum(TaxReturnType, name="tax_return_type"), nullable=False, index=True)
    )
    kind: TaxReturnKind = Field(
        default=TaxReturnKind.normal,
        sa_column=Column(SAEnum(TaxReturnKind, name="tax_return_kind"), nullable=False),
    )
    #: 0 = normal; n = pembetulan ke-n. Same-row correction counter.
    correction_no: int = Field(default=0, ge=0)
    status: TaxReturnStatus = Field(
        default=TaxReturnStatus.draft,
        sa_column=Column(
            SAEnum(TaxReturnStatus, name="tax_return_status"), nullable=False, index=True
        ),
    )
    tax_month: int = Field(ge=1, le=12, index=True)
    tax_year: int = Field(ge=2020, le=2100, index=True)

    # --- Pernyataan (statement) ---
    signer_npwp: str | None = Field(default=None, max_length=32)
    signer_name: str | None = Field(default=None, max_length=150)
    signer_capacity: SignerCapacity = Field(
        default=SignerCapacity.wajib_pajak,
        sa_column=Column(SAEnum(SignerCapacity, name="signer_capacity"), nullable=False),
    )
    submitted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    submitted_by_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )

    # --- Bagian I (PPN penyerahan), aggregated from issued faktur keluaran ---
    penyerahan_dpp: int = Field(default=0, ge=0, sa_type=BigInteger)
    penyerahan_dpp_nilai_lain: int = Field(default=0, ge=0, sa_type=BigInteger)
    penyerahan_ppn: int = Field(default=0, ge=0, sa_type=BigInteger)
    penyerahan_ppnbm: int = Field(default=0, ge=0, sa_type=BigInteger)

    # --- Bagian II (PPN masukan), aggregated from credited faktur masukan ---
    masukan_dpp: int = Field(default=0, ge=0, sa_type=BigInteger)
    masukan_dpp_nilai_lain: int = Field(default=0, ge=0, sa_type=BigInteger)
    masukan_ppn: int = Field(default=0, ge=0, sa_type=BigInteger)
    #: II.E — carryover overpayment from earlier reported SPT PPN periods.
    kompensasi_lebih_bayar: int = Field(default=0, ge=0, sa_type=BigInteger)

    # --- Bagian III (PPN) / Induk B (PPh) — exactly one of the two is nonzero ---
    ppn_kurang_bayar: int = Field(default=0, ge=0, sa_type=BigInteger)
    ppn_lebih_bayar: int = Field(default=0, ge=0, sa_type=BigInteger)
    total_income_tax: int = Field(default=0, ge=0, sa_type=BigInteger)

    #: Value the billing is issued for: PPN kurang bayar, or the PPh total.
    amount_due: int = Field(default=0, ge=0, sa_type=BigInteger)

    score: int | None = Field(default=None, ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)


class TaxReturnLine(TimestampMixin, table=True):
    """One aggregated row of a posted SPT.

    ``section`` encodes the form region — PPN: ``I_A1|I_A2|I_B1|I_B2|I_B3|I_C``;
    PPh unifikasi: ``pasal_4_2|pasal_15|pasal_22|pasal_23|pasal_26``.
    ``code`` is the Faktur Pajak kode for PPN rows (e.g. ``04``) or the full
    KAP-KJS for PPh rows (e.g. ``411124-100``).
    """

    __tablename__ = "tax_return_lines"

    id: int | None = Field(default=None, primary_key=True)
    return_id: int = Field(
        sa_column=Column(
            ForeignKey("tax_returns.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    section: str = Field(max_length=20, index=True)
    code: str = Field(max_length=20)
    description: str = Field(max_length=255)

    # --- PPN rows ---
    dpp: int = Field(default=0, ge=0, sa_type=BigInteger)
    dpp_nilai_lain: int = Field(default=0, ge=0, sa_type=BigInteger)
    ppn: int = Field(default=0, ge=0, sa_type=BigInteger)
    ppnbm: int = Field(default=0, ge=0, sa_type=BigInteger)

    # --- PPh rows ---
    setor_sendiri: int = Field(default=0, ge=0, sa_type=BigInteger)
    pemotongan: int = Field(default=0, ge=0, sa_type=BigInteger)
    ditanggung_pemerintah: int = Field(default=0, ge=0, sa_type=BigInteger)
    dibayar_dari_spt_dibetulkan: int = Field(default=0, ge=0, sa_type=BigInteger)
