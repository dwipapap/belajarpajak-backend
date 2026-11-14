"""Bukti potong simulasi PDF — layouts mirror the standard withholding slip format.

Two families share the kit in ``pdf_kit``:
* unifikasi style (bppu, bp23, bpnr, sp, cy): DPP / TARIF / PAJAK table, sections A-C.
* PPh 21/26 style (bp21, bp26, bpmp): PENGHASILAN BRUTO / DPP (%) / TARIF (%) table.

Branding rule: neutral simulator letterhead only; never real-institution names.
"""

from __future__ import annotations

from app.models.enums import SlipStatus, SlipTaxFacility, SlipTaxNature, SlipType
from app.models.slip import WithholdingSlip
from app.services import objek_pajak
from app.services.pdf_kit import (
    SIGN_VALID_NOTE,
    Col,
    Sheet,
    fmt_date,
    fmt_number,
    fmt_percent,
    fmt_period,
    or_dash,
)

_CODE = {
    "bppu": "BPPU",
    "bp23": "BPPU",
    "bp21": "BP21",
    "bp26": "BP26",
    "bpmp": "BPMP",
    "bpnr": "BPNR",
    "sp": "BPSS",
    "cy": "BPCY",
}

_UNIFIKASI_TITLE = {
    "bppu": ["BUKTI PEMOTONGAN DAN/ATAU PEMUNGUTAN PPh", "UNIFIKASI BERFORMAT STANDAR"],
    "bp23": ["BUKTI PEMOTONGAN DAN/ATAU PEMUNGUTAN PPh", "UNIFIKASI BERFORMAT STANDAR"],
    "bpnr": ["BUKTI PEMOTONGAN PPh PASAL 26", "ATAS WAJIB PAJAK LUAR NEGERI"],
    "sp": ["BUKTI PENYETORAN SENDIRI PPh", "UNIFIKASI BERFORMAT STANDAR"],
    "cy": ["BUKTI PENYETORAN PPh UNIFIKASI", "SECARA DIGUNGGUNG"],
}
_P21_TITLE = {
    "bp21": [
        "BUKTI PEMOTONGAN",
        (
            "PAJAK PENGHASILAN PASAL 21 YANG TIDAK BERSIFAT FINAL DAN "
            "PAJAK PENGHASILAN PASAL 21 YANG BERSIFAT FINAL"
        ),
    ],
    "bp26": ["BUKTI PEMOTONGAN", "PAJAK PENGHASILAN PASAL 26"],
    "bpmp": [
        "BUKTI PEMOTONGAN BULANAN",
        "PAJAK PENGHASILAN PASAL 21 PEGAWAI TETAP",
    ],
}

_FACILITY = {
    SlipTaxFacility.none: "Tanpa Fasilitas",
    SlipTaxFacility.dtp: "PPh Ditanggung Pemerintah (DTP)",
    SlipTaxFacility.skb: "Surat Keterangan Bebas (SKB)",
    SlipTaxFacility.rate_0: "Tarif 0%",
}

_PPH_BY_PREFIX = {
    "21": "Pasal 21",
    "22": "Pasal 22",
    "23": "Pasal 23",
    "24": "Pasal 23",
    "26": "Pasal 26",
    "27": "Pasal 26",
    "28": "Pasal 4 ayat (2)",
}


def _nitku(npwp: str | None, nitku: str | None, name: str | None) -> str:
    ident = nitku or npwp
    if not ident:
        return "-"
    return f"{ident} - {name}" if name else ident


def _pph_kind(slip: WithholdingSlip) -> str:
    if slip.tax_type:
        return slip.tax_type
    return _PPH_BY_PREFIX.get((slip.tax_object_code or "")[:2], "-")


def _status(slip: WithholdingSlip) -> str:
    status = slip.status.value if hasattr(slip.status, "value") else str(slip.status)
    return "DIBATALKAN" if status == SlipStatus.invalid.value else "NORMAL"


def _sifat(slip: WithholdingSlip) -> str:
    return "FINAL" if slip.tax_nature == SlipTaxNature.final else "TIDAK FINAL"


def _catalog_name(slip: WithholdingSlip) -> str | None:
    obj = objek_pajak.get_object(slip.slip_type, slip.tax_object_code or "")
    return obj["nama"] if obj else None


def _object_name(slip: WithholdingSlip) -> str | None:
    return slip.tax_object_name or slip.income_type or _catalog_name(slip)


