"""Schemas for the simulated SPT Masa workflow.

Clients never send derived amounts — posting computes Bagian I/II/III and the
Induk aggregates from faktur/slip sources, and submit decides kurang/lebih
bayar server-side. A draft only carries the period, the form type, and the
statement (pernyataan) fields.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    BillingStatus,
    SignerCapacity,
    TaxReturnKind,
    TaxReturnStatus,
    TaxReturnType,
)


class TaxReturnCreate(BaseModel):
    """Buat konsep SPT — exactly the wizard: jenis pajak → periode → jenis."""

    return_type: TaxReturnType
    tax_month: int = Field(ge=1, le=12)
    tax_year: int = Field(ge=2020, le=2100)
    kind: TaxReturnKind = TaxReturnKind.normal
    class_id: int | None = None
    siswa_id: int | None = None


class TaxReturnPernyataan(BaseModel):
    """Statement (pernyataan) data collected before Bayar dan Lapor."""

    signer_name: str = Field(min_length=1, max_length=150)
    signer_npwp: str | None = Field(default=None, max_length=32)
    signer_capacity: SignerCapacity = SignerCapacity.wajib_pajak

    @model_validator(mode="after")
    def _kuasa_requires_npwp(self) -> TaxReturnPernyataan:
        if self.signer_capacity == SignerCapacity.kuasa_wajib_pajak and not self.signer_npwp:
            raise ValueError("Kuasa wajib pajak wajib mencantumkan NPWP kuasa")
        return self


class TaxReturnSubmit(BaseModel):
    """Bayar dan Lapor — passphrase verifies the signer (pola e-sign)."""

    passphrase: str = Field(min_length=1, max_length=100)


class TaxReturnReview(BaseModel):
    score: int = Field(ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)


class TaxReturnLineRead(BaseModel):
    id: int
    return_id: int
    section: str
    code: str
    description: str
    dpp: int
    dpp_nilai_lain: int
    ppn: int
    ppnbm: int
    setor_sendiri: int
    pemotongan: int
    ditanggung_pemerintah: int
    dibayar_dari_spt_dibetulkan: int


class TaxReturnRead(BaseModel):
    id: int
    tenant_id: int
    class_id: int | None
    siswa_id: int
    created_by_id: int

    return_type: TaxReturnType
    kind: TaxReturnKind
    correction_no: int
    status: TaxReturnStatus
    tax_month: int
    tax_year: int

    signer_npwp: str | None
    signer_name: str | None
    signer_capacity: SignerCapacity
    submitted_at: datetime | None
    submitted_by_id: int | None

    penyerahan_dpp: int
    penyerahan_dpp_nilai_lain: int
    penyerahan_ppn: int
    penyerahan_ppnbm: int
    masukan_dpp: int
    masukan_dpp_nilai_lain: int
    masukan_ppn: int
    kompensasi_lebih_bayar: int
    ppn_kurang_bayar: int
    ppn_lebih_bayar: int
    total_income_tax: int
    amount_due: int

    score: int | None
    teacher_feedback: str | None
    created_at: datetime | None
    updated_at: datetime | None

    lines: list[TaxReturnLineRead] = Field(default_factory=list)


class TaxReturnListResponse(BaseModel):
    items: list[TaxReturnRead]
    total: int


class TaxReturnSummary(BaseModel):
    """Hitung dokumen per bucket status — pola /summary modul lain."""

    draft: int = 0
    awaiting_payment: int = 0
    reported: int = 0
    total: int = 0


class BillingRead(BaseModel):
    id: int
    tenant_id: int
    siswa_id: int
    spt_id: int | None
    code: str
    status: BillingStatus
    amount: int
    kap_kjs: str
    masa_pajak: str
    uraian: str | None
    paid_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class BillingCreate(BaseModel):
    """Layanan Mandiri Kode Billing — one code, one KAP-KJS, one period, one amount.

    ``spt_id`` is never accepted here: SPT billing is minted only by submit.
    Siswa creates for themselves; admin/guru may specify a same-tenant siswa.
    """

    kap_kjs: str = Field(max_length=20)
    masa_pajak: str = Field(max_length=6)
    amount: int = Field(ge=1, le=9_999_999_999)
    uraian: str | None = Field(default=None, max_length=200)
    siswa_id: int | None = None


class BillingListResponse(BaseModel):
    items: list[BillingRead]
    total: int
