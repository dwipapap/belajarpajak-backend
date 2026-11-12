"""SPT Masa PPh Pasal 21/26 and PPh Unifikasi document layouts (Induk)."""

from __future__ import annotations

from collections.abc import Sequence

from app.models.tax_return import TaxReturn, TaxReturnLine
from app.services import kjs
from app.services.pdf_kit import BOLD, CAPTION, FONT, GRAY_HEAD, GRAY_SUB, Sheet, fmt_number
from app.services.spt_pdf_common import (
    DISABLED,
    LINE,
    checkbox,
    draw_header,
    draw_qr,
    footer,
    gray_bar,
    is_kuasa,
    masa_label,
    new_sheet,
    qr_payload,
    rich_text,
    sign_date,
    signed_caption,
    signer_npwp,
    status_label,
    strip,
)

IDENTITY_STATEMENT = (
    "Dengan menyadari sepenuhnya atas segala akibatnya termasuk sanksi-sanksi sesuai dengan "
    "ketentuan yang berlaku, saya menyatakan bahwa apa yang telah saya beritahukan di atas "
    "beserta lampiran-lampirannya adalah benar, lengkap dan jelas."
)


def _sum(lines: Sequence[TaxReturnLine], section: str, attr: str) -> int:
    return sum(getattr(line, attr) for line in lines if line.section == section)


def _identity(sheet: Sheet, tax_return: TaxReturn, taxpayer_name: str, labels, code_w: float):
    """A.1-A.4 rows; ``labels`` = (npwp, nama, alamat, telepon) captions."""
    rows = [
        ("A.1", labels[0], signer_npwp(tax_return)),
        ("A.2", labels[1], taxpayer_name),
        ("A.3", labels[2], "-"),
        ("A.4", labels[3], "-"),
    ]
    for code, label, value in rows:
        sheet.label_row(code, label, value, code_w, 130, 144, pad=2.5)


# ------------------------------------------------------------------ PPh 21/26
_E21_NAMES = {
    "pasal_21": ("21", "PPh Pasal 21"),
    "pasal_26": ("26", "PPh Pasal 26"),
}


def _p21_table(sheet: Sheet, title: str, codes: tuple[str, str, str, str], rows) -> None:
    """Sub bar + NO/URAIAN/KAP-KJS/JUMLAH header + rows. ``rows`` = (no, text, kjs, amount)."""
    gray_bar(sheet, title, h=13, fill=GRAY_SUB)
    edges = _p21_edges(sheet)
    top = sheet.y
    head_h = 24
    sheet.c.setFillColor(GRAY_HEAD)
    sheet.c.rect(sheet.left, top - head_h, sheet.cw, head_h, stroke=0, fill=1)
    titles = ["NO", "URAIAN", "KAP-KJS", "JUMLAH (Rp)"]
    for i, (t, code) in enumerate(zip(titles, codes, strict=True)):
        mid = (edges[i] + edges[i + 1]) / 2
        sheet.text(mid, top - 10, t, BOLD, 8, align="center")
        sheet.text(mid, top - head_h + 3.5, code, BOLD, 6, align="center")
    y = top - head_h
    for no, text, kjs_code, amount in rows:
        by = y - 10
        sheet.text(edges[0] + 8, by, no, FONT, 8, align="center")
        # description may run under the (empty) KAP-KJS column
        max_w = (edges[3] if not kjs_code else edges[2]) - edges[1] - 8
        rich_text(sheet, edges[1] + 4, by, text, 8, max_w)
        if kjs_code:
            sheet.text((edges[2] + edges[3]) / 2, by, kjs_code, FONT, 8, align="center")
        sheet.text(sheet.right - 4, by, amount, FONT, 8, align="right")
        y -= 14
    sheet.y = y - 2


def _p21_edges(sheet: Sheet) -> list[float]:
    cw = sheet.cw
    out = [sheet.left]
    for f in (0.052, 0.509, 0.216):
        out.append(out[-1] + cw * f)
    out.append(sheet.right)
    return out


