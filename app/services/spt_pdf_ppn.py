"""SPT Masa PPN document layout (Induk, two pages)."""

from __future__ import annotations

from collections.abc import Sequence

from reportlab.lib.colors import white

from app.models.enums import LbTreatment
from app.models.tax_return import TaxReturn, TaxReturnLine
from app.services.pdf_kit import BOLD, FONT, GRAY_HEAD, GRAY_SUB, Sheet, fmt_number, or_dash
from app.services.spt_pdf_common import (
    DISABLED,
    acct,
    checkbox,
    draw_header,
    draw_qr,
    due_date_text,
    footer,
    is_kuasa,
    masa_label,
    new_sheet,
    qr_payload,
    sign_date,
    signed_caption,
    signer_npwp,
    status_label,
)

PX = 0.72
GRAY = "G"  # disabled cell marker
SIZE = 6.6
LEAD = 8.5

# column geometry in reference pixels (100 dpi) -> points
BOUNDS = [393 * PX, 516 * PX, 614 * PX, 714 * PX, 808 * PX]
RIGHTS = [502 * PX, 612 * PX, 712 * PX, 806 * PX]
HEAD_CENTERS = [455 * PX, 563 * PX, 689 * PX, 779 * PX]
LABEL_RIGHT = 391 * PX

HEAD_I = ["HARGA JUAL/", "PENGGANTIAN/ NILAI", "EKSPOR/DPP", "(Rupiah)"]
HEAD_II = ["HARGA JUAL/", "PENGGANTIAN/ NILAI", "IMPOR/DPP", "(Rupiah)"]


def _total(lines: Sequence[TaxReturnLine], section: str, attr: str) -> int:
    return sum(getattr(line, attr) for line in lines if line.section == section)


# ------------------------------------------------------------------ primitives
def _page_header(sheet: Sheet, tax_return: TaxReturn, tenant_name: str | None, page: int) -> None:
    draw_header(
        sheet,
        [
            ("SURAT PEMBERITAHUAN MASA", 9),
            ("PAJAK PERTAMBAHAN NILAI (SPT MASA PPN)", 9),
            ("BAGI PENGUSAHA KENA PAJAK", 8),
        ],
        [("INDUK", 12), (f"Halaman {page}", 12)],
        tenant_name,
    )
    sheet.gap(-6)
    top = sheet.y
    h = 31
    sheet.c.setFillColor(GRAY_HEAD)
    sheet.c.rect(sheet.left, top - h, sheet.cw, h, stroke=0, fill=1)

    def box(x0, x1, value):
        sheet.c.setFillColor(white)
        sheet.c.rect(x0, top - h + 3, x1 - x0, 13, stroke=0, fill=1)
        sheet.text((x0 + x1) / 2, top - h + 7, value, FONT, 8, align="center")

    third = sheet.cw / 3
    x1, x2 = sheet.left + third, sheet.left + 2 * third
    sheet.text(sheet.left + third / 2, top - 11, "Masa Pajak", BOLD, 8, align="center")
    sheet.text(x1 + third / 2, top - 11, "Tahun Buku", BOLD, 8, align="center")
    sheet.text(x2 + third / 2, top - 11, "Normal/Pembetulan", BOLD, 8, align="center")
    box(sheet.left + 35, x1 - 35, masa_label(tax_return))
    box(x1 + 25, x1 + 62, "1")
    sheet.text(x1 + 75, top - h + 7, "s.d", BOLD, 8)
    box(x1 + 95, x1 + 135, "12")
    box(x2 + 35, x2 + third - 35, status_label(tax_return))
    sheet.y = top - h - 2


