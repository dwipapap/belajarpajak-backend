"""Render every bukti potong PDF type in memory and check header + key text."""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess

import pytest

from app.models.enums import SlipStatus, SlipTaxFacility, SlipTaxNature, SlipType
from app.models.slip import WithholdingSlip
from app.services.slip_pdf import render_slip_pdf

TITLES = {
    "bp21": ("BP21", "PASAL 21"),
    "bp23": ("BPPU", "UNIFIKASI"),
    "bp26": ("BP26", "PASAL 26"),
    "bppu": ("BPPU", "UNIFIKASI"),
    "bpmp": ("BPMP", "BULANAN"),
    "bpnr": ("BPNR", "PASAL 26"),
    "sp": ("BPSS", "PENYETORAN SENDIRI"),
    "cy": ("BPCY", "DIGUNGGUNG"),
}


def _slip(slip_type: SlipType, **over) -> WithholdingSlip:
    data = dict(
        slip_type=slip_type,
        tenant_id=1,
        siswa_id=1,
        created_by_id=1,
        status=SlipStatus.issued,
        withholding_number="SIM-TEST0001",
        tax_month=10,
        tax_year=2026,
        withholder_npwp="0024001414211000",
        withholder_name="PT CONTOH",
        recipient_identity_number="1471015008000001",
        recipient_name="FINDA LUKMAN",
        tax_object_code="21-100-01",
        tax_nature=SlipTaxNature.non_final,
        tax_facility=SlipTaxFacility.none,
        gross_income=1234567,
        dpp=1234567,
        rate_basis_points=500,
        income_tax=61728,
        signer_name="Guru Simulasi",
        issued_at=dt.datetime(2026, 11, 10),
        document_date=dt.date(2026, 9, 1),
    )
    data.update(over)
    return WithholdingSlip(**data)


def _text(pdf: bytes, tmp_path) -> str | None:
    if shutil.which("pdftotext") is None:
        return None
    path = tmp_path / "x.pdf"
    path.write_bytes(pdf)
    return subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.parametrize("stype", list(TITLES))
def test_render_each_slip_type(stype: str, tmp_path) -> None:
    pdf = render_slip_pdf(_slip(SlipType(stype)), stype.upper(), "SMK Contoh")
    assert pdf.startswith(b"%PDF")
    text = _text(pdf, tmp_path)
    if text is None:
        pytest.skip("pdftotext unavailable")
    code, phrase = TITLES[stype]
    assert code in text
    assert phrase in text
    assert "SIM-TEST0001" in text
    assert "10-2026" in text
    assert "1.234.567" in text
    assert "10 November 2026" in text
    assert "SMK CONTOH" in text
    for banned in ("Coretax", "DJP", "Direktorat Jenderal", "Kementerian Keuangan"):
        assert banned not in text


def test_render_handles_missing_data(tmp_path) -> None:
    slip = _slip(
        SlipType.sp,
        recipient_identity_number=None,
        recipient_name=None,
        withholder_npwp=None,
        withholder_name=None,
        withholding_number=None,
        signer_name=None,
        issued_at=None,
        document_date=None,
        status=SlipStatus.invalid,
    )
    pdf = render_slip_pdf(slip, "SP")
    assert pdf.startswith(b"%PDF")
    text = _text(pdf, tmp_path)
    if text is not None:
        assert "DIBATALKAN" in text