def _p21_section(sheet: Sheet, tax_return: TaxReturn, lines, key: str, letter: str) -> None:
    num, name = _E21_NAMES[key]
    codes = kjs.KJS_PPH_21_26[key]
    kjs_code = codes[0]
    dipotong = _sum(lines, key, "pemotongan")
    dibetulkan = _sum(lines, key, "dibayar_dari_spt_dibetulkan")
    ditanggung = _sum(lines, key, "ditanggung_pemerintah")
    kurang = dipotong  # 1 - 2 - 3 with 2 and 3 not tracked
    karena = kurang - dibetulkan if tax_return.correction_no > 0 else 0
    sheet.section_bar(f"{letter}. PAJAK PENGHASILAN PASAL {num}")
    p = f"{letter}."
    i_codes = (f"{p}1", f"{p}2", f"{p}3", f"{p}4")
    ii_codes = (f"{p}5", f"{p}6", f"{p}7", f"{p}8")
    _p21_table(
        sheet,
        f"I. PAJAK PENGHASILAN PASAL {num} YANG DILAKUKAN PEMOTONGAN",
        i_codes,
        [
            ("1.", f"{name} yang Dipotong", kjs_code, fmt_number(dipotong)),
            (
                "2.",
                f"Kelebihan Penyetoran {name} dari Masa Pajak Sebelumnya",
                "",
                "0",
            ),
            (
                "3.",
                f"Pembayaran {name} dengan SP2D \x01(Hanya untuk Instansi Pemerintah)",
                "",
                "0",
            ),
            (
                "4.",
                f"{name} yang Kurang (Lebih) Disetor (1-2-3) "
                "\x01(Apabila ada lebih setor akan dikompensasikan)",
                "",
                fmt_number(kurang),
            ),
            (
                "5.",
                f"{name} yang Kurang (Lebih) Disetor pada SPT yang Dibetulkan",
                "",
                fmt_number(dibetulkan),
            ),
            (
                "6.",
                f"{name} yang Kurang (Lebih) Disetor Karena Pembetulan (4-5) "
                "\x01(Apabila ada Lebih Setor akan dikompensasikan)",
                "",
                fmt_number(karena),
            ),
        ],
    )
    _p21_table(
        sheet,
        f"II. PAJAK PENGHASILAN PASAL {num} DITANGGUNG PEMERINTAH",
        ii_codes,
        [("1.", f"{name} Ditanggung Pemerintah", kjs_code, fmt_number(ditanggung))],
    )


def render_pph21_26(
    tax_return: TaxReturn,
    lines: Sequence[TaxReturnLine],
    tenant_name: str | None,
    taxpayer_name: str,
) -> bytes:
    sheet = new_sheet(f"SPT Masa PPh 21/26 {tax_return.tax_month:02d}-{tax_return.tax_year}")
    draw_header(
        sheet,
        [
            ("SURAT PEMBERITAHUAN (SPT) MASA", 9),
            ("PAJAK PENGHASILAN (PPh)", 9),
            ("PASAL 21 DAN/ATAU PASAL 26", 9),
        ],
        [("INDUK", 24)],
        tenant_name,
    )
    sheet.gap(-4)
    strip(
        sheet,
        [
            ("MASA PAJAK", str(tax_return.tax_month)),
            ("TAHUN PAJAK", str(tax_return.tax_year)),
            ("STATUS", status_label(tax_return)),
        ],
    )
    sheet.section_bar("A. IDENTITAS PEMOTONG")
    _identity(sheet, tax_return, taxpayer_name, ("NPWP/NIK", "Nama", "Alamat", "No. Telepon"), 28)
    sheet.gap(2)
    _p21_section(sheet, tax_return, lines, "pasal_21", "B")
    sheet.gap(10)
    _p21_section(sheet, tax_return, lines, "pasal_26", "C")
    sheet.gap(8)
    _p21_sign_box(sheet, tax_return)
    footer(sheet)
    return sheet.finish()


def _p21_sign_box(sheet: Sheet, tax_return: TaxReturn) -> None:
    h = 118
    top = sheet.y
    sheet.c.setStrokeColor(LINE)
    sheet.c.setLineWidth(0.6)
    sheet.c.rect(sheet.left, top - h, sheet.cw, h, stroke=1, fill=0)
    sheet.text(
        sheet.left + sheet.cw / 2,
        top - 13,
        "D. PERNYATAAN DAN TANDA TANGAN PEMOTONG",
        BOLD,
        8,
        align="center",
    )
    kuasa = is_kuasa(tax_return)
    x = sheet.left + 3
    sheet.text(x, top - 33, "D.1")
    checkbox(sheet, sheet.left + 40, top - 37, not kuasa, 11)
    sheet.text(sheet.left + 62, top - 33, "Wajib Pajak")
    sheet.text(x, top - 50, "D.2")
    checkbox(sheet, sheet.left + 40, top - 54, kuasa, 11)
    sheet.text(sheet.left + 62, top - 50, "Kuasa")
    sheet.text(x, top - 67, "D.3")
    sheet.text(sheet.left + 40, top - 67, f"Nama : {tax_return.signer_name or '-'}")
    sheet.text(x, top - 80, "D.4")
    sheet.text(sheet.left + 40, top - 80, f"Tanggal : {sign_date(tax_return)}")
    sheet.text(x, top - 93, "D.5")
    sheet.text(sheet.left + 40, top - 93, "Pernyataan Wajib Pajak")
    sheet.paragraph(sheet.left + 40, top - 94, IDENTITY_STATEMENT, sheet.cw - 60, size=8)
    qr_size = 50
    draw_qr(sheet, sheet.right - 175, top - 26, qr_size, qr_payload(tax_return, "pph2126"))
    signed_caption(sheet, sheet.right - 5, top - 26 - qr_size - 2)
    sheet.y = top - h


