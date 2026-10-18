"""BPE simulasi PDF rendering — printable withholding slip (Bukti Potong).

Layout mirrors the learning document shell: SIMULASI header, document identity
(BPE-SIM number, period, status), withholder/recipient blocks, tax calculation
block (DPP/PPh/method), signer block, and a QR code carrying the
withholding number. Never uses real-institution branding.
"""

from __future__ import annotations

import io

import segno
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.models.slip import WithholdingSlip

_INK = (0.1, 0.1, 0.14)
_GOLD = (0.94, 0.74, 0.15)

_METHOD_LABEL = {
    "ter": "SIM-TER Jan–Nov",
    "progresif": "Progresif Desember",
    "flat": "Tarif flat objek",
    "flat_x2": "Flat x2 (tanpa NPWP)",
    "treaty": "Treaty P3B/SKD",
    "nol_skb": "Nol (SKB/Tarif 0%)",
}


def render_slip_pdf(slip: WithholdingSlip, label: str) -> bytes:
    """Render the simulated BPE as PDF bytes."""
    buffer = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"BPE Simulasi {slip.withholding_number or label}")

    margin = 25 * mm
    top = height - 30 * mm

    pdf.setFillColorRGB(*_INK)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(margin, top, "SIMULATOR PEMBELAJARAN PAJAK")
    pdf.setFont("Helvetica", 10.5)
    pdf.drawString(margin, top - 6 * mm, "Bukti Potong Simulasi (BPE-SIM) — bukan dokumen resmi")

    box_w, box_h = 72 * mm, 16 * mm
    box_x, box_y = width - margin - box_w, top - 6 * mm
    pdf.setStrokeColorRGB(*_INK)
    pdf.setLineWidth(1.2)
    pdf.rect(box_x, box_y, box_w, box_h)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(box_x + box_w / 2, box_y + box_h - 5.5 * mm, f"BPE-SIM {label}")
    pdf.setFont("Courier-Bold", 10)
    pdf.drawCentredString(box_x + box_w / 2, box_y + 3.5 * mm, slip.withholding_number or "-")

    y = top - 24 * mm
    rows = [
        ("Masa Pajak", f"{slip.tax_month:02d}-{slip.tax_year}"),
        ("Status", slip.status.value if hasattr(slip.status, "value") else str(slip.status)),
        ("Kode Objek", slip.tax_object_code),
        ("Sifat / Fasilitas", f"{slip.tax_nature} / {slip.tax_facility}"),
        ("Metode Hitung", _METHOD_LABEL.get(slip.calculation_method or "", "-")),
        ("TER", slip.ter_category or "-"),
    ]
    for k, v in rows:
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(margin, y, k)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(margin + 45 * mm, y, f": {v}")
        y -= 5.6 * mm

    y -= 2 * mm
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(margin, y, "Pemotong / Penerima:")
    y -= 6 * mm
    pdf.setFont("Helvetica", 10)
    for k, v in [
        ("Pemotong", f"{slip.withholder_name or '-'} ({slip.withholder_npwp or '-'})"),
        ("Penerima", f"{slip.recipient_name} ({slip.recipient_identity_number})"),
        ("DPP", f"Rp{slip.dpp:,}".replace(",", ".")),
        ("PPh", f"Rp{slip.income_tax:,}".replace(",", ".")),
        ("KAP-KJS", slip.kap_kjs or "-"),
        ("Penandatangan", slip.signer_name or "-"),
    ]:
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(margin, y, k)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(margin + 45 * mm, y, f": {v}")
        y -= 5.6 * mm

    qr_payload = f"BPESIM:{slip.withholding_number or slip.id}:{label}"
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