def render_slip_pdf(slip: WithholdingSlip, label: str, tenant_name: str | None = None) -> bytes:
    """Render the simulated bukti potong as PDF bytes."""
    stype = slip.slip_type.value if hasattr(slip.slip_type, "value") else str(slip.slip_type)
    p21 = stype in _P21_TITLE
    no_cp = stype in (SlipType.sp.value, SlipType.cy.value)
    number = slip.withholding_number or "-"
    sheet = Sheet(f"Bukti Potong Simulasi {number if number != '-' else label}")

    title = _P21_TITLE[stype] if p21 else _UNIFIKASI_TITLE.get(stype, _UNIFIKASI_TITLE["bppu"])
    sheet.header(title, _CODE.get(stype, label.upper()), tenant_name)
    if p21:
        sheet.info_strip(
            [
                ("NOMOR BUKTI PEMOTONGAN", number),
                ("MASA PAJAK", fmt_period(slip.tax_month, slip.tax_year)),
                ("SIFAT PEMOTONGAN", _sifat(slip)),
                ("STATUS BUKTI PEMOTONGAN", _status(slip)),
            ]
        )
    else:
        sheet.info_strip(
            [
                ("NOMOR", number),
                ("MASA PAJAK", fmt_period(slip.tax_month, slip.tax_year)),
                ("SIFAT PEMOTONGAN DAN/ATAU PEMUNGUTAN PPh", _sifat(slip)),
                ("STATUS BUKTI PEMOTONGAN / PEMUNGUTAN", _status(slip)),
            ]
        )

    # ---- A. identity of the recipient (or withholder itself for sp/cy)
    if no_cp or not slip.recipient_identity_number:
        a_npwp, a_name, a_nitku = (
            (slip.withholder_npwp, slip.withholder_name, slip.withholder_nitku)
            if no_cp
            else (None, None, None)
        )
    else:
        a_npwp, a_name, a_nitku = (
            slip.recipient_identity_number,
            slip.recipient_name,
            slip.recipient_nitku,
        )
    nitku_a = _nitku(a_npwp, a_nitku, a_name)
    if p21:
        sheet.section_bar("A. IDENTITAS PENERIMA PENGHASILAN")
        sheet.label_row("A.1", "NIK/NPWP", or_dash(a_npwp), 28, 160, 172)
        sheet.label_row("A.2", "Nama", or_dash(a_name), 28, 160, 172)
        sheet.label_row("A.3", "NITKU", nitku_a, 28, 160, 172)
        sheet.section_bar("B. PENGHASILAN YANG DIPOTONG")
        sheet.free_row("B.1", f"Jenis Fasilitas : {_FACILITY.get(slip.tax_facility, '-')}", 28)
        sheet.table(
            [
                Col("KODE OBJEK PAJAK", "B.2", 80, "center"),
                Col("OBJEK PAJAK", "B.3", 150),
                Col("PENGHASILAN BRUTO (Rp)", "B.4", 90, "right"),
                Col("DPP (%)", "B.5", 60, "center"),
                Col("TARIF (%)", "B.6", 60, "center"),
                Col("PPh DIPOTONG (Rp)", "B.7", 90, "right"),
            ],
            [
                [
                    or_dash(slip.tax_object_code),
                    or_dash(_object_name(slip)),
                    fmt_number(slip.gross_income),
                    fmt_percent(slip.dpp_rate_basis_points),
                    fmt_percent(slip.rate_basis_points),
                    fmt_number(slip.income_tax),
                ]
            ],
            x0=26,
        )
        sheet.gap(2)
        _doc_rows_p21(sheet, slip)
        sheet.gap(2)
        sheet.section_bar("C. IDENTITAS PEMOTONG PPh")
        lab = (28, 200, 212)
        sheet.label_row("C.1", "NPWP/NIK", or_dash(slip.withholder_npwp), *lab, pad=3)
        sheet.label_row(
            "C.2",
            "NITKU atau Nomor Identitas Subunit Organisasi",
            _nitku(slip.withholder_npwp, slip.withholder_nitku, slip.withholder_name),
            *lab,
            pad=3,
        )
        sheet.label_row("C.3", "Nama Pemotong", or_dash(slip.withholder_name), *lab, pad=3)
        stmt_first = (
            "Dengan ini saya menyatakan bahwa Bukti Pemotongan ini telah saya isi "
            "dengan benar dan telah saya tandatangani secara elektronik."
        )
    else:
        a_title = (
            "A. IDENTITAS WAJIB PAJAK YANG MELAKUKAN PENYETORAN"
            if no_cp
            else (
                "A. IDENTITAS WAJIB PAJAK YANG DIPOTONG DAN/ATAU DIPUNGUT PPh "
                "ATAU PENERIMA PENGHASILAN"
            )
        )
        sheet.section_bar(a_title)
        sheet.label_row("A.1", "NPWP / NIK", or_dash(a_npwp), 28, 160, 172)
        sheet.label_row("A.2", "NAMA", or_dash(a_name), 28, 160, 172)
        sheet.label_row(
            "A.3", "NOMOR IDENTITAS TEMPAT KEGIATAN USAHA (NITKU)", nitku_a, 28, 160, 172
        )
        sheet.section_bar("B. PEMOTONGAN DAN/ATAU PEMUNGUTAN PPh")
        sheet.free_row("B.1", f"Jenis Fasilitas : {_FACILITY.get(slip.tax_facility, '-')}", 28)
        sheet.free_row("B.2", f"Jenis PPh : {_pph_kind(slip)}", 28)
        sheet.table(
            [
                Col("KODE OBJEK PAJAK", "B.3", 95, "center"),
                Col("OBJEK PAJAK", "B.4", 215),
                Col("DPP (Rp)", "B.5", 80, "right"),
                Col("TARIF (%)", "B.6", 55, "center"),
                Col("PAJAK PENGHASILAN (Rp)", "B.7", 100, "right"),
            ],
            [
                [
                    or_dash(slip.tax_object_code),
                    or_dash(_object_name(slip)),
                    fmt_number(slip.dpp),
                    fmt_percent(slip.rate_basis_points),
                    fmt_number(slip.income_tax),
                ]
            ],
            x0=26,
        )
        sheet.gap(2)
        lab = (28, 200, 212)
        _doc_rows_unif(sheet, slip)
        sheet.free_row("B.10", "Untuk Instansi Pemerintah, Pembayaran PPh Menggunakan :", 28)
        sheet.label_row("B.11", "Nomor SP2D", " ", 168, 290, 300, pad=5)
        sheet.section_bar("C. IDENTITAS PEMOTONG DAN/ATAU PEMUNGUT PPh")
        lab = (28, 245, 257)
        sheet.label_row("C.1", "NPWP / NIK", or_dash(slip.withholder_npwp), *lab, pad=3)
        sheet.label_row(
            "C.2",
            "NOMOR IDENTITAS TEMPAT KEGIATAN USAHA (NITKU) / SUBUNIT ORGANISASI",
            _nitku(slip.withholder_npwp, slip.withholder_nitku, slip.withholder_name),
            *lab,
            pad=3,
        )
        sheet.label_row(
            "C.3", "NAMA PEMOTONG DAN/ATAU PEMUNGUT PPh", or_dash(slip.withholder_name), *lab, pad=3
        )
        stmt_first = (
            "Dengan ini saya menyatakan bahwa Bukti Pemotongan dan/atau Pemungutan "
            "PPh Unifikasi telah saya isi dengan benar dan telah saya tandatangani "
            "secara elektronik."
        )

    signed = slip.issued_at
    lab_title4 = "Tanggal" if p21 else "TANGGAL"
    lab_title5 = "Nama Penandatangan" if p21 else "NAMA PENANDATANGAN"
    sheet.label_row("C.4", lab_title4, fmt_date(signed), *lab, pad=3)
    sheet.label_row("C.5", lab_title5, or_dash(slip.signer_name), *lab, pad=3)
    sheet.label_row(
        "C.6", "Pernyataan" if p21 else "PERNYATAAN WAJIB PAJAK", stmt_first, *lab, pad=3
    )
    sheet.signature(f"BPESIM:{number}:{label}", [SIGN_VALID_NOTE], text_x=lab[2])
    sheet.footer()
    return sheet.finish()