def _identity_block(sheet: Sheet, tax_return: TaxReturn, taxpayer_name: str) -> None:
    top = sheet.y
    h = 46
    sheet.c.setFillColor(GRAY_HEAD)
    sheet.c.rect(sheet.left, top - h, sheet.cw, h, stroke=0, fill=1)
    left_label, left_colon, left_val = sheet.left + 3, sheet.left + 70, sheet.left + 78
    right_label, right_colon, right_val = sheet.left + 385, sheet.left + 425, sheet.left + 432
    sheet.text(left_label, top - 12, "NAMA PKP", BOLD, 7.5)
    sheet.text(left_colon, top - 12, ":", BOLD, 7.5)
    sheet.text(left_val, top - 12, taxpayer_name[:60], FONT, 7.5)
    sheet.text(right_label, top - 12, "NPWP", BOLD, 7.5)
    sheet.text(right_colon, top - 12, ":", BOLD, 7.5)
    sheet.text(right_val, top - 12, signer_npwp(tax_return), FONT, 7.5)
    sheet.text(left_label, top - 25, "ALAMAT", BOLD, 7.5)
    sheet.text(left_colon, top - 25, ":", BOLD, 7.5)
    sheet.text(left_val, top - 25, "-", FONT, 7.5)
    sheet.text(right_label, top - 25, "KLU", BOLD, 7.5)
    sheet.text(right_colon, top - 25, ":", BOLD, 7.5)
    sheet.text(right_val, top - 25, "-", FONT, 7.5)
    sheet.text(left_label, top - 38, "TELEPON", BOLD, 7.5)
    sheet.text(left_colon, top - 38, ":", BOLD, 7.5)
    sheet.text(left_val, top - 38, "-", FONT, 7.5)
    sheet.text(right_label, top - 38, "HP", BOLD, 7.5)
    sheet.text(right_colon, top - 38, ":", BOLD, 7.5)
    sheet.text(right_val, top - 38, "-", FONT, 7.5)
    sheet.y = top - h


def _section_head(
    sheet: Sheet,
    title: str,
    cols: dict[int, list[str]],
    height: float | None = None,
    wrap_w: float | None = None,
) -> None:
    """Gray band: title at the left, column captions (index -> lines) on the right."""
    n = max((len(v) for v in cols.values()), default=1)
    h = height or max(n * 8.8 + 4, 16)
    top = sheet.y
    sheet.c.setFillColor(GRAY_SUB)
    sheet.c.rect(sheet.left, top - h, sheet.cw, h, stroke=0, fill=1)
    ty = top - 11
    for line in sheet.wrap(title, wrap_w or (BOUNDS[0] - sheet.left - 10), BOLD, 7.5):
        sheet.text(sheet.left + 3, ty, line, BOLD, 7.5)
        ty -= 9
    for idx, lines in cols.items():
        cx = HEAD_CENTERS[idx]
        for j, line in enumerate(lines):
            sheet.text(cx, top - 10 - j * 8.8, line, BOLD, 7.5, align="center")
    sheet.y = top - h


def _row(
    sheet: Sheet,
    label: str,
    cells: list | None = None,
    code: str = "",
    level: int = 0,
    pad: float = 1.7,
    bold: bool = False,
) -> None:
    """Label (wrapped) at the left, four right-aligned cells; GRAY draws a disabled box."""
    code_x = 65 * PX if level == 0 else 86 * PX
    text_x = 78 * PX if level == 0 else 102 * PX
    if level == 2:
        code_x, text_x = 86 * PX, 86 * PX
    width = LABEL_RIGHT - text_x
    lines = sheet.wrap(label, width, FONT, SIZE)
    h = max(len(lines), 1) * LEAD + pad * 2
    top = sheet.y
    for i, (x0, x1, cell) in enumerate(
        zip(BOUNDS[:-1], BOUNDS[1:], cells or [None] * 4, strict=True)
    ):
        if cell == GRAY:
            sheet.c.setFillColor(DISABLED)
            sheet.c.rect(x0 + 1, top - h + 1, x1 - x0 - 1, h - 2, stroke=0, fill=1)
        elif cell is not None:
            sheet.text(
                RIGHTS[i], top - pad - SIZE, cell, BOLD if bold else FONT, SIZE, align="right"
            )
    if code:
        sheet.text(code_x, top - pad - SIZE, code, FONT, SIZE)
    for j, line in enumerate(lines):
        sheet.text(text_x if code else code_x, top - pad - SIZE - j * LEAD, line, FONT, SIZE)
    sheet.y = top - h


def _check_row(sheet: Sheet, code: str, label: str) -> None:
    """'F. [ ] diminta pengembalian ...' row with an unchecked box."""
    top = sheet.y
    sheet.text(65 * PX, top - 2 - SIZE, code, FONT, SIZE)
    checkbox(sheet, 82 * PX, top - 11, False, 8)
    sheet.text(102 * PX, top - 2 - SIZE, label, FONT, SIZE)
    sheet.y = top - 13


