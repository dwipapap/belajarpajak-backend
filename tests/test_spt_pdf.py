"""SPT Masa document PDF endpoint: GET /spt/{id}/pdf for the three return types."""

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.db import engine
from tests.conftest import auth_headers
from tests.test_spt import (
    BASE,
    OTHER_SISWA,
    SISWA,
    _create_spt,
    _make_bp23_issued,
    _make_keluaran_issued,
    _pernyataan,
    _posting,
)

# Periods live in 2023 so they never collide with test_spt.py (which owns 2024-2026).
YEAR = 2023

_TABLES = (
    "billing_codes",
    "tax_return_lines",
    "tax_returns",
    "tax_input_lines",
    "tax_input_invoices",
    "tax_invoice_lines",
    "tax_invoices",
    "withholding_slips",
)


@pytest.fixture(scope="module", autouse=True)
def _clean_tables() -> None:
    with Session(engine) as session:
        for table in _TABLES:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        session.commit()


def _pdf_text(content: bytes, tmp_path) -> str:
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext not installed")
    path = tmp_path / "spt.pdf"
    path.write_bytes(content)
    return subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    ).stdout


def _get_pdf(client: TestClient, headers: dict, spt_id: int):
    return client.get(f"{BASE}/spt/{spt_id}/pdf", headers=headers)


def test_spt_ppn_pdf(client: TestClient, tmp_path):
    headers = auth_headers(client, SISWA)
    _make_keluaran_issued(client, headers, 3, year=YEAR)
    spt = _create_spt(client, headers, 3, year=YEAR)
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])

    res = _get_pdf(client, headers, spt["id"])
    assert res.status_code == 200, res.text
    assert res.content.startswith(b"%PDF")
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"] == f'inline; filename="SPT-ppn-{YEAR}-03.pdf"'
    out = _pdf_text(res.content, tmp_path)
    assert "SPT MASA PPN" in out
    assert "PENGHITUNGAN PPN KURANG BAYAR/LEBIH BAYAR" in out
    assert "Halaman 2" in out
    assert "Maret 2023" in out
    assert "NORMAL" in out
    for banned in ("Coretax", "DJP", "Direktorat Jenderal", "Kementerian"):
        assert banned not in out


def test_spt_pph_unifikasi_pdf(client: TestClient, tmp_path):
    headers = auth_headers(client, SISWA)
    # bp23 helper issues in 2026; the SPT only needs a posted snapshot of its own period.
    _make_bp23_issued(client, headers, 2)
    spt = _create_spt(client, headers, 2, return_type="pph_unifikasi", year=YEAR)
    _posting(client, headers, spt["id"])

    res = _get_pdf(client, headers, spt["id"])
    assert res.status_code == 200, res.text
    assert res.content.startswith(b"%PDF")
    out = _pdf_text(res.content, tmp_path)
    assert "SURAT PEMBERITAHUAN MASA PPH UNIFIKASI" in out
    assert "KJS : 411124-100" in out
    assert "Total Pajak" in out
    assert "Februari 2023" in out


def test_spt_pph_21_26_pdf(client: TestClient, tmp_path):
    headers = auth_headers(client, SISWA)
    spt = _create_spt(client, headers, 4, return_type="pph_21_26", year=YEAR)
    _posting(client, headers, spt["id"])

    res = _get_pdf(client, headers, spt["id"])
    assert res.status_code == 200, res.text
    assert res.content.startswith(b"%PDF")
    out = _pdf_text(res.content, tmp_path)
    assert "PASAL 21 DAN/ATAU PASAL 26" in out
    assert "PPh Pasal 21 yang Dipotong" in out
    assert "PPh Pasal 26 Ditanggung Pemerintah" in out
    assert "D. PERNYATAAN DAN TANDA TANGAN PEMOTONG" in out


def test_spt_pdf_not_ready_is_409(client: TestClient):
    headers = auth_headers(client, SISWA)
    spt = _create_spt(client, headers, 5, year=YEAR)
    res = _get_pdf(client, headers, spt["id"])
    assert res.status_code == 409
    assert "Posting" in res.json()["detail"]


def test_spt_pdf_other_siswa_gets_404(client: TestClient):
    owner = auth_headers(client, SISWA)
    spt = _create_spt(client, owner, 6, year=YEAR)
    _posting(client, owner, spt["id"])
    assert _get_pdf(client, auth_headers(client, OTHER_SISWA), spt["id"]).status_code == 404
    assert _get_pdf(client, owner, 999_999).status_code == 404