def _doc_rows_p21(sheet: Sheet, slip: WithholdingSlip) -> None:
    """B.8/B.9 on the PPh 21/26 slip (columns inside the row, like the portal)."""
    _doc_pair(
        sheet, slip, ("B.8", "Dokumen Referensi"), "B.9", 28, 100, 200, 235, 355, "Tanggal Dokumen"
    )


def _doc_rows_unif(sheet: Sheet, slip: WithholdingSlip) -> None:
    _doc_pair(
        sheet,
        slip,
        (
            "B.8",
            (
                "Dokumen Dasar Bukti Pemotongan dan/atau Pemungutan PPh Unifikasi "
                "atau Dasar Pemberian Fasilitas"
            ),
        ),
        "B.9",
        28,
        100,
        200,
        235,
        355,
        "Tanggal",
    )


def _doc_pair(
    sheet, slip, first, code2, x_code, x_label, x_sub, x_colon, x_val, date_label
) -> None:
    """Draw 'Jenis Dokumen : x   Tanggal : d' then 'Nomor Dokumen : n' below."""
    left = sheet.left
    top = sheet.y - 4
    long_label = len(first[1]) > 30
    lw = 105 if long_label else 70
    sub_x = x_sub - 20 if long_label else x_sub - 40
    lines = sheet.wrap(first[1], lw)
    lead = 8.5 * 1.3
    sheet.text(left + x_code, top - 8.5, first[0])
    for i, ln in enumerate(lines):
        sheet.text(left + 54, top - 8.5 - i * lead, ln)
    sheet.text(left + sub_x, top - 8.5, "Jenis Dokumen")
    sheet.text(left + sub_x + 75, top - 8.5, ":")
    sheet.text(left + sub_x + 83, top - 8.5, or_dash(slip.document_type)[:24])
    sheet.text(left + 365, top - 8.5, f"{date_label} : {fmt_date(slip.document_date)}")
    y2 = top - max(len(lines), 1) * lead - 4
    sheet.text(left + x_code, y2 - 8.5, code2)
    sheet.text(left + sub_x, y2 - 8.5, "Nomor Dokumen")
    sheet.text(left + sub_x + 75, y2 - 8.5, ":")
    sheet.text(left + sub_x + 83, y2 - 8.5, or_dash(slip.document_number))
    sheet.y = y2 - 8.5 - 5
