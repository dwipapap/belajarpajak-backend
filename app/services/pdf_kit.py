"""Shared reportlab layout kit for the simulator's printable documents.

Reused by bukti potong, faktur and SPT PDFs. Provides a top-down cursor
(``Sheet``) with a neutral letterhead, header strip, section bars, label rows,
tables, an e-signature block and a footer note.

Branding rule: letterhead is the simulator's own; never real-institution names
or crests.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

import segno
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

A4_SIZE = A4
F4_SIZE = (609.45, 935.43)

NAVY = HexColor("#0B3D7A")
YELLOW = HexColor("#FFD700")
GRAY_HEAD = HexColor("#D9D9D9")
GRAY_SUB = HexColor("#BFBFBF")
INK = HexColor("#111111")
CAPTION = HexColor("#B0B0B0")

FONT = "Helvetica"
BOLD = "Helvetica-Bold"
BASE_SIZE = 8.5

DISCLAIMER = (
    "Pemberitahuan: Dokumen ini dihasilkan oleh simulator pembelajaran pajak untuk "
    "keperluan latihan dan bukan dokumen perpajakan resmi."
)
SIGN_VALID_NOTE = (
    "Sesuai dengan ketentuan yang berlaku, bukti pemotongan ini dinyatakan sah "
    "dalam simulator ini dan tidak diperlukan tanda tangan basah."
)

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
def fmt_number(value: int | float | None) -> str:
    """1234567 -> '1.234.567' (Indonesian thousands separator)."""
    if value is None:
        return "-"
    return f"{int(round(value)):,}".replace(",", ".")


def fmt_percent(basis_points: int | None) -> str:
    """Basis points -> '2' / '1,5' (Indonesian decimal comma)."""
    if basis_points is None:
        return "-"
    pct = basis_points / 100
    text = f"{pct:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def fmt_date(value) -> str:
    """date/datetime -> '01 September 2026'."""
    if value is None:
        return "-"
    return f"{value.day:02d} {_MONTHS[value.month]} {value.year}"


def fmt_period(month: int | None, year: int | None) -> str:
    if not month or not year:
        return "-"
    return f"{month:02d}-{year}"


def or_dash(value) -> str:
    text = "" if value is None else str(value).strip()
    return text or "-"


@dataclass
class Col:
    """Table column: ``title`` may contain newlines, ``sub`` is e.g. 'B.3'."""

    title: str
    sub: str
    width: float
    align: str = "left"  # left | center | right


class Sheet:
    """Top-down page cursor over a reportlab canvas."""

    def __init__(self, title: str, pagesize: tuple[float, float] = A4_SIZE, margin: float = 28):
        self.buffer = io.BytesIO()
        self.pagesize = pagesize
        self.w, self.h = pagesize
        self.margin = margin
        self.left = margin
        self.right = self.w - margin
        self.cw = self.right - self.left
        self.c = canvas.Canvas(self.buffer, pagesize=pagesize)
        self.c.setTitle(title)
        self.y = self.h - 40  # cursor measured as absolute pdf y (descends)

    # ------------------------------------------------------------- primitives
    def text(self, x, y, s, font=FONT, size=BASE_SIZE, color=INK, align="left"):
        self.c.setFillColor(color)
        self.c.setFont(font, size)
        if align == "right":
            self.c.drawRightString(x, y, s)
        elif align == "center":
            self.c.drawCentredString(x, y, s)
        else:
            self.c.drawString(x, y, s)

    def wrap(self, s: str, width: float, font=FONT, size=BASE_SIZE) -> list[str]:
        out: list[str] = []
        for part in (s or "-").split("\n"):
            out.extend(simpleSplit(part or " ", font, size, width) or [""])
        return out

    def paragraph(self, x, top, s, width, font=FONT, size=BASE_SIZE, align="left", lead=None):
        """Draw wrapped text with first baseline below ``top``; returns height used."""
        lead = lead or size * 1.3
        lines = self.wrap(s, width, font, size)
        for i, line in enumerate(lines):
            by = top - size - i * lead
            if align == "center":
                self.text(x + width / 2, by, line, font, size, align="center")
            elif align == "right":
                self.text(x + width, by, line, font, size, align="right")
            else:
                self.text(x, by, line, font, size)
        return len(lines) * lead

    def gap(self, pts: float):
        self.y -= pts

    # ----------------------------------------------------------------- header
    def header(self, title_lines: Sequence[str], code: str, tenant_name: str | None):
        top = self.y
        # emblem: navy rounded square with "PS"
        em = 46
        ex, ey = self.left + 14, top - em
        self.c.setFillColor(NAVY)
        self.c.roundRect(ex, ey, em, em, 7, stroke=0, fill=1)
        self.c.setStrokeColor(YELLOW)
        self.c.setLineWidth(1.4)
        self.c.roundRect(ex + 3, ey + 3, em - 6, em - 6, 5, stroke=1, fill=0)
        self.text(ex + em / 2, ey + em / 2 - 6, "PS", BOLD, 17, YELLOW, "center")
        # letterhead
        lx = ex + em + 10
        self.text(lx, top - 14, "SIMULATOR PEMBELAJARAN PAJAK", FONT, 7.5)
        self.text(lx, top - 23, "PAJAKSIM", FONT, 7.5)
        self.text(lx, top - 32, (tenant_name or "DOKUMEN LATIHAN").upper()[:34], BOLD, 7.5)
        # title (middle) — placed right of the letterhead block
        tx = lx + 150
        box_w, box_h = 118, 60
        box_x = self.right - box_w
        tw = box_x - tx - 10
        ty = top - 8
        for line in title_lines:
            for sub in self.wrap(line, tw, BOLD, 9):
                self.text(tx, ty - 8, sub, BOLD, 9)
                ty -= 11
        # yellow code box
        self.c.setFillColor(YELLOW)
        self.c.rect(box_x, top - box_h, box_w, box_h, stroke=0, fill=1)
        self.text(box_x + box_w / 2, top - box_h / 2 - 8, code, BOLD, 24, INK, "center")
        self.y = top - box_h - 12

    def info_strip(self, items: Sequence[tuple[str, str]], widths: Sequence[float] | None = None):
        """Gray strip of labeled boxes with white value cells."""
        n = len(items)
        widths = widths or [self.cw / n] * n
        scale = self.cw / sum(widths)
        widths = [x * scale for x in widths]
        label_h = 28
        value_h = 18
        top = self.y
        self.c.setFillColor(GRAY_HEAD)
        self.c.rect(
            self.left, top - label_h - value_h - 6, self.cw, label_h + value_h + 6, stroke=0, fill=1
        )
        x = self.left
        for i, ((label, value), cw) in enumerate(zip(items, widths, strict=True)):
            if i:
                self.c.setStrokeColor(white)
                self.c.setLineWidth(1.2)
                self.c.line(x, top - 2, x, top - label_h - value_h - 4)
            lines = self.wrap(label, cw - 12, BOLD, 8)[:2]
            ly = top - 14 if len(lines) == 1 else top - 11
            for j, line in enumerate(lines):
                self.text(x + cw / 2, ly - j * 10, line, BOLD, 8, align="center")
            self.c.setFillColor(white)
            self.c.rect(x + 10, top - label_h - value_h, cw - 20, value_h - 2, stroke=0, fill=1)
            vtxt = or_dash(value)
            self.text(x + cw / 2, top - label_h - value_h + 5, vtxt, FONT, 9.5, align="center")
            x += cw
        self.y = top - label_h - value_h - 6 - 2

    def section_bar(self, text: str):
        h = 14
        self.c.setFillColor(NAVY)
        self.c.rect(self.left, self.y - h, self.cw, h, stroke=0, fill=1)
        self.text(self.left + 2, self.y - h + 4, text, BOLD, 8.5, white)
        self.y -= h

    # ------------------------------------------------------------------ rows
    def label_row(
        self,
        code: str,
        label: str,
        value: str,
        label_x: float = 54,
        colon_x: float = 190,
        value_x: float = 200,
        pad: float = 5,
    ) -> float:
        """'A.1  NPWP / NIK  :  value' with wrapping on both label and value."""
        top = self.y - pad
        lw = colon_x - label_x - 6
        vw = self.right - (self.left + value_x)
        llines = self.wrap(label, lw)
        vlines = self.wrap(value, vw)
        n = max(len(llines), len(vlines))
        lead = BASE_SIZE * 1.3
        self.text(self.left + 2, top - BASE_SIZE, code)
        for i, line in enumerate(llines):
            self.text(self.left + label_x, top - BASE_SIZE - i * lead, line)
        if colon_x:
            self.text(self.left + colon_x, top - BASE_SIZE, ":")
        for i, line in enumerate(vlines):
            self.text(self.left + value_x, top - BASE_SIZE - i * lead, line)
        used = n * lead + pad * 2 - 2
        self.y -= used
        return used

    def free_row(self, code: str, text: str, x: float = 54):
        """'B.1  Jenis Fasilitas : Tanpa Fasilitas'."""
        top = self.y - 5
        self.text(self.left + 2, top - BASE_SIZE, code)
        used = self.paragraph(self.left + x, top, text, self.right - self.left - x)
        self.y -= used + 8

    # ----------------------------------------------------------------- table
    def table(self, cols: Sequence[Col], rows: Sequence[Sequence[str]], x0: float = 0):
        """Gray head (titles) + darker gray sub-label row + wrapped body rows."""
        total = sum(c.width for c in cols)
        left = self.left + x0
        scale = (self.cw - x0) / total
        widths = [c.width * scale for c in cols]
        head_lines = [
            self.wrap(c.title, w - 8, BOLD, 8.5) for c, w in zip(cols, widths, strict=True)
        ]
        head_n = max(len(x) for x in head_lines)
        head_h = head_n * 10.5 + 10
        sub_h = 13
        top = self.y - 2
        self.c.setFillColor(GRAY_HEAD)
        self.c.rect(left, top - head_h, self.cw - x0, head_h, stroke=0, fill=1)
        self.c.setFillColor(GRAY_SUB)
        self.c.rect(left, top - head_h - sub_h, self.cw - x0, sub_h, stroke=0, fill=1)
        x = left
        for c, w, lines in zip(cols, widths, head_lines, strict=True):
            for j, line in enumerate(lines):
                self.text(
                    x + w / 2,
                    top - 13 - (head_n - len(lines)) * 5.25 - j * 10.5,
                    line,
                    BOLD,
                    8.5,
                    align="center",
                )
            self.text(x + w / 2, top - head_h - sub_h + 4, c.sub, BOLD, 6.5, align="center")
            x += w
        y = top - head_h - sub_h
        lead = BASE_SIZE * 1.3
        for row in rows:
            cells = [self.wrap(v, w - 10) for v, w in zip(row, widths, strict=True)]
            n = max(len(x) for x in cells)
            x = left
            for c, w, lines in zip(cols, widths, cells, strict=True):
                for j, line in enumerate(lines):
                    by = y - 6 - BASE_SIZE - j * lead
                    if c.align == "right":
                        self.text(x + w - 5, by, line, align="right")
                    elif c.align == "center":
                        self.text(x + w / 2, by, line, align="center")
                    else:
                        self.text(x + 5, by, line)
                x += w
            y -= n * lead + 12
            self.c.setStrokeColor(HexColor("#DDDDDD"))
            self.c.setLineWidth(0.5)
            self.c.line(left, y + 2, self.right, y + 2)
        self.y = y - 2

    # ------------------------------------------------------------- signature
    def signature(self, qr_payload: str, statements: Sequence[str], text_x: float = 190):
        top = self.y - 8
        qr = segno.make(qr_payload, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=6, border=1)
        buf.seek(0)
        size = 62
        self.c.drawImage(ImageReader(buf), self.left + 54, top - size, size, size)
        self.text(
            self.left + 54, top - size - 11, "Ditandatangani secara elektronik", FONT, 8.5, CAPTION
        )
        ty = top
        for st in statements:
            ty -= self.paragraph(self.left + text_x, ty, st, self.right - self.left - text_x) + 4
        self.y = top - size - 16

    def footer(self, note: str = DISCLAIMER):
        lines = self.wrap(note, self.cw * 0.8, FONT, 6.5)
        for i, line in enumerate(lines):
            self.text(self.left + 2, 38 - i * 8, line, FONT, 6.5)

    def finish(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buffer.getvalue()


__all__ = [
    "A4_SIZE",
    "F4_SIZE",
    "Col",
    "Color",
    "DISCLAIMER",
    "SIGN_VALID_NOTE",
    "Sheet",
    "fmt_date",
    "fmt_number",
    "fmt_percent",
    "fmt_period",
    "or_dash",
]
