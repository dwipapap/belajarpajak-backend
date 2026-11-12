"""Faktur Pajak simulasi PDF (keluaran and masukan share this renderer).

Layout follows the standard faktur format: centered title, seller block at the
top right, a bordered column of boxed rows (nomor seri, penjual, pembeli), the
item table, summary rows, an e-signature block and a footer note. Multi-page
when there are many lines (the table header repeats, footer shows "n dari m").

Branding rule: only the simulator's own wording — never real-institution names.
The invoice objects are duck-typed so ``TaxInvoice`` and ``TaxInputInvoice``
(identical column sets, masukan has no ``signer_name``) both render here.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from typing import Any

import segno
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.models.enums import BuyerIdentityType, TaxInvoiceStatus
from app.services.pdf_kit import DISCLAIMER, F4_SIZE, fmt_date, or_dash

FONT = "Helvetica"
BOLD = "Helvetica-Bold"
SIZE = 10.0
LEAD = 12.2
INK = HexColor("#111111")
GRAY = HexColor("#8A8A8A")
LIGHT = HexColor("#B0B0B0")
RED = HexColor("#C0392B")

SIGN_NOTE = (
    "Sesuai dengan ketentuan yang berlaku, Faktur Pajak ini telah ditandatangani secara "
    "elektronik dalam simulator ini sehingga tidak diperlukan tanda tangan basah pada "
    "Faktur Pajak ini."
)

# Geometry (pt, measured from the top of the page).
PAGE_W, PAGE_H = F4_SIZE
BX0, BX1 = 29.0, 578.0  # outer border
BTOP, BBOT = 68.0, 825.0
PAD = 14.0
COL_NO, COL_CODE, COL_NAME = 74.0, 131.0, 425.5  # right edges of table columns
ROW_MIN = 26.0
SUM_ROW = 22.0  # summary row height
TAIL_H = 6 * SUM_ROW + 3 + 22 + 17 + 49 + 32  # summary + statement + signature + referensi
LIMIT = 794.0

_CITY_RE = re.compile(r"\b(KOTA|KABUPATEN|KAB\.)\s+[A-Z][A-Z .'-]*?(?=\s*(?:,|\d|$))", re.I)


def fmt_rp(value: int | None) -> str:
    """1818400 -> '1.818.400,00'."""
    return f"{int(value or 0):,}".replace(",", ".") + ",00"


def _fmt_qty(milli: int) -> str:
    text = f"{milli / 1000:.3f}"
    if text.endswith("0"):
        text = text[:-1]
    return text.replace(".", ",")


def _fmt_rate(bp: int) -> str:
    return f"{bp / 100:.2f}".replace(".", ",")


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _city(address: str | None) -> str | None:
    match = _CITY_RE.search(address or "")
    return match.group(0).strip().upper() if match else None


def _qr_image(payload: str) -> ImageReader:
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="png", scale=5, border=0)
    buf.seek(0)
    return ImageReader(buf)


class _Doc:
    def __init__(self, invoice, lines: Sequence, tenant_name: str | None, total_pages: int):
        self.inv = invoice
        self.lines = list(lines)
        self.tenant = tenant_name
        self.total = total_pages
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=F4_SIZE)
        number = invoice.invoice_number or "-"
        self.c.setTitle(f"Faktur Pajak Simulasi {number}")
        self.number = number
        self.page = 0
        self.t = BTOP
        self.cancelled = _enum_value(invoice.status) == TaxInvoiceStatus.invalid.value

    # ---------------------------------------------------------- primitives
    def text(self, x, t, s, font=FONT, size=SIZE, color=INK, align="left"):
        """Draw with the baseline at ``t`` points from the top."""
        self.c.setFillColor(color)
        self.c.setFont(font, size)
        y = PAGE_H - t
        if align == "right":
            self.c.drawRightString(x, y, s)
        elif align == "center":
            self.c.drawCentredString(x, y, s)
        else:
            self.c.drawString(x, y, s)

    def wrap(self, s: str, width: float, font=FONT, size=SIZE) -> list[str]:
        from reportlab.lib.utils import simpleSplit

        out: list[str] = []
        for part in (s or "-").split("\n"):
            out.extend(simpleSplit(part or " ", font, size, width) or [""])
        return out

    def hline(self, t, x0=BX0, x1=BX1):
        self.c.setStrokeColor(INK)
        self.c.setLineWidth(0.7)
        self.c.line(x0, PAGE_H - t, x1, PAGE_H - t)

    def vline(self, x, t0, t1):
        self.c.setStrokeColor(INK)
        self.c.setLineWidth(0.7)
        self.c.line(x, PAGE_H - t0, x, PAGE_H - t1)

    def block(self, lines: Sequence[str], x, top, height, align="left", font=FONT, width=None):
        """Vertically centered lines inside a cell of ``height`` starting at ``top``."""
        start = top + (height - len(lines) * LEAD) / 2 + SIZE * 0.85
        for i, line in enumerate(lines):
            tx = x
            if align == "center":
                tx = x + (width or 0) / 2
            elif align == "right":
                tx = x + (width or 0)
            self.text(tx, start + i * LEAD, line, font, SIZE, align=align)

    # ------------------------------------------------------------- pages
    def new_page(self, first: bool):
        if self.page:
            self.close_page()
            self.c.showPage()
        self.page += 1
        self.t = BTOP
        self.text(PAD + BX0, 56, "SIMULATOR PEMBELAJARAN PAJAK", BOLD, 6.5, GRAY)
        if self.tenant:
            self.text(PAD + BX0, 64, self.tenant.upper()[:60], FONT, 6.5, GRAY)
        self.text(PAGE_W / 2, 92, "Faktur Pajak", BOLD, 15, align="center")
        if self.cancelled:
            self.text(PAGE_W / 2, 104, "DIBATALKAN", BOLD, 10, RED, "center")
            self._watermark()
        self.t = 113.0 if self.cancelled else 108.0
        if first:
            self.seller_header()

    def _watermark(self):
        self.c.saveState()
        self.c.setFillColor(HexColor("#F3D6D2"))
        self.c.setFont(BOLD, 78)
        self.c.translate(PAGE_W / 2, PAGE_H / 2)
        self.c.rotate(40)
        self.c.drawCentredString(0, 0, "DIBATALKAN")
        self.c.restoreState()

    def close_page(self):
        # Outer border + footer (after content so the frame sits on top).
        self.c.setStrokeColor(INK)
        self.c.setLineWidth(0.9)
        self.c.rect(BX0, PAGE_H - BBOT, BX1 - BX0, BBOT - BTOP, stroke=1, fill=0)
        for i, line in enumerate(self.wrap(DISCLAIMER, 400, FONT, 6)):
            self.text(BX0 + 7, 801 + i * 7.5, line, FONT, 6)
        self.text(BX1 - 8, 801, f"{self.page} dari {self.total}", FONT, 6.5, align="right")

    # ------------------------------------------------------------ sections
    def seller_header(self):
        inv = self.inv
        x = 332.0
        lines = [f"Nama: {or_dash(inv.seller_name)}"]
        addr = f"Alamat: {or_dash(inv.seller_address)}"
        if inv.seller_idtku:
            addr += f" #{inv.seller_idtku}"
        lines += self.wrap(addr, 200)
        for i, line in enumerate(lines):
            self.text(x, self.t + 9 + i * LEAD, line)
        self.t += 9 + (len(lines) - 1) * LEAD + 5

    def row(self, lines: Sequence[str], min_h=ROW_MIN, font=FONT):
        h = max(min_h, len(lines) * LEAD + 11.4)
        self.hline(self.t)
        self.hline(self.t + h)
        self.vline(BX0, self.t, self.t + h)
        self.vline(BX1, self.t, self.t + h)
        self.block(lines, BX0 + PAD, self.t, h, font=font)
        self.t += h

    def info_rows(self):
        inv = self.inv
        self.row([f"Kode dan Nomor Seri Faktur Pajak: {self.number}"])
        self.row(["Pengusaha Kena Pajak:"])
        self.row(
            [
                f"Nama : {or_dash(inv.seller_name)}",
                *self.wrap(f"Alamat : {or_dash(inv.seller_address)}", BX1 - BX0 - 2 * PAD),
                f"NPWP : {or_dash(inv.seller_npwp)}",
            ]
        )
        self.row(["Pembeli Barang Kena Pajak/Penerima Jasa Kena Pajak:"])
        kind = _enum_value(inv.buyer_identity_type)
        ident = or_dash(inv.buyer_identity_number)

        def pick(want: BuyerIdentityType) -> str:
            return ident if kind == want.value else "-"

        body = [
            f"Nama : {or_dash(inv.buyer_name)}",
            *self.wrap(f"Alamat : {or_dash(inv.buyer_address)}", BX1 - BX0 - 2 * PAD),
        ]
        if inv.buyer_idtku:
            body.append(f"#{inv.buyer_idtku}")
        body += [
            f"NPWP : {pick(BuyerIdentityType.npwp)}",
            f"NIK : {pick(BuyerIdentityType.nik)}",
            f"Nomor Paspor : {pick(BuyerIdentityType.paspor)}",
            f"Identitas Lain : {pick(BuyerIdentityType.identitas_lain)}",
            f"Email: {or_dash(inv.buyer_email)}",
        ]
        self.row(body)

    def table_header(self):
        h = 47.0
        self.hline(self.t)
        self.hline(self.t + h)
        for x in (BX0, COL_NO, COL_CODE, COL_NAME, BX1):
            self.vline(x, self.t, self.t + h)
        self.block(["No."], BX0, self.t, h, "center", width=COL_NO - BX0)
        self.block(
            ["Kode", "Barang/", "Jasa"], COL_NO, self.t, h, "center", width=COL_CODE - COL_NO
        )
        self.block(
            ["Nama Barang Kena Pajak / Jasa Kena Pajak"],
            COL_CODE,
            self.t,
            h,
            "center",
            width=COL_NAME - COL_CODE,
        )
        self.block(
            ["Harga Jual / Penggantian /", "Uang Muka / Termin", "(Rp)"],
            COL_NAME,
            self.t,
            h,
            "center",
            width=BX1 - COL_NAME,
        )
        self.t += h

    def item_lines(self, line) -> list[str]:
        width = COL_NAME - COL_CODE - 14
        out = self.wrap(line.item_name, width)
        out.append(
            f"Rp {fmt_rp(line.unit_price)} x {_fmt_qty(line.quantity_milli)} {or_dash(line.unit)}"
        )
        out.append(f"Potongan Harga = Rp {fmt_rp(line.discount)}")
        out.append(f"PPnBM ({_fmt_rate(line.ppnbm_rate_basis_points)}%) = Rp {fmt_rp(line.ppnbm)}")
        return out

    def item_row(self, no: int, line):
        lines = self.item_lines(line)
        h = max(47.0, len(lines) * LEAD + 3)
        self.hline(self.t + h)
        for x in (BX0, COL_NO, COL_CODE, COL_NAME, BX1):
            self.vline(x, self.t, self.t + h)
        self.block([str(no)], BX0, self.t, h, "center", width=COL_NO - BX0)
        self.block([or_dash(line.item_code)], COL_NO, self.t, h, "center", width=COL_CODE - COL_NO)
        top = self.t + (h - len(lines) * LEAD) / 2 + SIZE * 0.85
        for i, text in enumerate(lines):
            self.text(COL_CODE + 7, top + i * LEAD, text)
        self.block(
            [fmt_rp(line.total_price)], COL_NAME, self.t, h, "right", width=BX1 - COL_NAME - 11
        )
        self.t += h

    def summary(self):
        inv = self.inv
        dpp_other = sum(ln.dpp_other for ln in self.lines if ln.use_dpp_other)
        dpp = sum(ln.dpp for ln in self.lines if not ln.use_dpp_other) + dpp_other
        rows = [
            ("Harga Jual / Penggantian / Uang Muka / Termin", fmt_rp(inv.total_price)),
            ("Dikurangi Potongan Harga", fmt_rp(inv.total_discount)),
            ("Dikurangi Uang Muka yang telah diterima", ""),
            ("Dasar Pengenaan Pajak", fmt_rp(dpp)),
            ("Jumlah PPN (Pajak Pertambahan Nilai)", fmt_rp(inv.total_ppn)),
            ("Jumlah PPnBM (Pajak Penjualan atas Barang Mewah)", fmt_rp(inv.total_ppnbm)),
        ]
        for label, value in rows:
            h = SUM_ROW
            self.hline(self.t + h)
            for x in (BX0, COL_NAME, BX1):
                self.vline(x, self.t, self.t + h)
            self.block([label], BX0 + PAD, self.t, h)
            if value:
                self.block([value], COL_NAME, self.t, h, "right", width=BX1 - COL_NAME - 11)
            self.t += h

    def signature(self):
        inv = self.inv
        self.t += 3
        for i, line in enumerate(self.wrap(SIGN_NOTE, 400, FONT, 7.5)):
            self.text(BX0 + PAD, self.t + 8 + i * 9, line, FONT, 7.5)
        self.t += 22
        size = 49.0
        self.c.drawImage(
            _qr_image(f"FAKTUR-SIM:{self.number}"),
            BX0 + 28,
            PAGE_H - (self.t + 4) - size,
            size,
            size,
        )
        rx = 351.0
        signed_at = inv.issued_at or inv.invoice_date
        city = _city(inv.seller_address)
        date_text = fmt_date(signed_at)
        self.text(rx, self.t + 12, f"{city}, {date_text}" if city else date_text)
        qr_top = self.t + 17
        self.c.drawImage(
            _qr_image(f"FAKTUR-SIM-TTD:{self.number}"), rx, PAGE_H - qr_top - size, size, size
        )
        self.text(rx, qr_top + size + 10, "Ditandatangani secara elektronik", FONT, 10, LIGHT)
        signer = getattr(inv, "signer_name", None)
        self.text(rx, qr_top + size + 24, or_dash(signer))
        self.t = qr_top + size + 32
        if inv.reference:
            self.text(BX0 + 40, self.t, f"(Referensi: {inv.reference})", FONT, 6.5)

    # --------------------------------------------------------------- build
    def build(self) -> bytes:
        self.new_page(first=True)
        self.info_rows()
        self.table_header()
        for no, line in enumerate(self.lines, start=1):
            h = max(47.0, len(self.item_lines(line)) * LEAD + 3)
            if self.t + h > LIMIT - 20:
                self.new_page(first=False)
                self.table_header()
            self.item_row(no, line)
        if self.t + TAIL_H > LIMIT:
            self.new_page(first=False)
            self.hline(self.t)
        self.summary()
        self.signature()
        self.close_page()
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def render_faktur_pdf(invoice, lines: Sequence, tenant_name: str | None = None) -> bytes:
    """Render a Faktur Pajak (keluaran or masukan) as PDF bytes."""
    probe = _Doc(invoice, lines, tenant_name, 1)
    probe.build()
    return _Doc(invoice, lines, tenant_name, probe.page).build()