# ------------------------------------------------------------------ Unifikasi
_UNIF_NAMES = {
    "pasal_4_2": "PPh Pasal 4 ayat 2",
    "pasal_15": "PPh Pasal 15",
    "pasal_22": "PPh Pasal 22",
    "pasal_23": "PPh Pasal 23",
    "pasal_26": "PPh Pasal 26",
}


def _unif_rows(lines: Sequence[TaxReturnLine]):
    """Yield (kind, label, setor, potong, tanggung, harus, dibetulkan, karena)."""
    by_code = {(line.section, line.code): line for line in lines}
    for section, codes in kjs.KJS_PPH_UNIFIKASI.items():
        known = list(codes)
        # lines stored under a KJS outside the catalog still belong to the pasal
        known += sorted(
            line.code for line in lines if line.section == section and line.code not in codes
        )
        group = []
        for code in known:
            line = by_code.get((section, code))
            setor = line.setor_sendiri if line else 0
            potong = line.pemotongan if line else 0
            tanggung = line.ditanggung_pemerintah if line else 0
            dibetulkan = line.dibayar_dari_spt_dibetulkan if line else 0
            group.append((code, setor, potong, tanggung, dibetulkan))
        yield section, group


def render_unifikasi(
    tax_return: TaxReturn,
    lines: Sequence[TaxReturnLine],
    tenant_name: str | None,
    taxpayer_name: str,
) -> bytes:
    sheet = new_sheet(f"SPT Masa PPh Unifikasi {tax_return.tax_month:02d}-{tax_return.tax_year}")
    draw_header(
        sheet, [("SURAT PEMBERITAHUAN MASA PPH UNIFIKASI", 8.5)], [("INDUK", 24)], tenant_name
    )
    sheet.gap(-4)
    strip(
        sheet,
        [("MASA PAJAK", masa_label(tax_return)), ("STATUS SPT", status_label(tax_return))],
    )
    sheet.section_bar("A. IDENTITAS PEMOTONG DAN/ATAU PEMUNGUT PPH")
    _identity(sheet, tax_return, taxpayer_name, ("NPWP/NIK", "NAMA", "ALAMAT", "NO. TELEPON"), 28)
    sheet.gap(2)
    sheet.section_bar("B. PAJAK PENGHASILAN")
    _unif_table(sheet, tax_return, lines)
    sheet.gap(0)
    _unif_sign_box(sheet, tax_return)
    footer(sheet)
    return sheet.finish()


