"""PDF watermark verification across all generated document PDFs."""

import shutil
import subprocess
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session

from app.db import engine
from app.models.billing import BillingCode
from app.models.enums import BillingStatus, SlipStatus
from app.models.slip import WithholdingSlip
from app.models.tax_invoice import (
    TaxInvoice,
    TaxInvoiceKind,
    TaxInvoiceLine,
    TaxInvoiceStatus,
)
from app.models.tax_return import TaxReturn, TaxReturnStatus, TaxReturnType
from app.services.billing_pdf import render_billing_pdf
from app.services.faktur_pdf import render_faktur_pdf
from app.services.pdf_kit import WATERMARK_MAIN, WATERMARK_SUB
from app.services.slip_pdf import render_slip_pdf
from app.services.spt_bpe_pdf import render_spt_bpe_pdf
from app.services.spt_pdf import render_spt_pdf


def _pdf_text(data: bytes) -> str:
    assert data[:5] == b"%PDF-"
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not available")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        path = Path(f.name)
    try:
        res = subprocess.run(
            ["pdftotext", "-raw", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        return res.stdout
    finally:
        path.unlink(missing_ok=True)


def _assert_watermark(text: str) -> None:
    assert WATERMARK_MAIN in text
    assert WATERMARK_SUB in text


@pytest.mark.parametrize(
    "slip_type",
    ["bp21", "bp26", "bppu", "bpmp", "bpnr", "sp", "cy"],
)
def test_slip_pdf_has_learning_watermark(slip_type: str):
    slip = WithholdingSlip(
        id=1,
        tenant_id=1,
        siswa_id=1,
        slip_type=slip_type,
        slip_number=f"{slip_type.upper()}/2025/05/00001",
        status=SlipStatus.issued,
        tax_month=5,
        tax_year=2025,
        recipient_name="PT Penerima Simulasi",
        recipient_npwp="0123456789012345",
        tax_object_code="24-104-01",
        tax_object_name="Jasa Konsultan",
        kap_kjs="411124-104",
        gross_amount=Decimal("10000000"),
        tax_base_amount=Decimal("10000000"),
        tax_rate_percent=Decimal("2"),
        income_tax_amount=Decimal("200000"),
        document_date=date(2025, 5, 15),
        electronic_signature_status="signed",
        signer_name="Budi Santoso",
        issued_at=datetime(2025, 5, 15, 10, 0, tzinfo=UTC),
    )
    pdf = render_slip_pdf(slip, slip_type.upper(), tenant_name="PT Siswa Simulasi")
    text = _pdf_text(pdf)
    _assert_watermark(text)


def test_faktur_pdf_normal_and_cancelled_watermarks():
    inv = TaxInvoice(
        id=10,
        tenant_id=1,
        siswa_id=1,
        invoice_number="01002500000000001",
        transaction_code="01",
        status=TaxInvoiceStatus.issued,
        kind=TaxInvoiceKind.normal,
        tax_month=5,
        tax_year=2025,
        invoice_date=date(2025, 5, 10),
        seller_name="PT Penjual Simulasi",
        seller_npwp="0123456789012345",
        buyer_name="PT Pembeli Simulasi",
        buyer_npwp="9876543210987654",
        total_dpp=Decimal("5000000"),
        total_dpp_other=Decimal("5000000"),
        total_ppn=Decimal("600000"),
        total_ppnbm=Decimal("0"),
        signer_name="Budi Santoso",
        electronic_signature_status="signed",
    )
    lines = [
        TaxInvoiceLine(
            id=1,
            invoice_id=10,
            line_no=1,
            item_kind="barang",
            item_code="BRG-01",
            item_name="Laptop Pembelajaran",
            unit="Unit",
            unit_price=Decimal("5000000"),
            quantity=Decimal("1"),
            discount=Decimal("0"),
            dpp=Decimal("5000000"),
            dpp_other=Decimal("5000000"),
            ppn_rate=Decimal("12"),
            ppn=Decimal("600000"),
            ppnbm_rate=Decimal("0"),
            ppnbm=Decimal("0"),
        )
    ]
    normal_text = _pdf_text(render_faktur_pdf(inv, lines))
    _assert_watermark(normal_text)
    assert "DIBATALKAN" not in normal_text

    inv.status = TaxInvoiceStatus.invalid
    cancelled_text = _pdf_text(render_faktur_pdf(inv, lines))
    _assert_watermark(cancelled_text)
    assert "DIBATALKAN" in cancelled_text


def test_billing_and_spt_bpe_pdf_have_learning_watermark():
    record = BillingCode(
        id=999999,
        tenant_id=1,
        siswa_id=1,
        code="091234567890123",
        kap_kjs="411124-100",
        masa_pajak="052025",
        amount=200000,
        status=BillingStatus.paid,
        ntpn="A1B2C3D4E5F6G7H8",
        paid_at=datetime(2025, 5, 20, 9, 0, tzinfo=UTC),
    )
    with Session(engine) as session:
        billing_text = _pdf_text(render_billing_pdf(session, record))
    _assert_watermark(billing_text)

    tr = TaxReturn(
        id=1,
        tenant_id=1,
        siswa_id=1,
        return_type=TaxReturnType.ppn,
        tax_month=5,
        tax_year=2025,
        revision_number=0,
        status=TaxReturnStatus.reported,
        bpe_number="BPE-202505-00001",
        submitted_at=datetime(2025, 5, 25, 11, 0, tzinfo=UTC),
        signer_name="Siswa Simulasi",
    )
    bpe_text = _pdf_text(render_spt_bpe_pdf(tr))
    _assert_watermark(bpe_text)


@pytest.mark.parametrize(
    "return_type",
    [TaxReturnType.ppn, TaxReturnType.pph_unifikasi, TaxReturnType.pph_21_26],
)
def test_spt_pdf_has_learning_watermark_on_every_page(return_type: TaxReturnType):
    tr = TaxReturn(
        id=2,
        tenant_id=1,
        siswa_id=1,
        return_type=return_type,
        tax_month=5,
        tax_year=2025,
        revision_number=0,
        status=TaxReturnStatus.reported,
        bpe_number="BPE-202505-00002",
        submitted_at=datetime(2025, 5, 25, 11, 0, tzinfo=UTC),
        signer_name="Siswa Simulasi",
        output_tax=1200000,
        input_tax=600000,
        amount_due=600000,
    )
    pdf = render_spt_pdf(
        tr,
        lines=[],
        tenant_name="SMK Pajak Nusantara",
        taxpayer_name="Siswa Simulasi",
    )
    text = _pdf_text(pdf)
    _assert_watermark(text)
    if return_type == TaxReturnType.ppn:
        assert text.count(WATERMARK_MAIN) >= 2
        assert text.count(WATERMARK_SUB) >= 2
