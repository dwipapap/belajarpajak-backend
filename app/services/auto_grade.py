"""Penilaian otomatis (auto-grade) rule checks for submitted student documents.

Rule-checks only (no answer key): verifies that a student's submitted document
is internally consistent, complete, and adheres to tax rules by reusing the
existing calculation and catalog services.

Returns ``(auto_score, checks)`` where each check is a dict:
``{"code": str, "label": str, "passed": bool, "message": str, "weight": int}``
and ``auto_score = round(100 * passed_weight / total_weight)``.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlmodel import Session, select

from app.core.money import apply_basis_points, basis_points_to_percent
from app.models.billing import BillingCode
from app.models.enums import (
    AssignmentDocType,
    BillingStatus,
    BuyerIdentityType,
    SlipStatus,
    SlipTaxFacility,
    SlipType,
    TaxInputCreditStatus,
    TaxInvoiceStatus,
    TaxReturnStatus,
    TaxReturnType,
)
from app.models.slip import WithholdingSlip
from app.models.tax_input_invoice import TaxInputInvoice, TaxInputLine
from app.models.tax_invoice import TaxInvoice, TaxInvoiceLine
from app.models.tax_return import TaxReturn
from app.routers.slips import (
    DIRECT_DPP_TYPES,
    FOREIGN_TIN_RE,
    NO_COUNTERPARTY_TYPES,
    _resolve_income_tax,
    calculate_dpp,
)
from app.routers.tax_invoices import (
    DPP_OTHER_DENOMINATOR,
    DPP_OTHER_NUMERATOR,
    dpp_nilai_lain,
)
from app.schemas.tax_invoice import DPP_OTHER_CODES, TRANSACTION_CODES
from app.services import objek_pajak as objek_service
from app.services import spt as spt_service
from app.services import ter as ter_service


def _check(
    code: str, label: str, passed: bool, message: str, weight: int
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "passed": bool(passed),
        "message": message,
        "weight": int(weight),
    }


def compute_score(checks: list[dict[str, Any]]) -> int:
    """Compute weighted 0..100 integer score from a list of check dicts."""
    total_weight = sum(int(c["weight"]) for c in checks)
    if total_weight <= 0:
        return 0
    passed_weight = sum(int(c["weight"]) for c in checks if c.get("passed"))
    return int(round(100 * passed_weight / total_weight))


def _is_valid_period(tax_month: int | None, tax_year: int | None) -> bool:
    return (
        tax_month is not None
        and tax_year is not None
        and 1 <= tax_month <= 12
        and 2020 <= tax_year <= 2100
    )


def _date_in_period(
    doc_date: date | None, tax_month: int | None, tax_year: int | None
) -> bool:
    if doc_date is None or not _is_valid_period(tax_month, tax_year):
        return False
    return doc_date.month == tax_month and doc_date.year == tax_year


# ---------------------------------------------------------------------------
# 1. Withholding Slips (e-Bupot: bp21, bp26, bppu, bpmp, bpnr, sp, cy)
# ---------------------------------------------------------------------------


def grade_withholding_slip(
    session: Session, slip: WithholdingSlip
) -> tuple[int, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    stype = slip.slip_type

    # 1. Document status (issued, not draft/invalid)
    is_issued = slip.status == SlipStatus.issued and bool(slip.withholding_number)
    checks.append(
        _check(
            "doc_status",
            "Status dokumen sudah terbit (final)",
            is_issued,
            (
                f"Bukti potong telah terbit dengan nomor {slip.withholding_number}."
                if is_issued
                else (
                    f"Status dokumen masih '{slip.status.value}'; "
                    "bukti potong harus diterbitkan terlebih dahulu."
                )
            ),
            20,
        )
    )

    # 2. Electronic signature
    is_signed = slip.electronic_signature_status == "signed" and bool(
        (slip.signer_name or "").strip()
    )
    checks.append(
        _check(
            "signature",
            "Tanda tangan elektronik dan nama penandatangan",
            is_signed,
            (
                f"Ditandatangani secara elektronik oleh {slip.signer_name}."
                if is_signed
                else (
                    "Bukti potong belum ditandatangani secara elektronik "
                    "atau nama penandatangan kosong."
                )
            ),
            10,
        )
    )

    # 3. Identity fields
    no_cp = stype in NO_COUNTERPARTY_TYPES
    ident = (slip.recipient_identity_number or "").strip()
    rec_name = (slip.recipient_name or "").strip()
    if no_cp:
        wh_npwp = (slip.withholder_npwp or "").strip()
        ident_ok = not wh_npwp or (wh_npwp.isdigit() and len(wh_npwp) in (15, 16))
        ident_msg = (
            "Dokumen setor sendiri/digunggung tidak memerlukan lawan transaksi."
            if ident_ok
            else "Format NPWP pemotong/penyetor tidak valid (harus 15–16 digit angka)."
        )
    elif stype == SlipType.bpnr:
        ident_ok = (
            bool(rec_name)
            and bool(ident)
            and len(ident) <= 30
            and bool(FOREIGN_TIN_RE.fullmatch(ident))
        )
        ident_msg = (
            f"Identitas WPLN ({ident} - {rec_name}) terisi dengan format TIN yang valid."
            if ident_ok
            else "Nama penerima atau TIN luar negeri belum lengkap/format tidak valid."
        )
    elif stype == SlipType.bp26:
        ident_ok = bool(rec_name) and bool(ident)
        ident_msg = (
            f"Identitas penerima ({ident} - {rec_name}) terisi lengkap."
            if ident_ok
            else "Nama dan nomor identitas (TIN/Paspor) penerima wajib diisi."
        )
    else:
        ident_ok = bool(rec_name) and ident.isdigit() and len(ident) == 16
        ident_msg = (
            f"NPWP/NIK 16 digit ({ident}) dan nama penerima ({rec_name}) valid."
            if ident_ok
            else "Nama penerima wajib diisi dan nomor identitas (NPWP/NIK) harus 16 digit angka."
        )
    checks.append(
        _check(
            "recipient_identity",
            "Kelengkapan dan format identitas wajib pajak",
            ident_ok,
            ident_msg,
            15,
        )
    )

    # 4. Masa/Tahun & Tanggal dokumen
    period_ok = _is_valid_period(slip.tax_month, slip.tax_year)
    if slip.document_date is not None:
        date_ok = period_ok and _date_in_period(
            slip.document_date, slip.tax_month, slip.tax_year
        )
        doc_iso = slip.document_date.isoformat()
        date_msg = (
            f"Masa pajak {slip.tax_month:02d}/{slip.tax_year} sesuai tanggal dokumen ({doc_iso})."
            if date_ok
            else (
                f"Tanggal dokumen ({doc_iso}) tidak berada di dalam "
                f"masa pajak {slip.tax_month:02d}/{slip.tax_year}."
            )
        )
    else:
        date_ok = period_ok
        date_msg = (
            f"Masa pajak {slip.tax_month:02d}/{slip.tax_year} valid."
            if date_ok
            else "Masa atau tahun pajak tidak valid."
        )
    checks.append(
        _check(
            "period_and_date",
            "Kesesuaian masa/tahun pajak dan tanggal dokumen",
            date_ok,
            date_msg,
            10,
        )
    )

    # 5. Kode objek pajak, KAP-KJS, dan tarif
    catalog_obj = objek_service.get_object(stype.value, slip.tax_object_code or "")
    rate_pct = basis_points_to_percent(slip.rate_basis_points)
    facility_val = slip.tax_facility.value if slip.tax_facility else "none"
    rate_ok = False
    rate_msg = ""

    if catalog_obj is None:
        rate_ok = False
        rate_msg = (
            f"Kode objek pajak '{slip.tax_object_code}' tidak ditemukan "
            f"pada katalog {stype.value.upper()}."
        )
    else:
        kap_ok = not slip.kap_kjs or slip.kap_kjs == catalog_obj["kap_kjs"]
        if not kap_ok:
            rate_ok = False
            rate_msg = (
                f"KAP-KJS '{slip.kap_kjs}' tidak sesuai dengan katalog "
                f"({catalog_obj['kap_kjs']})."
            )
        elif stype == SlipType.bp21:
            rate_ok = rate_pct >= 0
            rate_msg = (
                f"Kode objek {slip.tax_object_code} dengan tarif manual {rate_pct:g}% valid."
            )
        elif stype == SlipType.bpmp:
            cat = ter_service.ter_category_for_ptkp(slip.ptkp_status)
            rate_ok = cat is not None and rate_pct >= 0
            rate_msg = (
                f"Kode objek {slip.tax_object_code} dengan status PTKP "
                f"{slip.ptkp_status} (TER {cat}) dan tarif {rate_pct:g}% valid."
                if rate_ok
                else "BPMP wajib mencantumkan status PTKP yang valid."
            )
        elif facility_val in objek_service.NON_ENFORCED_FACILITIES:
            if slip.tax_facility == SlipTaxFacility.treaty and stype in (
                SlipType.bp26,
                SlipType.bpnr,
            ):
                rate_ok = bool((slip.nomor_skd or "").strip())
                rate_msg = (
                    f"Fasilitas SKD mencantumkan nomor SKD ({slip.nomor_skd}) "
                    f"dengan tarif {rate_pct:g}%."
                    if rate_ok
                    else "Fasilitas SKD/Treaty wajib mencantumkan nomor Receipt SKD."
                )
            else:
                rate_ok = True
                rate_msg = (
                    f"Kode objek {slip.tax_object_code} dengan fasilitas "
                    f"{facility_val} valid."
                )
        else:
            exp_rate = objek_service.expected_rate(
                catalog_obj, objek_service.facility_code_for(facility_val)
            )
            if exp_rate is None:
                rate_ok = True
                rate_msg = f"Kode objek {slip.tax_object_code} valid (tarif {rate_pct:g}%)."
            else:
                rate_ok = abs(float(rate_pct) - float(exp_rate)) <= 1e-6
                rate_msg = (
                    f"Tarif {rate_pct:g}% sesuai dengan katalog objek pajak "
                    f"{slip.tax_object_code}."
                    if rate_ok
                    else (
                        f"Tarif {rate_pct:g}% tidak sesuai katalog "
                        f"(seharusnya {exp_rate:g}%)."
                    )
                )
        if (
            rate_ok
            and slip.tax_facility == SlipTaxFacility.skb
            and not (slip.document_number or "").strip()
        ):
            rate_ok = False
            rate_msg = "Fasilitas SKB wajib mencantumkan nomor dokumen SKB."

    checks.append(
        _check(
            "tax_object_and_rate",
            "Kesesuaian kode objek pajak, fasilitas, dan tarif",
            rate_ok,
            rate_msg,
            20,
        )
    )

    # 6. Perhitungan DPP dan PPh
    if stype in DIRECT_DPP_TYPES and slip.dpp_rate_basis_points == 10_000:
        effective_gross = slip.gross_income if slip.gross_income > 0 else slip.dpp
        expected_dpp = effective_gross
    else:
        effective_gross = slip.gross_income
        expected_dpp = calculate_dpp(effective_gross, slip.dpp_rate_basis_points)

    expected_pph, method = _resolve_income_tax(
        session,
        slip_type=stype,
        tax_nature=slip.tax_nature,
        tax_facility=slip.tax_facility,
        gross_income=effective_gross,
        dpp=expected_dpp,
        rate_basis_points=slip.rate_basis_points,
        ptkp_status=slip.ptkp_status,
        tax_year=slip.tax_year,
        negara_treaty=slip.negara_treaty,
        tarif_treaty_basis_points=slip.tarif_treaty_basis_points,
        recipient_identity_number=slip.recipient_identity_number,
        ter_category=slip.ter_category,
        tax_month=slip.tax_month,
        tax_object_code=slip.tax_object_code,
        manual_income_tax=(
            slip.income_tax
            if stype == SlipType.bpmp and slip.calculation_method == "manual"
            else None
        ),
    )
    math_ok = (
        effective_gross > 0
        and slip.dpp == expected_dpp
        and slip.income_tax == expected_pph
    )
    if effective_gross <= 0:
        math_msg = "Penghasilan bruto / DPP harus lebih besar dari Rp0."
    elif math_ok:
        math_msg = (
            f"Perhitungan DPP (Rp{slip.dpp:,}) dan PPh (Rp{slip.income_tax:,}) akurat "
            f"(metode: {method})."
        ).replace(",", ".")
    else:
        math_msg = (
            f"Perhitungan tidak cocok: tercatat DPP Rp{slip.dpp:,} & PPh Rp{slip.income_tax:,}, "
            f"seharusnya DPP Rp{expected_dpp:,} & PPh Rp{expected_pph:,}."
        ).replace(",", ".")

    checks.append(
        _check(
            "tax_calculation",
            "Akurasi perhitungan DPP dan PPh terutang",
            math_ok,
            math_msg,
            25,
        )
    )

    return compute_score(checks), checks


# ---------------------------------------------------------------------------
# 2. Faktur Pajak Keluaran (TaxInvoice)
# ---------------------------------------------------------------------------


def grade_faktur_keluaran(
    session: Session, invoice: TaxInvoice
) -> tuple[int, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    lines = list(
        session.exec(
            select(TaxInvoiceLine)
            .where(TaxInvoiceLine.invoice_id == invoice.id)
            .order_by(TaxInvoiceLine.line_order, TaxInvoiceLine.id)
        ).all()
    )

    # 1. Status terbit & tanda tangan
    is_issued = (
        invoice.status == TaxInvoiceStatus.issued
        and bool(invoice.invoice_number)
        and invoice.electronic_signature_status == "signed"
    )
    checks.append(
        _check(
            "doc_status",
            "Faktur sudah diterbitkan (NSFP) dan ditandatangani",
            is_issued,
            (
                f"Faktur telah terbit dengan NSFP {invoice.invoice_number}."
                if is_issued
                else (
                    f"Status faktur '{invoice.status.value}'; faktur harus "
                    "diterbitkan dan ditandatangani terlebih dahulu."
                )
            ),
            20,
        )
    )

    # 2. Kode transaksi & masa/tanggal faktur
    code_ok = invoice.transaction_code in TRANSACTION_CODES
    date_ok = _date_in_period(
        invoice.invoice_date, invoice.tax_month, invoice.tax_year
    )
    tx_date_ok = code_ok and date_ok
    if not code_ok:
        tx_msg = f"Kode transaksi '{invoice.transaction_code}' tidak valid."
    elif not date_ok:
        tx_msg = (
            f"Tanggal faktur ({invoice.invoice_date.isoformat()}) tidak berada dalam "
            f"masa pajak {invoice.tax_month:02d}/{invoice.tax_year}."
        )
    else:
        tx_msg = (
            f"Kode transaksi {invoice.transaction_code} dan tanggal faktur "
            f"({invoice.invoice_date.isoformat()}) sesuai masa "
            f"{invoice.tax_month:02d}/{invoice.tax_year}."
        )
    checks.append(
        _check(
            "transaction_and_period",
            "Kode transaksi dan kesesuaian tanggal dengan masa pajak",
            tx_date_ok,
            tx_msg,
            15,
        )
    )

    # 3. Identitas pembeli
    buyer_name = (invoice.buyer_name or "").strip()
    buyer_ident = (invoice.buyer_identity_number or "").strip()
    if invoice.buyer_identity_type in (BuyerIdentityType.npwp, BuyerIdentityType.nik):
        buyer_ok = (
            bool(buyer_name) and buyer_ident.isdigit() and len(buyer_ident) == 16
        )
        id_kind = invoice.buyer_identity_type.value.upper()
        buyer_msg = (
            f"Identitas pembeli {buyer_name} ({id_kind}: {buyer_ident}) valid 16 digit."
            if buyer_ok
            else "Nama pembeli wajib diisi dan NPWP/NIK pembeli harus 16 digit angka."
        )
    else:
        buyer_ok = bool(buyer_name) and bool(buyer_ident)
        buyer_msg = (
            f"Identitas pembeli {buyer_name} ({buyer_ident}) terisi lengkap."
            if buyer_ok
            else "Nama dan nomor identitas pembeli wajib diisi."
        )
    checks.append(
        _check(
            "buyer_identity",
            "Kelengkapan dan format identitas pembeli",
            buyer_ok,
            buyer_msg,
            15,
        )
    )

    # 4. Perhitungan baris detail transaksi
    forced_dpp_other = invoice.transaction_code in DPP_OTHER_CODES
    lines_ok = len(lines) > 0
    line_err = ""
    for idx, ln in enumerate(lines, start=1):
        raw_total = Decimal(ln.unit_price) * Decimal(ln.quantity_milli) / Decimal("1000")
        exp_total = int(raw_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        exp_dpp = max(exp_total - ln.discount, 0)
        if forced_dpp_other and not ln.use_dpp_other:
            lines_ok = False
            line_err = (
                f"Baris #{idx}: Kode transaksi {invoice.transaction_code} "
                "wajib menggunakan DPP Nilai Lain (11/12)."
            )
            break
        if ln.use_dpp_other:
            exp_dpp_other = dpp_nilai_lain(exp_dpp)
            exp_ppn = apply_basis_points(exp_dpp_other, ln.ppn_rate_basis_points)
        else:
            exp_dpp_other = 0
            exp_ppn = apply_basis_points(exp_dpp, ln.ppn_rate_basis_points)
        exp_ppnbm = apply_basis_points(exp_dpp, ln.ppnbm_rate_basis_points)

        if (
            ln.total_price != exp_total
            or ln.dpp != exp_dpp
            or ln.dpp_other != exp_dpp_other
            or ln.ppn != exp_ppn
            or ln.ppnbm != exp_ppnbm
        ):
            lines_ok = False
            line_err = (
                f"Baris #{idx} ({ln.item_name}): perhitungan DPP/DPP Nilai Lain/PPN "
                "tidak konsisten."
            )
            break

    if not lines:
        line_msg = "Faktur tidak memiliki baris detail barang/jasa."
    elif lines_ok:
        line_msg = (
            f"Seluruh {len(lines)} baris barang/jasa memiliki perhitungan DPP, "
            "DPP Nilai Lain (11/12), dan PPN yang tepat."
        )
    else:
        line_msg = line_err

    checks.append(
        _check(
            "line_calculations",
            "Perhitungan DPP, DPP Nilai Lain (11/12), dan PPN per baris",
            lines_ok,
            line_msg,
            25,
        )
    )

    # 5. Akumulasi total header = jumlah seluruh baris
    sum_price = sum(ln.total_price for ln in lines)
    sum_disc = sum(ln.discount for ln in lines)
    sum_dpp = sum(ln.dpp for ln in lines)
    sum_dpp_other = sum(ln.dpp_other for ln in lines)
    sum_ppn = sum(ln.ppn for ln in lines)
    sum_ppnbm = sum(ln.ppnbm for ln in lines)
    totals_ok = (
        len(lines) > 0
        and invoice.total_dpp > 0
        and invoice.total_price == sum_price
        and invoice.total_discount == sum_disc
        and invoice.total_dpp == sum_dpp
        and invoice.total_dpp_other == sum_dpp_other
        and invoice.total_ppn == sum_ppn
        and invoice.total_ppnbm == sum_ppnbm
    )
    totals_ok_msg = (
        f"Total DPP (Rp{invoice.total_dpp:,}) dan PPN (Rp{invoice.total_ppn:,}) "
        "sesuai dengan jumlah rincian baris."
    ).replace(",", ".")
    checks.append(
        _check(
            "header_totals",
            "Kesesuaian total DPP dan PPN faktur dengan penjumlahan baris",
            totals_ok,
            (
                totals_ok_msg
                if totals_ok
                else "Total header faktur tidak cocok dengan penjumlahan baris atau bernilai Rp0."
            ),
            25,
        )
    )

    return compute_score(checks), checks


# ---------------------------------------------------------------------------
# 3. Faktur Pajak Masukan (TaxInputInvoice)
# ---------------------------------------------------------------------------


def grade_faktur_masukan(
    session: Session, invoice: TaxInputInvoice
) -> tuple[int, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    lines = list(
        session.exec(
            select(TaxInputLine)
            .where(TaxInputLine.invoice_id == invoice.id)
            .order_by(TaxInputLine.line_order, TaxInputLine.id)
        ).all()
    )

    # 1. Status dokumen valid (issued, not draft/invalid)
    status_ok = invoice.status == TaxInvoiceStatus.issued and bool(
        invoice.invoice_number
    )
    checks.append(
        _check(
            "doc_status",
            "Status Faktur Masukan aktif/terbit",
            status_ok,
            (
                f"Faktur Masukan nomor {invoice.invoice_number} berstatus aktif/terbit."
                if status_ok
                else (
                    f"Faktur Masukan masih berstatus '{invoice.status.value}' "
                    "atau nomor faktur kosong."
                )
            ),
            20,
        )
    )

    # 2. Keputusan pengkreditan (credited / uncredited decision made)
    decision_made = invoice.credit_status in (
        TaxInputCreditStatus.credited,
        TaxInputCreditStatus.uncredited,
    )
    checks.append(
        _check(
            "credit_decision",
            "Keputusan pengkreditan Pajak Masukan (Dikreditkan / Tidak Dikreditkan)",
            decision_made,
            (
                f"Keputusan pengkreditan telah ditetapkan: {invoice.credit_status.value}."
                if decision_made
                else (
                    "Faktur Masukan belum ditetapkan status pengkreditannya "
                    "(masih belum diputuskan)."
                )
            ),
            25,
        )
    )

    # 3. Identitas penjual & pembeli
    seller_npwp = (invoice.seller_npwp or "").strip()
    seller_name = (invoice.seller_name or "").strip()
    buyer_ident = (invoice.buyer_identity_number or "").strip()
    ident_ok = (
        bool(seller_name)
        and seller_npwp.isdigit()
        and len(seller_npwp) in (15, 16)
        and bool(buyer_ident)
    )
    checks.append(
        _check(
            "parties_identity",
            "Kelengkapan identitas PKP Penjual dan Pembeli",
            ident_ok,
            (
                f"Identitas penjual ({seller_name} - {seller_npwp}) dan pembeli valid."
                if ident_ok
                else "Nama atau NPWP 15/16 digit PKP Penjual/Pembeli belum lengkap."
            ),
            15,
        )
    )

    # 4. Masa pajak & tanggal faktur
    period_ok = _is_valid_period(invoice.tax_month, invoice.tax_year) and (
        invoice.invoice_date is not None
    )
    inv_iso = invoice.invoice_date.isoformat() if invoice.invoice_date else "-"
    checks.append(
        _check(
            "period_and_date",
            "Kelengkapan masa pajak dan tanggal faktur",
            period_ok,
            (
                f"Masa pajak {invoice.tax_month:02d}/{invoice.tax_year} "
                f"dan tanggal faktur {inv_iso} valid."
                if period_ok
                else "Masa pajak atau tanggal faktur tidak valid."
            ),
            15,
        )
    )

    # 5. Konsistensi nilai total dengan baris
    if lines:
        sum_dpp = sum(ln.dpp for ln in lines)
        sum_dpp_other = sum(ln.dpp_other for ln in lines)
        sum_ppn = sum(ln.ppn for ln in lines)
        sum_ppnbm = sum(ln.ppnbm for ln in lines)
        totals_ok = (
            invoice.total_dpp > 0
            and invoice.total_dpp == sum_dpp
            and invoice.total_dpp_other == sum_dpp_other
            and invoice.total_ppn == sum_ppn
            and invoice.total_ppnbm == sum_ppnbm
        )
    else:
        expected_base = (
            int(
                (
                    Decimal(invoice.total_dpp)
                    * DPP_OTHER_NUMERATOR
                    / DPP_OTHER_DENOMINATOR
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            if invoice.total_dpp_other > 0
            else invoice.total_dpp
        )
        totals_ok = (
            invoice.total_dpp > 0 and invoice.total_ppn >= 0 and expected_base > 0
        )

    masukan_totals_msg = (
        f"Nilai DPP (Rp{invoice.total_dpp:,}) dan PPN (Rp{invoice.total_ppn:,}) konsisten."
    ).replace(",", ".")
    checks.append(
        _check(
            "totals_consistency",
            "Konsistensi nilai DPP dan PPN Faktur Masukan",
            totals_ok,
            (
                masukan_totals_msg
                if totals_ok
                else "Nilai total DPP/PPN tidak konsisten dengan rincian baris atau bernilai Rp0."
            ),
            25,
        )
    )

    return compute_score(checks), checks


# ---------------------------------------------------------------------------
# 4. SPT Masa (TaxReturn: ppn, pph_unifikasi, pph_21_26 / pph21_26)
# ---------------------------------------------------------------------------


def _recompute_expected_spt_totals(
    session: Session, tax_return: TaxReturn
) -> dict[str, int]:
    """Pure recomputation of SPT totals from the student's underlying documents."""
    if tax_return.return_type == TaxReturnType.ppn:
        lines = spt_service._ppn_lines(session, tax_return)
        keluaran_lines = [line for line in lines if line.section.startswith("I_")]
        masukan_lines = [
            line
            for line in lines
            if line.section.startswith("II_") and line.section != "II_E"
        ]
        penyerahan_dpp = sum(line.dpp for line in keluaran_lines)
        penyerahan_ppn = sum(line.ppn for line in keluaran_lines)
        kompensasi = next((line.ppn for line in lines if line.section == "II_E"), 0)
        masukan_dpp = sum(line.dpp for line in masukan_lines)
        masukan_ppn = sum(line.ppn for line in masukan_lines)
        sisa = penyerahan_ppn - masukan_ppn - kompensasi
        kurang_bayar = max(sisa, 0)
        lebih_bayar = max(-sisa, 0)
        return {
            "penyerahan_dpp": penyerahan_dpp,
            "penyerahan_ppn": penyerahan_ppn,
            "masukan_dpp": masukan_dpp,
            "masukan_ppn": masukan_ppn,
            "ppn_kurang_bayar": kurang_bayar,
            "ppn_lebih_bayar": lebih_bayar,
            "total_income_tax": 0,
            "amount_due": kurang_bayar,
        }

    if spt_service._is_pph21_26(tax_return.return_type):
        lines = spt_service._pph21_lines(session, tax_return)
    else:
        lines = spt_service._pph_lines(session, tax_return)
    total_pph = sum(line.pemotongan for line in lines)
    return {
        "penyerahan_dpp": 0,
        "penyerahan_ppn": 0,
        "masukan_dpp": 0,
        "masukan_ppn": 0,
        "ppn_kurang_bayar": 0,
        "ppn_lebih_bayar": 0,
        "total_income_tax": total_pph,
        "amount_due": total_pph,
    }