def _unif_table(sheet: Sheet, tax_return: TaxReturn, lines: Sequence[TaxReturnLine]) -> None:
    cw = sheet.cw
    fr = [0.039, 0.151, 0.133, 0.133, 0.136, 0.132, 0.131, 0.145]
    edges = [sheet.left]
    for f in fr:
        edges.append(edges[-1] + cw * f)
    edges[-1] = sheet.right
    top = sheet.y
    head_h = 62
    sheet.c.setFillColor(GRAY_HEAD)
    sheet.c.rect(sheet.left, top - head_h, cw, head_h, stroke=0, fill=1)
    titles = {
        0: "NO",
        1: "DETIL",
        4: "PPH YANG\nDITANGGUNG\nPEMERINTAH",
        5: "JUMLAH PPH\nYANG HARUS\nDISETOR",
        6: "JUMLAH PPH\nYANG TELAH\nDISETOR PADA\nSPT YANG\nDIBETULKAN",
        7: "PPH KURANG\n(LEBIH) SETOR\nKARENA\nPEMBETULAN",
    }
    for i, t in titles.items():
        mid = (edges[i] + edges[i + 1]) / 2
        for j, line in enumerate(t.split("\n")):
            sheet.text(mid, top - 10 - j * 8.5, line, BOLD, 7.5, align="center")
    gmid = (edges[2] + edges[4]) / 2
    sheet.text(gmid, top - 10, "PAJAK PENGHASILAN", BOLD, 7.5, align="center")
    for i, t in ((2, "SETOR SENDIRI"), (3, "PEMOTONGAN\nDAN/ATAU\nPEMUNGUTAN\nPPH")):
        mid = (edges[i] + edges[i + 1]) / 2
        for j, line in enumerate(t.split("\n")):
            sheet.text(mid, top - 22 - j * 8.5, line, BOLD, 7.5, align="center")
    for i in range(8):
        mid = (edges[i] + edges[i + 1]) / 2
        sheet.text(mid, top - head_h + 3, f"B.{i + 1}", BOLD, 6, align="center")
    y = top - head_h
    rh = 16.5
    corr = tax_return.correction_no > 0
    totals = [0, 0, 0, 0, 0, 0]
    for idx, (section, group) in enumerate(_unif_rows(lines), start=1):
        sums = [sum(g[k] for g in group) for k in (1, 2, 3, 4)]
        harus = sums[0] + sums[1]
        karena = harus - sums[3] if corr else 0
        values = [sums[0], sums[1], sums[2], harus, sums[3], karena]
        for k, v in enumerate(values):
            totals[k] += v
        by = y - 11.5
        sheet.text(edges[0] + (edges[1] - edges[0]) / 2, by, str(idx), FONT, 8, align="center")
        sheet.text(edges[1] + 8, by, _UNIF_NAMES[section], FONT, 8)
        for k, v in enumerate(values):
            sheet.text(
                edges[k + 2 + 0] + (edges[k + 3] - edges[k + 2]) - 4,
                by,
                fmt_number(v),
                FONT,
                8,
                align="right",
            )
        y -= rh
        for code, setor, potong, _tanggung, dibetulkan in group:
            by = y - 11.5
            harus_k = setor + potong
            karena_k = harus_k - dibetulkan if corr else 0
            sheet.text(edges[1] + 8, by, f"KJS : {code}", FONT, 8)
            cells = [setor, potong, None, harus_k, dibetulkan, karena_k]
            for k, v in enumerate(cells):
                x0, x1 = edges[k + 2], edges[k + 3]
                if v is None:
                    sheet.c.setFillColor(DISABLED)
                    sheet.c.rect(x0, y - rh, x1 - x0, rh, stroke=0, fill=1)
                    continue
                sheet.text(x1 - 4, by, fmt_number(v), FONT, 8, align="right")
            y -= rh
        sheet.c.setStrokeColor(LINE)
        sheet.c.setLineWidth(0.6)
        sheet.c.line(edges[0], y, sheet.right, y)
    # total row
    by = y - 11.5
    sheet.text(edges[0] + (edges[1] - edges[0]) / 2, by, "6", BOLD, 8, align="center")
    sheet.text(edges[1] + 8, by, "Total Pajak", BOLD, 8)
    sheet.text(edges[1] + 8, by - 9.5, "Penghasilan", BOLD, 8)
    for k in range(5):
        sheet.text(edges[k + 3] - 4, by, fmt_number(totals[k]), FONT, 8, align="right")
    sheet.c.setFillColor(DISABLED)
    sheet.c.rect(edges[7], y - 22, edges[8] - edges[7], 22, stroke=0, fill=1)
    y -= 22
    sheet.y = y


def _unif_sign_box(sheet: Sheet, tax_return: TaxReturn) -> None:
    h = 124
    top = sheet.y - 6
    sheet.c.setStrokeColor(LINE)
    sheet.c.setLineWidth(0.6)
    sheet.c.rect(sheet.left, top - h, sheet.cw, h, stroke=1, fill=0)
    sheet.text(
        sheet.left + sheet.cw / 2, top - 13, "PERNYATAAN DAN TANDA TANGAN", BOLD, 8, align="center"
    )
    x = sheet.left + 4
    sheet.text(x, top - 31, "C.1")
    sheet.text(
        sheet.left + 40, top - 31, "Kuasa Wajib Pajak" if is_kuasa(tax_return) else "Wajib Pajak"
    )
    sheet.text(sheet.right - 175, top - 31, "C.5 QR Code :")
    sheet.text(x, top - 45, "C.2")
    sheet.text(sheet.left + 40, top - 45, "NAMA")
    sheet.text(sheet.left + 100, top - 45, f": {tax_return.signer_name or '-'}")
    sheet.text(x, top - 58, "C.3")
    sheet.text(sheet.left + 40, top - 58, "TANGGAL")
    sheet.text(sheet.left + 100, top - 58, f": {sign_date(tax_return)}")
    sheet.text(x, top - 71, "C.4")
    sheet.text(sheet.left + 40, top - 71, "PERNYATAAN WAJIB PAJAK")
    sheet.paragraph(
        sheet.left + 40,
        top - 72,
        "Dengan menyadari sepenuhnya akan segala akibatnya termasuk sanksi-sanksi sesuai "
        "dengan ketentuan perundang-undangan yang berlaku, saya menyatakan bahwa apa yang "
        "telah saya beritahukan di atas beserta lampiran-lampirannya adalah benar, lengkap "
        "dan jelas",
        sheet.cw - 225,
        size=8,
    )
    draw_qr(sheet, sheet.right - 175, top - 36, 50, qr_payload(tax_return, "unifikasi"))
    sheet.text(sheet.right - 175, top - 36 - 50 - 9, "Ditandatangani secara", FONT, 8, CAPTION)
    sheet.text(sheet.right - 175, top - 36 - 50 - 18, "elektronik", FONT, 8, CAPTION)
    sheet.y = top - h
