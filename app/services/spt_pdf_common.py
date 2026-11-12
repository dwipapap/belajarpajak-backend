"""Shared drawing helpers for the SPT Masa document PDFs.

Builds on ``pdf_kit.Sheet`` (letterhead, section bars, footer) and adds the
pieces the SPT forms need: a compact period strip, mid-gray sub bars, rich
one-line text with italic asides, checkboxes, a QR block and the bordered
"Pernyataan" box.
"""

from __future__ import annotations

import calendar
import io

import segno
from reportlab.lib.colors import HexColor, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth

from app.models.enums import SignerCapacity
from app.models.tax_return import TaxReturn
from app.services.pdf_kit import (
    BOLD,
    CAPTION,
    F4_SIZE,
    FONT,
    GRAY_HEAD,
    GRAY_SUB,
    INK,
    YELLOW,
    Sheet,
    fmt_date,
    fmt_number,
    or_dash,
)

ITALIC = "Helvetica-Oblique"
DISABLED = HexColor("#ADADAD")
LINE = HexColor("#666666")
_MONTHS = [
    "",
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
]


# ---------------------------------------------------------------- formatting
def month_name(month: int) -> str:
    return _MONTHS[month]


def masa_label(tax_return: TaxReturn) -> str:
    return f"{_MONTHS[tax_return.tax_month]} {tax_return.tax_year}"


def status_label(tax_return: TaxReturn) -> str:
    if tax_return.correction_no > 0:
        return f"PEMBETULAN KE-{tax_return.correction_no}"
    return "NORMAL"


def acct(value: int | None) -> str:
    """Accounting style: negatives in parentheses, None -> '-'."""
    if value is None:
        return "-"
    if value < 0:
        return f"({fmt_number(-value)})"
    return fmt_number(value)


def due_date_text(tax_return: TaxReturn) -> str:
    """End of the month after the tax period (batas penyampaian SPT Masa)."""
    month = tax_return.tax_month + 1
    year = tax_return.tax_year
    if month > 12:
        month, year = 1, year + 1
    last = calendar.monthrange(year, month)[1]
    return f"{last:02d} {_MONTHS[month]} {year}"


def signer_npwp(tax_return: TaxReturn) -> str:
    return or_dash(tax_return.signer_npwp)


def is_kuasa(tax_return: TaxReturn) -> bool:
    return tax_return.signer_capacity == SignerCapacity.kuasa_wajib_pajak


def sign_date(tax_return: TaxReturn) -> str:
    return fmt_date(tax_return.submitted_at)


def qr_payload(tax_return: TaxReturn, kind: str) -> str:
    return f"SPTSIM:{kind}:{tax_return.tax_year}-{tax_return.tax_month:02d}:{tax_return.id}"


# ------------------------------------------------------------------- drawing
def new_sheet(title: str) -> Sheet:
    return Sheet(title, pagesize=F4_SIZE)


def draw_header(
    sheet: Sheet,
    title_lines: list[tuple[str, float]],
    code_lines: list[tuple[str, float]],
    tenant_name: str | None,
) -> None:
    """Letterhead + title block + yellow box with one or two code lines."""
    top = sheet.y
    sheet.header([], "", tenant_name)  # emblem, letterhead, empty yellow box
    box_w, box_h = 118, 60
    box_x = sheet.right - box_w
    # repaint the box so the (empty) code text drawn by the kit cannot show
    sheet.c.setFillColor(YELLOW)
    sheet.c.rect(box_x, top - box_h, box_w, box_h, stroke=0, fill=1)
    n = len(code_lines)
    cy = top - box_h / 2 + (n - 1) * 8 - 5
    for i, (text, size) in enumerate(code_lines):
        sheet.text(box_x + box_w / 2, cy - i * 17, text, BOLD, size, INK, "center")
    tx = sheet.left + 14 + 46 + 10 + 150
    ty = top - 14
    for text, size in title_lines:
        sheet.text(tx, ty, text, BOLD, size)
        ty -= size + 2


def strip(
    sheet: Sheet, items: list[tuple[str, str]], widths: list[float] | None = None, h: float = 32
) -> None:
    """Gray band: bold label over a white value box, per column."""
    n = len(items)
    widths = widths or [1] * n
    scale = sheet.cw / sum(widths)
    top = sheet.y
    sheet.c.setFillColor(GRAY_HEAD)
    sheet.c.rect(sheet.left, top - h, sheet.cw, h, stroke=0, fill=1)
    x = sheet.left
    for i, ((label, value), wd) in enumerate(zip(items, widths, strict=True)):
        cw = wd * scale
        if i:
            sheet.c.setStrokeColor(white)
            sheet.c.setLineWidth(1)
            sheet.c.line(x, top - 1, x, top - h + 1)
        sheet.text(x + cw / 2, top - 11, label, BOLD, 8, align="center")
        sheet.c.setFillColor(white)
        sheet.c.rect(x + 10, top - h + 3, cw - 20, 13, stroke=0, fill=1)
        sheet.text(x + cw / 2, top - h + 7, value, FONT, 8.5, align="center")
        x += cw
    sheet.y = top - h - 3


def gray_bar(sheet: Sheet, text: str, h: float = 13, fill=GRAY_SUB) -> None:
    sheet.c.setFillColor(fill)
    sheet.c.rect(sheet.left, sheet.y - h, sheet.cw, h, stroke=0, fill=1)
    sheet.text(sheet.left + 3, sheet.y - h + 4, text, BOLD, 7.5)
    sheet.y -= h


def rich_text(sheet: Sheet, x: float, y: float, text: str, size: float, max_w: float) -> None:
    """One line; text after the marker ``\\x01`` is italic. Shrinks to fit ``max_w``."""
    plain = text.replace("\x01", "")
    while size > 5.5 and stringWidth(plain, FONT, size) > max_w:
        size -= 0.25
    head, _, tail = text.partition("\x01")
    sheet.text(x, y, head, FONT, size)
    if tail:
        sheet.text(x + stringWidth(head, FONT, size), y, tail, ITALIC, size)


def checkbox(sheet: Sheet, x: float, y: float, checked: bool, size: float = 9) -> None:
    """Square box with its lower-left corner at (x, y)."""
    sheet.c.setStrokeColor(INK)
    sheet.c.setLineWidth(0.6)
    sheet.c.setFillColor(white)
    sheet.c.rect(x, y, size, size, stroke=1, fill=1)
    if checked:
        sheet.text(x + size / 2, y + 1.6, "X", FONT, size - 1.5, INK, "center")


def draw_qr(sheet: Sheet, x: float, top: float, size: float, payload: str) -> None:
    qr = segno.make(payload, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=6, border=1)
    buf.seek(0)
    sheet.c.drawImage(ImageReader(buf), x, top - size, size, size)


def signed_caption(sheet: Sheet, x_right: float, y: float, size: float = 8) -> None:
    sheet.text(x_right, y, "Ditandatangani secara elektronik", FONT, size, CAPTION, "right")


def footer(sheet: Sheet) -> None:
    sheet.footer()