def _single_row(sheet: Sheet, label: str, value: str, code: str = "", right: float = 3) -> None:
    """Section III style: label left, one value right-aligned under the PPN column."""
    _row(sheet, label, [None, None, value, None], code=code)


def _num(value: int) -> str:
    return fmt_number(value)


# ------------------------------------------------------------------- page 1
def _page1(
    sheet: Sheet,
    tax_return: TaxReturn,
    lines: Sequence[TaxReturnLine],
    tenant_name: str | None,
    taxpayer_name: str,
) -> None:
    _page_header(sheet, tax_return, tenant_name, 1)
    _identity_block(sheet, tax_return, taxpayer_name)
    sheet.gap(0)

    def sec(section: str):
        return (
            _total(lines, section, "dpp"),
            _total(lines, section, "dpp_nilai_lain"),
            _total(lines, section, "ppn"),
            _total(lines, section, "ppnbm"),
        )

    # ---- I. Penyerahan
    _section_head(
        sheet,
        "I. PENYERAHAN BARANG DAN JASA",
        {
            0: HEAD_I,
            1: ["DPP NILAI LAIN/ DPP", "(Rupiah)"],
            2: ["PPN", "(Rupiah)"],
            3: ["PPnBM", "(Rupiah)"],
        },
    )
    sheet.gap(3)
    _row(sheet, "Penyerahan BKP/JKP yang terutang PPN", code="A.")
    a = {k: sec(f"I_A{k}") for k in range(1, 10)}
    labels = {
        1: "Ekspor BKP/BKP Tidak Berwujud/JKP",
        2: (
            "Penyerahan yang PPN atau PPN dan PPnBM-nya harus dipungut sendiri dengan DPP "
            "Nilai Lain atau Besaran Tertentu (dengan Faktur Pajak Kode 04 dan 05)"
        ),
        3: (
            "Penyerahan yang PPN atau PPN dan PPnBM-nya harus dipungut sendiri kepada turis "
            "sesuai dengan Pasal 16E UU PPN (dengan Faktur Pajak Kode 06)"
        ),
        4: (
            "Penyerahan yang PPN atau PPN dan PPnBM-nya harus dipungut sendiri lainnya "
            "(dengan Faktur Pajak Kode 01, 09 dan 10)"
        ),
        5: (
            "Penyerahan yang PPN atau PPN dan PPnBM-nya harus dipungut sendiri dengan Faktur "
            "Pajak yang dilaporkan secara digunggung"
        ),
        6: (
            "Penyerahan yang PPN atau PPN dan PPnBM-nya harus dipungut oleh Pemungut PPN "
            "(dengan Faktur Pajak Kode 02 dan 03)"
        ),
        7: (
            "Penyerahan yang mendapat fasilitas PPN atau PPnBM Tidak Dipungut "
            "(dengan Faktur Pajak Kode 07)"
        ),
        8: (
            "Penyerahan yang mendapat fasilitas PPN atau PPnBM Dibebaskan "
            "(dengan Faktur Pajak Kode 08)"
        ),
        9: (
            "Penyerahan yang mendapat fasilitas PPN atau PPnBM dengan Faktur Pajak yang "
            "dilaporkan secara digunggung"
        ),
    }
    # The simulator books export (09/10) under I.A.1 and everything else by faktur code.
    # Row 5 and 9 (digunggung) are not tracked -> zeros.
    vals = {k: a[k] for k in (1, 2, 3, 4, 6, 7, 8)}
    vals[5] = (0, 0, 0, 0)
    vals[9] = (0, 0, 0, 0)
    for k in range(1, 10):
        d, dl, p, bm = vals[k]
        if k == 1:
            cells = [_num(d), GRAY, GRAY, GRAY]
        elif k == 4:
            cells = [_num(d), GRAY, _num(p), _num(bm)]
        else:
            cells = [_num(d), _num(dl), _num(p), _num(bm)]
        _row(sheet, labels[k], cells, code=f"{k}.", level=1)
    sum_d = sum(v[0] for v in vals.values())
    sum_p = sum(v[2] for v in vals.values())
    sum_bm = sum(v[3] for v in vals.values())
    _row(
        sheet,
        "Jumlah (I.A.1 + I.A.2 + I.A.3 + I.A.4 + I.A.5 + I.A.6 + I.A.7 + I.A.8 + I.A.9)",
        [_num(sum_d), GRAY, _num(sum_p), _num(sum_bm)],
        level=2,
    )
    _row(
        sheet,
        "Penyerahan barang/jasa yang tidak terutang PPN",
        ["0", GRAY, GRAY, GRAY],
        code="B.",
    )
    _row(
        sheet,
        "Jumlah seluruh penyerahan barang dan jasa (I.A + I.B)",
        [_num(sum_d), GRAY, GRAY, GRAY],
        code="C.",
    )
    sheet.gap(1)

    # ---- II. Perolehan
    _section_head(
        sheet,
        "II. PEROLEHAN BARANG DAN JASA",
        {
            0: HEAD_II,
            1: ["DPP NILAI LAIN/ DPP", "(Rupiah)"],
            2: ["PPN", "(Rupiah)"],
            3: ["PPnBM", "(Rupiah)"],
        },
    )
    sheet.gap(3)
    ii = {k: sec(f"II_{k}") for k in "BCD"}
    _row(
        sheet,
        (
            "Impor BKP, Pemanfaatan BKP Tidak Berwujud dan/atau JKP dari luar Daerah Pabean di "
            "dalam Daerah Pabean yang Pajak Masukannya dapat dikreditkan"
        ),
        [_num(0), GRAY, _num(0), _num(0)],
        code="A.",
    )
    _row(
        sheet,
        (
            "Perolehan BKP/JKP dari dalam negeri dengan DPP Nilai Lain atau Besaran Tertentu "
            "yang Pajak Masukannya dapat dikreditkan (dengan Faktur Pajak Kode 04 dan 05)"
        ),
        [_num(ii["B"][0]), _num(ii["B"][1]), _num(ii["B"][2]), _num(ii["B"][3])],
        code="B.",
    )
    _row(
        sheet,
        (
            "Perolehan BKP/JKP dari dalam negeri selain dengan DPP Nilai Lain yang Pajak "
            "Masukannya dapat dikreditkan (dengan Faktur Pajak Kode 01, 09, dan 10)"
        ),
        [_num(ii["C"][0]), GRAY, _num(ii["C"][2]), _num(ii["C"][3])],
        code="C.",
    )
    _row(
        sheet,
        (
            "Perolehan BKP/JKP dari dalam negeri sebagai Pemungut PPN yang Pajak Masukannya "
            "dapat dikreditkan (dengan Faktur Pajak Kode 02 dan 03)"
        ),
        [_num(ii["D"][0]), _num(ii["D"][1]), _num(ii["D"][2]), _num(ii["D"][3])],
        code="D.",
    )
    kompensasi = tax_return.kompensasi_lebih_bayar
    _row(
        sheet, "Kompensasi kelebihan Pajak Masukan", [GRAY, GRAY, _num(kompensasi), GRAY], code="E."
    )
    _row(
        sheet,
        "Hasil penghitungan kembali Pajak Masukan yang telah dikreditkan",
        [GRAY, GRAY, "0", GRAY],
        code="F.",
    )
    sum_in_d = sum(ii[k][0] for k in "BCD")
    sum_in_p = sum(ii[k][2] for k in "BCD")
    g_ppn = sum_in_p + kompensasi
    _row(
        sheet,
        "Jumlah Pajak Masukan yang dapat diperhitungkan (II.A + II.B + II.C + II.D + II.E + II.F)",
        [_num(sum_in_d), GRAY, _num(g_ppn), GRAY],
        code="G.",
    )
    _row(
        sheet,
        (
            "Impor atau perolehan BKP/JKP yang Pajak Masukannya tidak dikreditkan dan/atau "
            "impor atau perolehan BKP/JKP yang mendapat fasilitas"
        ),
        ["0", "0", "0", "0"],
        code="H.",
    )
    _row(
        sheet,
        (
            "Impor atau perolehan BKP/JKP dengan Faktur Pajak yang dilaporkan secara "
            "digunggung dan barang/jasa yang tidak terutang PPN"
        ),
        ["0", GRAY, "-", "-"],
        code="I.",
    )
    _row(
        sheet,
        "Jumlah perolehan (II.A + II.B + II.C + II.D +II.H + II.I)",
        [_num(sum_in_d), GRAY, "-", "-"],
        code="J.",
    )
    sheet.gap(1)

    # ---- III. Penghitungan
    _section_head(
        sheet,
        "III. PENGHITUNGAN PPN KURANG BAYAR/LEBIH BAYAR",
        {2: ["PPN", "(Rupiah)"]},
    )
    sheet.gap(3)
    pembetulan = tax_return.correction_no > 0
    e_val = tax_return.ppn_kurang_bayar - tax_return.ppn_lebih_bayar
    ppn_cell = lambda v: [None, None, v, None]  # noqa: E731
    _row(
        sheet,
        "Pajak Keluaran yang harus dipungut sendiri (I.A.2 + I.A.3 + I.A.4 + I.A.5)",
        ppn_cell(_num(tax_return.penyerahan_ppn)),
        code="A.",
    )
    _row(sheet, "PPN disetor dimuka dalam Masa Pajak yang sama", ppn_cell("0"), code="B.")
    _row(
        sheet,
        "Pajak Masukan yang dapat diperhitungkan (II.G)",
        ppn_cell(_num(tax_return.masukan_ppn + kompensasi)),
        code="C.",
    )
    _row(sheet, "Kelebihan pemungutan PPN oleh Pemungut PPN", ppn_cell("0"), code="D.")
    _row(
        sheet,
        "PPN kurang atau (lebih) bayar (III.A - III.B - III.C - III.D)",
        ppn_cell(acct(e_val)),
        code="E.",
    )
    _row(
        sheet,
        "PPN kurang atau (lebih) bayar pada SPT yang dibetulkan sebelumnya",
        ppn_cell("-"),
        code="F.",
    )
    g_text = acct(e_val) if pembetulan else "-"
    top = sheet.y
    _row(
        sheet,
        "PPN kurang atau (lebih) bayar karena pembetulan SPT (III.E - III.F)",
        ppn_cell(g_text),
        code="G.",
    )
    checkbox(sheet, 407 * PX, top - 12.5, pembetulan, 8)
    sheet.text(432 * PX, top - 10, "Ganti SPT Sebelumnya", FONT, SIZE)
    # H. diminta untuk
    lb = tax_return.ppn_lebih_bayar > 0
    kompensasi_pick = lb and tax_return.lb_treatment == LbTreatment.kompensasi
    restitusi_pick = lb and tax_return.lb_treatment == LbTreatment.restitusi
    top = sheet.y - 3
    sheet.text(65 * PX, top - 8, "H.", FONT, SIZE)
    sheet.text(78 * PX, top - 8, "diminta untuk:", FONT, SIZE)
    opts = [
        ("1. dikompensasikan", kompensasi_pick),
        ("2. dikembalikan melalui pengembalian pendahuluan", False),
        ("3. dikembalikan melalui pemeriksaan", restitusi_pick),
    ]
    for i, (text, on) in enumerate(opts):
        oy = top - 8 - i * 11.5
        checkbox(sheet, 141 * PX, oy - 2, on, 9)
        sheet.text(169 * PX, oy, text, FONT, SIZE)
    sheet.y = top - 3 * 11.5 - 3
    # rekening box
    top = sheet.y
    h = 25
    sheet.c.setFillColor(GRAY_HEAD)
    sheet.c.rect(sheet.left, top - h, sheet.cw, h, stroke=0, fill=1)
    sheet.text(sheet.left + 3, top - 10, "Nomor Rekening", FONT, SIZE)
    sheet.text(sheet.left + 110, top - 10, ":", BOLD, SIZE)
    sheet.text(sheet.left + 3, top - 21, "Nama Pemilik Rekening", FONT, SIZE)
    sheet.text(sheet.left + 110, top - 21, ":", BOLD, SIZE)
    sheet.text(sheet.left + 360, top - 10, "Nama Bank", FONT, SIZE)
    sheet.text(sheet.left + 428, top - 10, ":", BOLD, SIZE)
    sheet.y = top - h