def grade_tax_return(
    session: Session, tax_return: TaxReturn
) -> tuple[int, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []

    # 1. Status SPT sudah dilaporkan (reported + BPE)
    is_reported = (
        tax_return.status == TaxReturnStatus.reported
        and bool(tax_return.bpe_number)
        and tax_return.submitted_at is not None
    )
    checks.append(
        _check(
            "spt_reported",
            "SPT telah dilaporkan dan memiliki Nomor BPE",
            is_reported,
            (
                f"SPT telah dilaporkan dengan nomor BPE {tax_return.bpe_number}."
                if is_reported
                else (
                    f"Status SPT masih '{tax_return.status.value}'; "
                    "SPT harus dilaporkan hingga menerbitkan BPE."
                )
            ),
            25,
        )
    )

    # 2. Pernyataan & penandatangan
    statement_ok = bool((tax_return.signer_name or "").strip()) and (
        tax_return.submitted_at is not None
    )
    checks.append(
        _check(
            "statement_signed",
            "Pernyataan kebenaran SPT telah disetujui dan ditandatangani",
            statement_ok,
            (
                f"Pernyataan SPT ditandatangani oleh {tax_return.signer_name}."
                if statement_ok
                else "Bagian pernyataan SPT belum ditandatangani oleh wajib pajak/kuasa."
            ),
            15,
        )
    )

    # 3. Masa dan tahun pajak valid
    period_ok = _is_valid_period(tax_return.tax_month, tax_return.tax_year)
    checks.append(
        _check(
            "period_valid",
            "Validitas masa dan tahun pajak SPT",
            period_ok,
            (
                f"Masa pajak {tax_return.tax_month:02d}/{tax_return.tax_year} valid."
                if period_ok
                else "Masa atau tahun pajak SPT tidak valid."
            ),
            10,
        )
    )

    # 4. Kesesuaian angka Induk SPT dengan hasil hitung ulang dokumen sumber masa tersebut
    expected = _recompute_expected_spt_totals(session, tax_return)
    if tax_return.return_type == TaxReturnType.ppn:
        totals_match = (
            tax_return.penyerahan_dpp == expected["penyerahan_dpp"]
            and tax_return.penyerahan_ppn == expected["penyerahan_ppn"]
            and tax_return.masukan_dpp == expected["masukan_dpp"]
            and tax_return.masukan_ppn == expected["masukan_ppn"]
            and tax_return.ppn_kurang_bayar == expected["ppn_kurang_bayar"]
            and tax_return.ppn_lebih_bayar == expected["ppn_lebih_bayar"]
            and tax_return.amount_due == expected["amount_due"]
        )
        totals_msg = (
            f"Nilai PPN Keluaran (Rp{tax_return.penyerahan_ppn:,}), "
            f"Masukan (Rp{tax_return.masukan_ppn:,}), dan Kurang/Lebih Bayar sesuai "
            f"dokumen sumber masa {tax_return.tax_month:02d}/{tax_return.tax_year}."
        ).replace(",", ".")
        if not totals_match:
            totals_msg = (
                "Angka pada SPT PPN tidak sinkron dengan faktur sumber terkini; "
                "lakukan Posting Ulang SPT sebelum melapor."
            )
    else:
        totals_match = (
            tax_return.total_income_tax == expected["total_income_tax"]
            and tax_return.amount_due == expected["amount_due"]
        )
        pph_ok_msg = (
            f"Total PPh terutang (Rp{tax_return.total_income_tax:,}) sesuai "
            "dengan seluruh bukti potong terbit pada masa ini."
        ).replace(",", ".")
        pph_err_msg = (
            f"Total PPh pada SPT (Rp{tax_return.total_income_tax:,}) tidak cocok "
            f"dengan jumlah bukti potong sumber (Rp{expected['total_income_tax']:,})."
        ).replace(",", ".")
        totals_msg = pph_ok_msg if totals_match else pph_err_msg

    checks.append(
        _check(
            "recomputed_totals",
            "Kesesuaian nilai SPT dengan rekalkulasi dokumen sumber",
            totals_match,
            totals_msg,
            30,
        )
    )

    # 5. Pembayaran / kode billing lunas jika Kurang Bayar
    if tax_return.amount_due > 0:
        paid_billing = session.exec(
            select(BillingCode).where(
                BillingCode.spt_id == tax_return.id,
                BillingCode.status == BillingStatus.paid,
            )
        ).first()
        has_deposit_payment = bool(
            (tax_return.payment_ref or "").startswith("DEPOSIT:")
        )
        payment_ok = paid_billing is not None or has_deposit_payment
        if paid_billing is not None:
            payment_msg = (
                f"SPT Kurang Bayar telah dilunasi melalui Kode Billing {paid_billing.code} "
                f"(NTPN: {paid_billing.ntpn or '-'})."
            )
        elif has_deposit_payment:
            payment_msg = (
                "SPT Kurang Bayar telah dilunasi melalui pemindahbukuan Deposit "
                f"({tax_return.payment_ref})."
            )
        else:
            payment_msg = (
                "SPT Kurang Bayar belum memiliki pembayaran Kode Billing lunas "
                "atau pelunasan Deposit."
            )
    elif tax_return.ppn_lebih_bayar > 0:
        payment_ok = tax_return.lb_treatment is not None
        payment_msg = (
            f"SPT Lebih Bayar telah memilih perlakuan: {tax_return.lb_treatment.value}."
            if payment_ok
            else "SPT Lebih Bayar belum memilih perlakuan (kompensasi/restitusi)."
        )
    else:
        payment_ok = True
        payment_msg = "SPT Nihil tidak memerlukan pembayaran Kode Billing."

    checks.append(
        _check(
            "payment_or_settlement",
            "Penyelesaian pembayaran Kurang Bayar / perlakuan Lebih Bayar",
            payment_ok,
            payment_msg,
            20,
        )
    )

    return compute_score(checks), checks


# ---------------------------------------------------------------------------
# Dispatcher by AssignmentDocType
# ---------------------------------------------------------------------------

SLIP_DOC_TYPES: dict[AssignmentDocType, SlipType] = {
    AssignmentDocType.bp21: SlipType.bp21,
    AssignmentDocType.bp26: SlipType.bp26,
    AssignmentDocType.bppu: SlipType.bppu,
    AssignmentDocType.bpmp: SlipType.bpmp,
    AssignmentDocType.bpnr: SlipType.bpnr,
    AssignmentDocType.sp: SlipType.sp,
    AssignmentDocType.cy: SlipType.cy,
}

SPT_DOC_TYPES: dict[AssignmentDocType, tuple[TaxReturnType, ...]] = {
    AssignmentDocType.spt_ppn: (TaxReturnType.ppn,),
    AssignmentDocType.spt_pph_unifikasi: (TaxReturnType.pph_unifikasi,),
    AssignmentDocType.spt_pph_21_26: (
        TaxReturnType.pph_21_26,
        TaxReturnType.pph21_26,
    ),
    AssignmentDocType.spt_pph21_26: (
        TaxReturnType.pph_21_26,
        TaxReturnType.pph21_26,
    ),
}


def auto_grade_document(
    session: Session, doc_type: AssignmentDocType | str, doc: Any
) -> tuple[int, list[dict[str, Any]]]:
    """Run rule-based auto-grading for a resolved document object."""
    dtype = AssignmentDocType(doc_type)
    if dtype in SLIP_DOC_TYPES:
        return grade_withholding_slip(session, doc)
    if dtype == AssignmentDocType.faktur_keluaran:
        return grade_faktur_keluaran(session, doc)
    if dtype == AssignmentDocType.faktur_masukan:
        return grade_faktur_masukan(session, doc)
    if dtype in SPT_DOC_TYPES:
        return grade_tax_return(session, doc)
    raise ValueError(f"Tipe dokumen tugas tidak dikenali: {doc_type}")
