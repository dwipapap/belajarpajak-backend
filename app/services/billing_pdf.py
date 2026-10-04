"""Kode billing PDF rendering — the printable payment slip.

Layout mirrors the reference billing document: a KODE BILLING box, the
taxpayer identity block, a DETAIL BILLING table (KAP-KJS / Masa Pajak /
Ref Tagihan / NOP / Nominal), a TOTAL row, the amount spelled out in
Indonesian words (terbilang), and a QR code carrying the billing code.

Branding rule: the letterhead is the simulator's own ("Simulator Pembelajaran
Pajak"), never the real institutions' names or crests.
"""

from __future__ import annotations

import io

import segno
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlmodel import Session, select

from app.models.billing import BillingCode
from app.models.enums import TaxReturnType
from app.models.tax_return import TaxReturn, TaxReturnLine
from app.services import kjs

_INK = (0.1, 0.1, 0.14)
_GOLD = (0.94, 0.74, 0.15)
_LIGHT = (0.97, 0.86, 0.35)

_ONES = [
    "",
    "satu",
    "dua",
    "tiga",
    "empat",
    "lima",
    "enam",
    "tujuh",
    "delapan",
    "sembilan",
    "sepuluh",
    "sebelas",
]

_SCALES = [
    (1_000_000_000_000, "triliun"),
    (1_000_000_000, "miliar"),
    (1_000_000, "juta"),
    (1_000, "ribu"),
]


def _under_thousand(n: int) -> str:
    if n < 12:
        return _ONES[n]
    if n < 20:
        return f"{_ONES[n - 10]} belas"
    if n < 100:
        parts = [f"{_ONES[n // 10]} puluh"]
        if n % 10:
            parts.append(_ONES[n % 10])
        return " ".join(parts)
    hundreds = "se" if n // 100 == 1 else f"{_ONES[n // 100]} "
    return " ".join(part for part in (f"{hundreds}ratus", _under_thousand(n % 100)) if part.strip())


def terbilang(n: int) -> str:
    """Spell a non-negative integer in Indonesian words.

    Example: 1084909 → 'satu juta delapan puluh empat ribu sembilan ratus sembilan'.
    """
    n = int(n)
    if n == 0:
        return "nol"
    parts: list[str] = []
    for scale, label in _SCALES:
        if n >= scale:
            count = n // scale
            if count == 1 and scale == 1_000:
                parts.append("seribu")
            else:
                parts.append(" ".join(part for part in (_under_thousand(count), label) if part))
            n %= scale
    if n > 0:
        parts.append(_under_thousand(n))
    return " ".join(parts).strip()


def billing_detail_rows(session: Session, billing: BillingCode) -> list[tuple[str, str, int]]:
    """(KAP-KJS, masa pajak, nominal) rows for the detail table.

    Derived from the SPT's posted lines so a PPh billing lists one row per
    pasal KJS with a nonzero amount, exactly like the reference document. The
    billing's own ``amount`` stays the single source of truth for the total.
    """
    if billing.spt_id is None:
        return [(billing.kap_kjs, billing.masa_pajak, billing.amount)]
    tax_return = session.get(TaxReturn, billing.spt_id)
    if tax_return is None:
        return [(billing.kap_kjs, billing.masa_pajak, billing.amount)]

    if tax_return.return_type == TaxReturnType.ppn:
        return [(kjs.KJS_PPN_MASA, billing.masa_pajak, billing.amount)]

    lines = session.exec(
        select(TaxReturnLine).where(TaxReturnLine.return_id == tax_return.id)
    ).all()
    per_kjs: dict[str, int] = {}
    for line in lines:
        if line.pemotongan > 0:
            per_kjs[line.code] = per_kjs.get(line.code, 0) + line.pemotongan
    if not per_kjs:
        return [(billing.kap_kjs, billing.masa_pajak, billing.amount)]
    return [(code, billing.masa_pajak, amount) for code, amount in per_kjs.items()]