# ------------------------------------------------------------------- page 2
def _page2(
    sheet: Sheet, tax_return: TaxReturn, tenant_name: str | None, taxpayer_name: str
) -> None:
    _page_header(sheet, tax_return, tenant_name, 2)
    sheet.gap(6)

    # IV
    _section_head(
        sheet,
        "IV. PPN TERUTANG ATAS KEGIATAN MEMBANGUN SENDIRI",
        {0: ["DPP", "(Rupiah)"], 2: ["PPN", "(Rupiah)"]},
    )
    _row(sheet, "PPN Terutang", ["0", None, "0", None], level=0, code="")
    sheet.gap(4)
    # V
    _section_head(
        sheet,
        "V. PEMBAYARAN KEMBALI PAJAK MASUKAN YANG TIDAK DAPAT DIKREDITKAN",
        {2: ["PPN", "(Rupiah)"]},
    )
    _row(sheet, "PPN Yang Wajib Dibayar Kembali", [None, None, "0", None])
    sheet.gap(4)
    # VI
    _section_head(sheet, "VI. PAJAK PENJUALAN ATAS BARANG MEWAH", {3: ["PPnBM", "(Rupiah)"]})
    bm = lambda v: [None, None, None, v]  # noqa: E731
    _row(
        sheet,
        "PPnBM yang harus dipungut sendiri (I.A.2 + I.A.3 + I.A.4 + I.A.5)",
        bm(_num(tax_return.penyerahan_ppnbm)),
        code="A.",
    )
    _row(sheet, "Kelebihan pemungutan PPnBM oleh Pemungut PPN", bm("0"), code="B.")
    _row(
        sheet,
        "PPnBM kurang atau (lebih) bayar (VI.A - VI.B)",
        bm(_num(tax_return.penyerahan_ppnbm)),
        code="C.",
    )
    _row(
        sheet,
        "PPnBM kurang atau (lebih) bayar pada SPT yang dibetulkan sebelumnya",
        bm("-"),
        code="D.",
    )
    _row(
        sheet,
        "PPnBM kurang atau (lebih) bayar karena pembetulan SPT  (VI.C - VI.D)",
        bm("-"),
        code="E.",
    )
    _check_row(sheet, "F.", "diminta pengembalian pajak yang tidak seharusnya terutang")
    sheet.gap(4)
    # VII
    four = {
        0: ["HARGA JUAL/", "PENGGANTIAN/DPP", "(Rupiah)"],
        1: ["DPP NILAI LAIN/ DPP", "(Rupiah)"],
        2: ["PPN", "(Rupiah)"],
        3: ["PPnBM", "(Rupiah)"],
    }
    _section_head(sheet, "VII. PEMUNGUTAN PPN ATAU PPN DAN PPNBM OLEH PEMUNGUT PPN", four)
    sheet.gap(3)
    _row(sheet, "Jumlah PPN dan PPnBM yang dipungut", ["0", "0", "0", "0"], code="A.")
    _row(
        sheet,
        "PPN dan PPnBM kurang atau (lebih) bayar pada SPT yang dibetulkan sebelumnya",
        ["-"] * 4,
        code="B.",
    )
    _row(
        sheet,
        "PPN dan PPnBM kurang atau (lebih) bayar karena pembetulan SPT (VII.A - VII.B)",
        ["-"] * 4,
        code="C.",
    )
    sheet.text(
        78 * PX,
        sheet.y - 7,
        "Setiap kelebihan pemungutan PPN oleh Pemungut PPN pada bagian ini akan dipindah ke "
        "bagian III.D untuk PPN dan VI.B untuk PPnBM.",
        FONT,
        6,
    )
    sheet.gap(12)
    # VIII
    _section_head(sheet, "VIII. PEMUNGUTAN PPN ATAU PPN DAN PPNBM OLEH PIHAK LAIN", four)
    sheet.gap(3)
    _row(sheet, "Jumlah PPN dan PPnBM yang dipungut", ["0", "0", "0", "0"], code="A.")
    _row(
        sheet,
        "PPN dan PPnBM kurang atau (lebih) bayar pada SPT yang dibetulkan sebelumnya",
        ["-"] * 4,
        code="B.",
    )
    _row(
        sheet,
        "PPN dan PPnBM kurang atau (lebih) bayar karena pembetulan SPT (VIII.A - VIII.B)",
        ["-"] * 4,
        code="C.",
    )
    _check_row(sheet, "D.", "diminta pengembalian pajak yang tidak seharusnya terutang")
    sheet.gap(4)
    # IX
    _section_head(sheet, "IX. KELENGKAPAN SPT", {})
    sheet.gap(3)
    for i, text in enumerate(
        [
            "Dokumen Daftar Rincian Penyerahan Kendaraan Bermotor.",
            "Hasil Penghitungan Kembali Pajak Masukan yang Telah Dikreditkan.",
        ],
        start=1,
    ):
        top = sheet.y
        sheet.text(62 * PX, top - 10, f"{i}.", FONT, SIZE + 0.5)
        checkbox(sheet, 78 * PX, top - 12, False, 9)
        sheet.text(102 * PX, top - 10, text, FONT, SIZE + 0.5)
        sheet.y = top - 15
    sheet.gap(4)
    # PERNYATAAN
    gray_h = 14
    sheet.c.setFillColor(GRAY_SUB)
    sheet.c.rect(sheet.left, sheet.y - gray_h, sheet.cw, gray_h, stroke=0, fill=1)
    sheet.text(sheet.left + 3, sheet.y - 10, "PERNYATAAN", BOLD, 7.5)
    sheet.y -= gray_h + 4
    used = sheet.paragraph(
        sheet.left + 3,
        sheet.y,
        "Dengan menyadari sepenuhnya akan segala akibatnya termasuk sanksi-sanksi sesuai dengan "
        "ketentuan perundang-undangan yang berlaku, Saya menyatakan bahwa apa yang telah Saya "
        "beritahukan di atas adalah benar, lengkap, dan jelas.",
        sheet.cw - 6,
        size=SIZE + 0.5,
    )
    sheet.y -= used + 14
    sheet.text(
        sheet.right - 5, sheet.y, f"{sign_date(tax_return)}", FONT, SIZE + 0.5, align="right"
    )
    sheet.y -= 28
    kuasa = is_kuasa(tax_return)
    top = sheet.y
    checkbox(sheet, 43 * PX, top - 9, not kuasa, 10)
    sheet.text(66 * PX, top - 7, "PKP", FONT, 8)
    checkbox(sheet, 185 * PX, top - 9, kuasa, 10)
    sheet.text(206 * PX, top - 7, "Kuasa", FONT, 8)
    sheet.text(43 * PX, top - 21, "Nama", FONT, SIZE + 0.5)
    sheet.text(125 * PX, top - 21, f": {or_dash(tax_return.signer_name)}", FONT, SIZE + 0.5)
    sheet.text(43 * PX, top - 32, "Jabatan", FONT, SIZE + 0.5)
    sheet.text(125 * PX, top - 32, ": -", FONT, SIZE + 0.5)
    qr_size = 50
    draw_qr(sheet, 604 * PX, top - 2, qr_size, qr_payload(tax_return, "ppn"))
    signed_caption(sheet, 722 * PX, top - 2 - qr_size - 6, 8)
    sheet.y = top - 2 - qr_size - 24
    sheet.text(
        sheet.right - 5,
        sheet.y,
        f"Jatuh tempo penyampaian : {due_date_text(tax_return)}",
        "Helvetica-Oblique",
        SIZE + 0.5,
        align="right",
    )
    sheet.y -= 16
    sheet.text(sheet.left + 3, sheet.y, "Perhatian:", FONT, SIZE + 0.5)
    sheet.y -= 4
    sheet.paragraph(
        sheet.left + 3,
        sheet.y,
        "Sesuai dengan ketentuan Pasal 3 ayat (7) UU Nomor 6 Tahun 1983 dan perubahannya, apabila "
        "SPT Masa yang Saudara sampaikan tidak ditandatangani atau tidak sepenuhnya dilampiri "
        "keterangan dan/atau dokumen yang ditetapkan, maka SPT Saudara dianggap tidak disampaikan",
        sheet.cw - 6,
        size=SIZE + 0.5,
    )


def render_ppn(
    tax_return: TaxReturn,
    lines: Sequence[TaxReturnLine],
    tenant_name: str | None,
    taxpayer_name: str,
) -> bytes:
    sheet = new_sheet(f"SPT Masa PPN {tax_return.tax_month:02d}-{tax_return.tax_year}")
    sheet.y = sheet.h - 30
    _page1(sheet, tax_return, lines, tenant_name, taxpayer_name)
    footer(sheet)
    sheet.c.showPage()
    sheet.y = sheet.h - 30
    _page2(sheet, tax_return, tenant_name, taxpayer_name)
    footer(sheet)
    return sheet.finish()
