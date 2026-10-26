"""SPT Masa posting service — recompute header totals and detail lines.

Posting is pure and idempotent: it aggregates the tenant's source documents for
the SPT's period, replaces the stored detail lines, and rewrites the header
amounts. It has no side effects on the source documents — the ``spt_flag``
"is now reported" marking happens only at submit time, so a student may re-post
freely while the SPT is still a draft.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.models.billing import BillingCode
from app.models.enums import (
    BillingStatus,
    SlipSptFlag,
    SlipStatus,
    SlipType,
    TaxInputCreditStatus,
    TaxInvoiceStatus,
    TaxReturnStatus,
    TaxReturnType,
)
from app.models.slip import WithholdingSlip
from app.models.tax_input_invoice import TaxInputInvoice
from app.models.tax_invoice import TaxInvoice
from app.models.tax_return import TaxReturn, TaxReturnLine
from app.services import kjs

#: Faktur Pajak kode → (Bagian I section, label) mapping, as the real form
#: groups penyerahan rows by FP kode.
PENYERAHAN_SECTIONS: dict[str, tuple[str, str]] = {
    "09": ("I_A1", "Ekspor BKP/BKP Tidak Berwujud/JKP"),
    "10": ("I_A1", "Ekspor BKP/BKP Tidak Berwujud/JKP"),
    "04": ("I_A2", "Penyerahan dengan DPP Nilai Lain (FP Kode 04 dan 05)"),
    "05": ("I_A2", "Penyerahan dengan DPP Nilai Lain (FP Kode 04 dan 05)"),
    "06": ("I_A3", "Penyerahan kepada Turis sesuai Pasal 16E (FP Kode 06)"),
    "01": ("I_A4", "Penyerahan lainnya (FP Kode 01)"),
    "02": ("I_A6", "Penyerahan yang dipungut oleh Pemungut PPN (FP Kode 02 dan 03)"),
    "03": ("I_A6", "Penyerahan yang dipungut oleh Pemungut PPN (FP Kode 02 dan 03)"),
    "07": ("I_A7", "Penyerahan mendapat fasilitas PPN/PPnBM Tidak Dipungut (FP Kode 07)"),
    "08": ("I_A8", "Penyerahan mendapat fasilitas PPN/PPnBM Dibebaskan (FP Kode 08)"),
}

#: Faktur Pajak kode → (Bagian II section, label) for credited Pajak Masukan.
MASUKAN_SECTIONS: dict[str, tuple[str, str]] = {
    "04": ("II_B", "Perolehan dengan DPP Nilai Lain (FP Kode 04 dan 05)"),
    "05": ("II_B", "Perolehan dengan DPP Nilai Lain (FP Kode 04 dan 05)"),
    "02": ("II_D", "Perolehan sebagai Pemungut PPN (FP Kode 02 dan 03)"),
    "03": ("II_D", "Perolehan sebagai Pemungut PPN (FP Kode 02 dan 03)"),
}

KOMPENSASI_CODE = "-"
KOMPENSASI_DESCRIPTION = "Kompensasi kelebihan Pajak Masukan"


def compute_available_overpayment(session: Session, tax_return: TaxReturn) -> int:
    """Sisa lebih bayar PPN dari semua SPT PPN yang sudah dilaporkan sebelum periode ini.

    Sisa = Σ lebih bayar tercatat − Σ kompensasi yang sudah dipakai. A carryover
    survives until a later SPT consumes it through its own II.E row.
    """
    earlier = session.exec(
        select(TaxReturn).where(
            TaxReturn.tenant_id == tax_return.tenant_id,
            TaxReturn.siswa_id == tax_return.siswa_id,
            TaxReturn.return_type == TaxReturnType.ppn,
            TaxReturn.status == TaxReturnStatus.reported,
            TaxReturn.id != tax_return.id,
            (
                (TaxReturn.tax_year < tax_return.tax_year)
                | (
                    (TaxReturn.tax_year == tax_return.tax_year)
                    & (TaxReturn.tax_month < tax_return.tax_month)
                )
            ),
        )
    ).all()
    lebih = sum(r.ppn_lebih_bayar for r in earlier)
    dipakai = sum(r.kompensasi_lebih_bayar for r in earlier)
    return max(lebih - dipakai, 0)


def _ppn_lines(session: Session, tax_return: TaxReturn) -> list[TaxReturnLine]:
    """Aggregate Bagian I (issued faktur keluaran) and Bagian II (credited masukan)."""
    aggregated: dict[tuple[str, str], dict[str, int]] = {}

    keluaran = session.exec(
        select(TaxInvoice).where(
            TaxInvoice.tenant_id == tax_return.tenant_id,
            TaxInvoice.siswa_id == tax_return.siswa_id,
            TaxInvoice.tax_year == tax_return.tax_year,
            TaxInvoice.tax_month == tax_return.tax_month,
            TaxInvoice.status == TaxInvoiceStatus.issued,
        )
    ).all()
    for invoice in keluaran:
        section, _label = PENYERAHAN_SECTIONS.get(
            invoice.transaction_code, ("I_A4", "Penyerahan lainnya")
        )
        row = aggregated.setdefault(
            (section, invoice.transaction_code),
            {"dpp": 0, "dpp_nilai_lain": 0, "ppn": 0, "ppnbm": 0},
        )
        row["dpp"] += invoice.total_dpp
        row["dpp_nilai_lain"] += invoice.total_dpp_other
        row["ppn"] += invoice.total_ppn
        row["ppnbm"] += invoice.total_ppnbm

    masukan = session.exec(
        select(TaxInputInvoice).where(
            TaxInputInvoice.tenant_id == tax_return.tenant_id,
            TaxInputInvoice.siswa_id == tax_return.siswa_id,
            TaxInputInvoice.tax_year == tax_return.tax_year,
            TaxInputInvoice.tax_month == tax_return.tax_month,
            TaxInputInvoice.credit_status == TaxInputCreditStatus.credited,
        )
    ).all()
    for invoice in masukan:
        section, _label = MASUKAN_SECTIONS.get(
            invoice.transaction_code, ("II_C", "Perolehan lainnya (FP Kode 01, 09 dan 10)")
        )
        row = aggregated.setdefault(
            (section, invoice.transaction_code),
            {"dpp": 0, "dpp_nilai_lain": 0, "ppn": 0, "ppnbm": 0},
        )
        row["dpp"] += invoice.total_dpp
        row["dpp_nilai_lain"] += invoice.total_dpp_other
        row["ppn"] += invoice.total_ppn
        row["ppnbm"] += invoice.total_ppnbm

    lines = [
        TaxReturnLine(
            return_id=tax_return.id,
            section=section,
            code=code,
            description=_label_for(section),
            dpp=amounts["dpp"],
            dpp_nilai_lain=amounts["dpp_nilai_lain"],
            ppn=amounts["ppn"],
            ppnbm=amounts["ppnbm"],
        )
        for (section, code), amounts in aggregated.items()
    ]
    # The II.E compensation row appears whenever a carryover is consumed.
    kompensasi = min(
        compute_available_overpayment(session, tax_return),
        sum(invoice.total_ppn for invoice in masukan),
    )
    if kompensasi > 0:
        lines.append(
            TaxReturnLine(
                return_id=tax_return.id,
                section="II_E",
                code=KOMPENSASI_CODE,
                description=KOMPENSASI_DESCRIPTION,
                ppn=kompensasi,
            )
        )
    return lines


#: Source slips for an SPT PPh 21/26 period: monthly employee slips plus the
#: article-26 and one-off article-21 slips of the same month.
PPh_21_26_SOURCE_TYPES: tuple[SlipType, ...] = (SlipType.bpmp, SlipType.bp26, SlipType.bp21)


def _pph21_lines(session: Session, tax_return: TaxReturn) -> list[TaxReturnLine]:
    """Induk rows for SPT PPh 21/26: one zero row per known KJS + aggregation.

    Mirrors :func:`_pph_lines` but scoped to the monthly-slip source set and
    grouped by the PPh 21/26 KJS catalog.
    """
    aggregated: dict[str, dict[str, int]] = {}
    slips = session.exec(
        select(WithholdingSlip).where(
            WithholdingSlip.tenant_id == tax_return.tenant_id,
            WithholdingSlip.siswa_id == tax_return.siswa_id,
            WithholdingSlip.tax_year == tax_return.tax_year,
            WithholdingSlip.tax_month == tax_return.tax_month,
            WithholdingSlip.status == SlipStatus.issued,
            WithholdingSlip.slip_type.in_(PPh_21_26_SOURCE_TYPES),
        )
    ).all()
    for slip in slips:
        row = aggregated.setdefault(
            _slip_kjs(slip), {"setor_sendiri": 0, "pemotongan": 0, "ditanggung_pemerintah": 0}
        )
        row["pemotongan"] += slip.income_tax

    lines = []
    for pasal, kjs_codes in kjs.KJS_PPH_21_26.items():
        for kjs_code in kjs_codes:
            amounts = aggregated.get(
                kjs_code,
                {"setor_sendiri": 0, "pemotongan": 0, "ditanggung_pemerintah": 0},
            )
            lines.append(
                TaxReturnLine(
                    return_id=tax_return.id,
                    section=pasal,
                    code=kjs_code,
                    description=f"KJS:{kjs_code}",
                    setor_sendiri=amounts["setor_sendiri"],
                    pemotongan=amounts["pemotongan"],
                    ditanggung_pemerintah=amounts["ditanggung_pemerintah"],
                )
            )
    return lines


def _slip_pasal(slip: WithholdingSlip) -> str:
    mapped = kjs.pasal_for_kjs(slip.kap_kjs or "")
    if mapped:
        return mapped
    if slip.slip_type == SlipType.bp23:
        return "pasal_23"
    if slip.slip_type == SlipType.bpmp:
        return "pasal_21"
    return "pasal_26"


def _slip_kjs(slip: WithholdingSlip) -> str:
    if slip.kap_kjs:
        return slip.kap_kjs
    if slip.slip_type == SlipType.bpmp:
        return kjs.KJS_BPMP_DEFAULT
    return kjs.KJS_BP26_DEFAULT if slip.slip_type == SlipType.bp26 else kjs.KJS_BP23_DEFAULT


def _is_pph21_26(return_type: TaxReturnType) -> bool:
    """Either spelling (local ``pph_21_26`` + Fase ``pph21_26``) selects SPT 21/26."""
    return return_type in (TaxReturnType.pph_21_26, TaxReturnType.pph21_26)


def _pph_lines(session: Session, tax_return: TaxReturn) -> list[TaxReturnLine]:
    """Induk B rows: one zero row per known KJS, plus aggregation of issued slips.

    pph_unifikasi aggregates BP23/BP26/BPPU. SPT PPh 21/26 under either
    spelling is served by :func:`_pph21_lines` via :func:`posting`.
    """
    wanted = [SlipType.bp23, SlipType.bp26, SlipType.bppu]
    aggregated: dict[str, dict[str, int]] = {}
    slips = session.exec(
        select(WithholdingSlip).where(
            WithholdingSlip.tenant_id == tax_return.tenant_id,
            WithholdingSlip.siswa_id == tax_return.siswa_id,
            WithholdingSlip.tax_year == tax_return.tax_year,
            WithholdingSlip.tax_month == tax_return.tax_month,
            WithholdingSlip.status == SlipStatus.issued,
            WithholdingSlip.slip_type.in_(wanted),
        )
    ).all()
    for slip in slips:
        row = aggregated.setdefault(
            _slip_kjs(slip), {"setor_sendiri": 0, "pemotongan": 0, "ditanggung_pemerintah": 0}
        )
        row["pemotongan"] += slip.income_tax

    lines = []
    for pasal, kjs_codes in kjs.KJS_PPH_UNIFIKASI.items():
        for kjs_code in kjs_codes:
            amounts = aggregated.get(
                kjs_code,
                {"setor_sendiri": 0, "pemotongan": 0, "ditanggung_pemerintah": 0},
            )
            lines.append(
                TaxReturnLine(
                    return_id=tax_return.id,
                    section=pasal,
                    code=kjs_code,
                    description=f"KJS:{kjs_code}",
                    setor_sendiri=amounts["setor_sendiri"],
                    pemotongan=amounts["pemotongan"],
                    ditanggung_pemerintah=amounts["ditanggung_pemerintah"],
                )
            )
    return lines


def _label_for(section: str) -> str:
    for _code, (sec, label) in PENYERAHAN_SECTIONS.items():
        if sec == section:
            return label
    for _code, (sec, label) in MASUKAN_SECTIONS.items():
        if sec == section:
            return label
    return section


def posting(session: Session, tax_return: TaxReturn) -> TaxReturn:
    """Recompute header amounts and replace the detail lines. Idempotent."""
    if tax_return.return_type == TaxReturnType.ppn:
        lines = _ppn_lines(session, tax_return)
        keluaran_lines = [line for line in lines if line.section.startswith("I_")]
        masukan_lines = [
            line for line in lines if line.section.startswith("II_") and line.section != "II_E"
        ]
        tax_return.penyerahan_dpp = sum(line.dpp for line in keluaran_lines)
        tax_return.penyerahan_dpp_nilai_lain = sum(
            line.dpp_nilai_lain for line in keluaran_lines
        )
        tax_return.penyerahan_ppn = sum(line.ppn for line in keluaran_lines)
        tax_return.penyerahan_ppnbm = sum(line.ppnbm for line in keluaran_lines)
        kompensasi = next((line.ppn for line in lines if line.section == "II_E"), 0)
        tax_return.masukan_dpp = sum(line.dpp for line in masukan_lines)
        tax_return.masukan_dpp_nilai_lain = sum(line.dpp_nilai_lain for line in masukan_lines)
        tax_return.masukan_ppn = sum(line.ppn for line in masukan_lines)
        tax_return.kompensasi_lebih_bayar = kompensasi
        sisa = tax_return.penyerahan_ppn - tax_return.masukan_ppn - kompensasi
        tax_return.ppn_kurang_bayar = max(sisa, 0)
        tax_return.ppn_lebih_bayar = max(-sisa, 0)
        tax_return.total_income_tax = 0
        tax_return.amount_due = tax_return.ppn_kurang_bayar
    else:
        if _is_pph21_26(tax_return.return_type):
            lines = _pph21_lines(session, tax_return)
        else:
            lines = _pph_lines(session, tax_return)
        tax_return.total_income_tax = sum(line.pemotongan for line in lines)
        tax_return.amount_due = tax_return.total_income_tax
        tax_return.penyerahan_dpp = tax_return.penyerahan_dpp_nilai_lain = 0
        tax_return.penyerahan_ppn = tax_return.penyerahan_ppnbm = 0
        tax_return.masukan_dpp = tax_return.masukan_dpp_nilai_lain = 0
        tax_return.masukan_ppn = tax_return.kompensasi_lebih_bayar = 0
        tax_return.ppn_kurang_bayar = tax_return.ppn_lebih_bayar = 0

    # Replace the snapshot wholesale — delete-then-insert keeps posting re-runnable.
    for existing in session.exec(
        select(TaxReturnLine).where(TaxReturnLine.return_id == tax_return.id)
    ).all():
        session.delete(existing)
    session.add(tax_return)
    session.flush()
    for line in lines:
        line.id = None
        session.add(line)
    session.flush()
    return tax_return


def generate_billing_code(session: Session, tax_return: TaxReturn, kap_kjs: str) -> str:
    """15-digit kode billing: {KAP-KJS digits:6}{MM:2}{YY:2}{serial:5}.

    Serial runs per tenant per year; the advisory lock serialises concurrent
    generations exactly like NSFP numbering does.
    """
    return generate_code(
        session,
        tenant_id=tax_return.tenant_id,
        tax_month=tax_return.tax_month,
        tax_year=tax_return.tax_year,
        kap_kjs=kap_kjs,
    )


def generate_code(
    session: Session, *, tenant_id: int, tax_month: int, tax_year: int, kap_kjs: str
) -> str:
    """Shared serial allocator for SPT and self-service billing (no duplicates).

    Serial counts every 15-digit code of the tenant, so SPT and mandiri codes
    never collide even though they serve different obligations.
    """
    from sqlalchemy import text as sa_text
    from sqlmodel import func

    session.execute(
        sa_text("SELECT pg_advisory_xact_lock(:key1, :key2)"),
        {"key1": tenant_id, "key2": 20_000_000 + tax_year},
    )
    used = session.exec(
        select(BillingCode.code).where(
            BillingCode.tenant_id == tenant_id,
            func.length(BillingCode.code) == 15,
        )
    ).all()
    serials = [int(c[-5:]) for c in used if c[-5:].isdigit()]
    serial = max(serials, default=0) + 1
    if serial > 99_999:
        raise ValueError("Serial kode billing melebihi kapasitas tahun berjalan")
    digits = kap_kjs.replace("-", "")[:6].ljust(6, "0")
    return f"{digits}{tax_month:02d}{tax_year % 100:02d}{serial:05d}"


def ensure_billing(session: Session, tax_return: TaxReturn) -> BillingCode:
    """Return the SPT's active unpaid billing, creating one only if needed.

    Re-submitting an awaiting-payment SPT never doubles the billing — the same
    code stays valid until it is paid, as in the real portal.
    """

    existing = session.exec(
        select(BillingCode).where(
            BillingCode.spt_id == tax_return.id,
            BillingCode.status == BillingStatus.unpaid,
        )
    ).first()
    if existing is not None:
        return existing

    if tax_return.return_type == TaxReturnType.ppn:
        kap_kjs = kjs.KJS_PPN_MASA
    else:
        lines = session.exec(
            select(TaxReturnLine).where(TaxReturnLine.return_id == tax_return.id)
        ).all()
        top = max(lines, key=lambda line: line.pemotongan, default=None)
        kap_kjs = top.code if top is not None and top.pemotongan > 0 else "-"
    billing = BillingCode(
        tenant_id=tax_return.tenant_id,
        siswa_id=tax_return.siswa_id,
        spt_id=tax_return.id,
        code=generate_billing_code(session, tax_return, kap_kjs),
        amount=tax_return.amount_due,
        kap_kjs=kap_kjs,
        masa_pajak=f"{tax_return.tax_month:02d}{tax_return.tax_year}",
    )
    session.add(billing)
    session.flush()
    return billing


def mark_sources_reported(session: Session, tax_return: TaxReturn) -> None:
    """Set ``spt_flag = reported_in_spt`` on every source document of the period.

    This is the "posting SPT" effect visible in the source-document modules: a
    faktur/bupot that made it into a filed SPT carries the flag thereafter.
    """
    if tax_return.return_type == TaxReturnType.ppn:
        for invoice in session.exec(
            select(TaxInvoice).where(
                TaxInvoice.tenant_id == tax_return.tenant_id,
                TaxInvoice.siswa_id == tax_return.siswa_id,
                TaxInvoice.tax_year == tax_return.tax_year,
                TaxInvoice.tax_month == tax_return.tax_month,
                TaxInvoice.status == TaxInvoiceStatus.issued,
            )
        ).all():
            invoice.spt_flag = SlipSptFlag.reported_in_spt
            session.add(invoice)
        for invoice in session.exec(
            select(TaxInputInvoice).where(
                TaxInputInvoice.tenant_id == tax_return.tenant_id,
                TaxInputInvoice.siswa_id == tax_return.siswa_id,
                TaxInputInvoice.tax_year == tax_return.tax_year,
                TaxInputInvoice.tax_month == tax_return.tax_month,
                TaxInputInvoice.credit_status == TaxInputCreditStatus.credited,
            )
        ).all():
            invoice.spt_flag = SlipSptFlag.reported_in_spt
            session.add(invoice)
    else:
        if _is_pph21_26(tax_return.return_type):
            source_types = PPh_21_26_SOURCE_TYPES
        else:
            source_types = (SlipType.bp23, SlipType.bp26, SlipType.bppu)
        for slip in session.exec(
            select(WithholdingSlip).where(
                WithholdingSlip.tenant_id == tax_return.tenant_id,
                WithholdingSlip.siswa_id == tax_return.siswa_id,
                WithholdingSlip.tax_year == tax_return.tax_year,
                WithholdingSlip.tax_month == tax_return.tax_month,
                WithholdingSlip.status == SlipStatus.issued,
                WithholdingSlip.slip_type.in_(source_types),
            )
        ).all():
            slip.spt_flag = SlipSptFlag.reported_in_spt
            session.add(slip)
