"""Portal Saya → Dokumen Saya: one download list for the logged-in siswa.

Collects every output document the system produced for the student —
SPT documents and their receipts (BPE), issued withholding slips, issued
output invoices (faktur keluaran), and billing codes —
without leaking other students' rows (siswa_id is always the caller).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.billing import BillingCode
from app.models.enums import Role, SlipStatus, TaxInvoiceStatus, TaxReturnStatus
from app.models.slip import WithholdingSlip
from app.models.tax_invoice import TaxInvoice
from app.models.tax_return import TaxReturn
from app.schemas.dokumen_saya import DokumenSayaListResponse, DokumenSayaRow

LABEL = "Dokumen Saya"

router = APIRouter(prefix="/dokumen-saya", tags=["dokumen-saya"])

_roles = Depends(require_roles(Role.siswa))

_JENIS = ("spt", "bpe_spt", "bukti_potong", "faktur", "kode_billing")
_JENIS_TEXT = " | ".join(_JENIS)

_SPT_LABEL = {
    "ppn": "PPN",
    "pph_unifikasi": "PPh Unifikasi",
    "pph_21_26": "PPh 21/26",
    "pph21_26": "PPh 21/26",
}


def _spt_label(spt: TaxReturn) -> str:
    return _SPT_LABEL.get(spt.return_type.value, spt.return_type.value)


# Statuses whose SPT has been posted, so the SPT document PDF has content.
_SPT_READY = (TaxReturnStatus.awaiting_payment, TaxReturnStatus.reported)


@router.get("", response_model=DokumenSayaListResponse, dependencies=[_roles])
def list_dokumen_saya(
    current_user: CurrentUser,
    session: SessionDep,
    jenis: Annotated[str | None, Query(description=_JENIS_TEXT)] = None,
    tahun: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DokumenSayaListResponse:
    if jenis is not None and jenis not in _JENIS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"jenis harus salah satu dari: {', '.join(_JENIS)}",
        )
    rows: list[DokumenSayaRow] = []

    if jenis is None or jenis == "spt":
        query = select(TaxReturn).where(
            TaxReturn.siswa_id == current_user.id,
            TaxReturn.status.in_(_SPT_READY),
        )
        if tahun is not None:
            query = query.where(TaxReturn.tax_year == tahun)
        for spt in session.exec(query.order_by(TaxReturn.id.desc())).all():
            rows.append(
                DokumenSayaRow(
                    jenis="spt",
                    nomor=f"SPT-{spt.id}",
                    tanggal=spt.submitted_at or spt.updated_at,
                    uraian=(f"SPT Masa {_spt_label(spt)} {spt.tax_month:02d}/{spt.tax_year}"),
                    masa=f"{spt.tax_month:02d}/{spt.tax_year}",
                    download_path=f"/api/v1/spt/{spt.id}/pdf",
                )
            )

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
                    uraian=(f"BPE SPT Masa {_spt_label(spt)} {spt.tax_month:02d}/{spt.tax_year}"),
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

    if jenis is None or jenis == "faktur":
        query = select(TaxInvoice).where(
            TaxInvoice.siswa_id == current_user.id,
            TaxInvoice.status == TaxInvoiceStatus.issued,
        )
        if tahun is not None:
            query = query.where(TaxInvoice.tax_year == tahun)
        for invoice in session.exec(query.order_by(TaxInvoice.id.desc())).all():
            rows.append(
                DokumenSayaRow(
                    jenis="faktur",
                    nomor=invoice.invoice_number or f"FAKTUR-{invoice.id}",
                    tanggal=invoice.issued_at or invoice.updated_at,
                    uraian=f"Faktur Pajak Keluaran — {invoice.buyer_name}",
                    masa=f"{invoice.tax_month:02d}/{invoice.tax_year}",
                    download_path=f"/api/v1/faktur-keluaran/{invoice.id}/pdf",
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
