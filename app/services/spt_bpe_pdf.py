"""BPE SPT PDF rendering — printable Bukti Penerimaan Elektronik for a reported SPT.

Layout mirrors the other learning documents: SIMULASI header, document
identity (BPE-SIM number, period, correction), signer block, receipt status
block, and a QR code carrying the BPE number. Never uses real-institution
branding.
"""

from __future__ import annotations

import io

import segno
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.models.tax_return import TaxReturn

_INK = (0.1, 0.1, 0.14)


def _status_label(tax_return: TaxReturn) -> str:
    if tax_return.ppn_lebih_bayar > 0:
        return f"Lebih Bayar — Rp{tax_return.ppn_lebih_bayar:,}".replace(",", ".")
    if tax_return.amount_due > 0:
        return f"Kurang Bayar (lunas) — Rp{tax_return.amount_due:,}".replace(",", ".")
    return "Nihil"


def render_spt_bpe_pdf(tax_return: TaxReturn) -> bytes:
    """Render the BPE for a reported SPT as PDF bytes."""
    buffer = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"BPE SPT {tax_return.bpe_number or tax_return.id}")

    margin = 25 * mm
    top = height - 30 * mm

    pdf.setFillColorRGB(*_INK)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(margin, top, "SIMULATOR PEMBELAJARAN PAJAK")
    pdf.setFont("Helvetica", 10.5)
    pdf.drawString(
        margin, top - 6 * mm, "Bukti Penerimaan Elektronik SPT — bukan dokumen resmi"
    )

    box_w, box_h = 72 * mm, 16 * mm
    box_x, box_y = width - margin - box_w, top - 6 * mm
    pdf.setStrokeColorRGB(*_INK)
    pdf.setLineWidth(1.2)
    pdf.rect(box_x, box_y, box_w, box_h)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(box_x + box_w / 2, box_y + box_h - 5.5 * mm, "BPE SPT")
    pdf.setFont("Courier-Bold", 9)
    pdf.drawCentredString(
        box_x + box_w / 2, box_y + 3.5 * mm, tax_return.bpe_number or "-"
    )

    jenis = (
        f"Pembetulan ke-{tax_return.correction_no}"
        if tax_return.correction_no > 0
        else "Normal"
    )
    received = (
        tax_return.received_at.strftime("%d-%m-%Y")
        if tax_return.received_at is not None
        else "-"
    )
    submitted = (
        tax_return.submitted_at.strftime("%d-%m-%Y")
        if tax_return.submitted_at is not None
        else "-"
    )
    y = top - 24 * mm
    rows = [
        ("Jenis SPT", tax_return.return_type.value),
        ("Masa Pajak", f"{tax_return.tax_month:02d}-{tax_return.tax_year}"),
        ("Normal / Pembetulan", jenis),
        ("NPWP Penanda Tangan", tax_return.signer_npwp or "-"),
        ("Nama Penanda Tangan", tax_return.signer_name or "-"),
        ("Tanggal Penerimaan", received),
        ("Tanggal Submit SPT", submitted),
        ("Nomor BPE", tax_return.bpe_number or "-"),
        ("Status SPT", _status_label(tax_return)),
    ]
    for k, v in rows:
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(margin, y, k)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(margin + 50 * mm, y, f": {v}")
        y -= 5.6 * mm

    qr_payload = f"BPESPT:{tax_return.bpe_number or tax_return.id}"
    qr = segno.make(qr_payload, error="m")
    qr_buffer = io.BytesIO()
    qr.save(qr_buffer, kind="png", scale=6, border=2)
    qr_buffer.seek(0)
    pdf.drawImage(ImageReader(qr_buffer), margin, y - 28 * mm, 24 * mm, 24 * mm)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(
        margin + 30 * mm, y - 8 * mm, "Pindai untuk verifikasi simulasi di kelas."
    )

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
