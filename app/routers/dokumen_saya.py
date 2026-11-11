"""Portal Saya → Dokumen Saya: one download list for the logged-in siswa.

Collects every output document the system produced for the student —
reported-SPT receipts (BPE), issued withholding slips, and billing codes —
without leaking other students' rows (siswa_id is always the caller).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.billing import BillingCode
from app.models.enums import Role, SlipStatus, TaxReturnStatus
from app.models.slip import WithholdingSlip
from app.models.tax_return import TaxReturn
from app.schemas.dokumen_saya import DokumenSayaListResponse, DokumenSayaRow

LABEL = "Dokumen Saya"

router = APIRouter(prefix="/dokumen-saya", tags=["dokumen-saya"])

_roles = Depends(require_roles(Role.siswa))

_JENIS = ("bpe_spt", "bukti_potong", "kode_billing")


@router.get("", response_model=DokumenSayaListResponse, dependencies=[_roles])
def list_dokumen_saya(
    current_user: CurrentUser,
    session: SessionDep,
    jenis: Annotated[str | None, Query(description="bpe_spt | bukti_potong | kode_billing")] = None,
    tahun: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DokumenSayaListResponse:
    if jenis is not None and jenis not in _JENIS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="jenis harus salah satu dari: bpe_spt, bukti_potong, kode_billing",
        )
    rows: list[DokumenSayaRow] = []

    if jenis is None or jenis == "bpe_spt":
        query = select(TaxReturn).where(
            TaxReturn.siswa_id == current_user.id,
            TaxReturn.status == TaxReturnStatus.reported,
        )
        if tahun is not None:
            query = query.where(TaxReturn.tax_year == tahun)
        for spt in session.exec(query.order_by(TaxReturn.id.desc())).all():
            rows.append(
                DokumenSayaRow(
                    jenis="bpe_spt",
                    nomor=spt.bpe_number or "-",
                    tanggal=spt.received_at,
                    uraian=(
                        f"BPE SPT Masa {spt.return_type.value} "
                        f"{spt.tax_month:02d}/{spt.tax_year}"
                    ),
                    masa=f"{spt.tax_month:02d}/{spt.tax_year}",
                    download_path=f"/api/v1/spt/{spt.id}/bpe",
                )
            )

    if jenis is None or jenis == "bukti_potong":
        query = select(WithholdingSlip).where(
            WithholdingSlip.siswa_id == current_user.id,
            WithholdingSlip.status == SlipStatus.issued,
        )
        if tahun is not None:
            query = query.where(WithholdingSlip.tax_year == tahun)
        for slip in session.exec(query.order_by(WithholdingSlip.id.desc())).all():
            label = slip.slip_type.value.upper()
            rows.append(
                DokumenSayaRow(
                    jenis="bukti_potong",
                    nomor=slip.withholding_number or f"{label}-{slip.id}",
                    tanggal=slip.updated_at,
                    uraian=f"{label} — {slip.recipient_name or 'tanpa lawan transaksi'}",
                    masa=f"{slip.tax_month:02d}/{slip.tax_year}",
                    download_path=f"/api/v1/{slip.slip_type.value}/{slip.id}/pdf",
                )
            )

    if jenis is None or jenis == "kode_billing":
        query = select(BillingCode).where(BillingCode.siswa_id == current_user.id)
        for billing in session.exec(query.order_by(BillingCode.id.desc())).all():
            if tahun is not None and not billing.masa_pajak.endswith(str(tahun)):
                continue
            uraian = f"Kode billing {billing.code}"
            if billing.ntpn:
                uraian += f" — NTPN {billing.ntpn}"
            rows.append(
                DokumenSayaRow(
                    jenis="kode_billing",
                    nomor=billing.code,
                    tanggal=billing.paid_at or billing.created_at,
                    uraian=uraian,
                    masa=billing.masa_pajak,
                    download_path=f"/api/v1/billings/{billing.id}/pdf",
                )
            )

    # Newest first; rows carry datetimes from different tables, so sort on ISO text.
    rows.sort(key=lambda row: row.tanggal.isoformat() if row.tanggal else "", reverse=True)
    total = len(rows)
    return DokumenSayaListResponse(items=rows[(page - 1) * size : page * size], total=total)