def render_billing_pdf(session: Session, billing: BillingCode) -> bytes:
    """Render the printable billing slip as PDF bytes."""
    rows = billing_detail_rows(session, billing)

    buffer = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"Kode Billing {billing.code}")

    margin = 25 * mm
    top = height - 30 * mm

    # --- Letterhead ---
    pdf.setFillColorRGB(*_INK)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(margin, top, "SIMULATOR PEMBELAJARAN PAJAK")
    pdf.setFont("Helvetica", 10.5)
    pdf.drawString(margin, top - 6 * mm, "Layanan Pembayaran — Kode Billing Simulasi")

    # KODE BILLING box (top right)
    box_w, box_h = 62 * mm, 16 * mm
    box_x, box_y = width - margin - box_w, top - 6 * mm
    pdf.setStrokeColorRGB(*_INK)
    pdf.setLineWidth(1.2)
    pdf.rect(box_x, box_y, box_w, box_h)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(box_x + box_w / 2, box_y + box_h - 5.5 * mm, "KODE BILLING")
    pdf.setFont("Courier-Bold", 13)
    pdf.drawCentredString(box_x + box_w / 2, box_y + 3.5 * mm, billing.code)

    # --- Identity block ---
    y = top - 22 * mm
    identity = [
        ("NPWP", billing.tenant_id),
        ("MATA UANG", "IDR"),
        ("NOMINAL", f"{billing.amount:,}".replace(",", ".") + ",00"),
        ("JUMLAH DETAIL", len(rows)),
    ]
    for label, value in identity:
        pdf.setFont("Helvetica-Bold", 10.5)
        pdf.drawString(margin, y, label)
        pdf.setFont("Helvetica", 10.5)
        pdf.drawString(margin + 40 * mm, y, f": {value}")
        y -= 5.6 * mm
    # The reference portal shows NAMA/ALAMAT; the simulator uses the account label.
    pdf.setFont("Helvetica-Bold", 10.5)
    pdf.drawString(margin, y, "NAMA")
    pdf.setFont("Helvetica", 10.5)
    pdf.drawString(margin + 40 * mm, y, f": WP Simulasi #{billing.siswa_id}")
    y -= 6 * mm

    # --- Detail table ---
    y -= 4 * mm
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(margin, y, "DETAIL BILLING:")
    y -= 6 * mm

    columns = [
        ("KAP-KJS", 0),
        ("MASA PAJAK", 34 * mm),
        ("REF TAGIHAN", 68 * mm),
        ("NOP", 96 * mm),
        ("NOMINAL", 122 * mm),
    ]
    header_h = 8 * mm
    row_h = 7.5 * mm
    table_w = 145 * mm
    pdf.setFillColorRGB(*_GOLD)
    pdf.rect(margin, y - header_h, table_w, header_h, stroke=0, fill=1)
    pdf.setFillColorRGB(*_INK)
    pdf.setFont("Helvetica-Bold", 9.5)
    for label, offset in columns:
        pdf.drawString(margin + offset + 3 * mm, y - header_h + 2.5 * mm, label)

    row_y = y - header_h
    pdf.setFont("Helvetica", 9.5)
    for kap_kjs, masa, amount in rows:
        pdf.setFillColorRGB(1, 1, 1)
        pdf.rect(margin, row_y - row_h, table_w, row_h, stroke=0, fill=1)
        pdf.setFillColorRGB(*_INK)
        cells = [
            kap_kjs,
            masa,
            "-",
            "-",
            f"Rp{amount:,}.00".replace(",", "."),
        ]
        for (label, offset), text in zip(columns, cells, strict=True):
            align_right = label == "NOMINAL"
            if align_right:
                pdf.drawRightString(margin + offset + 21 * mm, row_y - row_h + 2.2 * mm, text)
            else:
                pdf.drawString(margin + offset + 3 * mm, row_y - row_h + 2.2 * mm, text)
        row_y -= row_h

    # TOTAL row (gold)
    pdf.setFillColorRGB(*_LIGHT)
    pdf.rect(margin, row_y - row_h, table_w, row_h, stroke=0, fill=1)
    pdf.setFillColorRGB(*_INK)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(margin + 3 * mm, row_y - row_h + 2.4 * mm, "TOTAL")
    pdf.drawRightString(
        margin + table_w - 3 * mm,
        row_y - row_h + 2.4 * mm,
        f"Rp{billing.amount:,}.00".replace(",", "."),
    )
    row_y -= row_h

    # Terbilang box
    pdf.setFont("Helvetica", 9.5)
    pdf.rect(margin, row_y - 12 * mm, table_w, 12 * mm)
    pdf.drawString(margin + 3 * mm, row_y - 4.5 * mm, "Terbilang:")
    words = terbilang(billing.amount)
    if not words.endswith("Rupiah"):
        words = f"{words} Rupiah"
    pdf.setFont("Helvetica-Oblique", 9.5)
    pdf.drawString(margin + 22 * mm, row_y - 9.5 * mm, words)

    # --- QR + usage note ---
    qr_y = row_y - 34 * mm
    qr = segno.make(f"KODEBILLING:{billing.code}", error="m")
    qr_buffer = io.BytesIO()
    qr.save(qr_buffer, kind="png", scale=6, border=2)
    qr_buffer.seek(0)
    pdf.drawImage(ImageReader(qr_buffer), margin, qr_y - 6 * mm, 22 * mm, 22 * mm)

    pdf.setFillColorRGB(*_INK)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(margin + 28 * mm, qr_y + 8 * mm, "GUNAKAN KODE BILLING DI BAWAH INI")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(
        margin + 28 * mm,
        qr_y + 2 * mm,
        "Simulasi pembelajaran - selesaikan pembayaran pada menu Pembayaran.",
    )

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
