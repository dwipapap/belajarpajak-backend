import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from tests.conftest import auth_headers
from tests.test_faktur_keluaran import (
    ADMIN,
    OTHER_SISWA,
    SISWA,
    _create,
    _issue,
)
from tests.test_faktur_masukan import _create as _create_masukan

URL = "/api/v1/faktur-keluaran/{}/pdf"


def _text(pdf: bytes) -> str:
    out = subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, check=True
    )
    return out.stdout.decode()


needs_pdftotext = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="no pdftotext")


@needs_pdftotext
def test_issued_invoice_pdf(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers, reference="REF-PDF-01")
    issued = _issue(client, created["id"], headers).json()

    res = client.get(URL.format(created["id"]), headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    number = issued["invoice_number"]
    disposition = res.headers["content-disposition"]
    assert disposition == f'inline; filename="FakturPajak-{number}.pdf"'
    assert res.content.startswith(b"%PDF")

    text = _text(res.content)
    assert "Faktur Pajak" in text
    assert number in text
    assert "PT Pembeli Simulasi" in text
    assert "1.000.000,00" in text
    assert "(Referensi: REF-PDF-01)" in text
    assert "1 dari 1" in text
    assert "DIBATALKAN" not in text
    lowered = text.lower()
    for banned in ("coretax", "djp", "direktorat jenderal pajak", "kementerian keuangan"):
        assert banned not in lowered


def test_draft_invoice_pdf_is_409(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)
    assert client.get(URL.format(created["id"]), headers=headers).status_code == 409


def test_other_siswa_cannot_download_pdf(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)
    _issue(client, created["id"], headers)
    res = client.get(URL.format(created["id"]), headers=auth_headers(client, OTHER_SISWA))
    assert res.status_code == 404


@needs_pdftotext
def test_cancelled_invoice_pdf_is_marked(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)
    _issue(client, created["id"], headers)
    cancel = client.post(
        f"/api/v1/faktur-keluaran/{created['id']}/cancel",
        json={"reason": "Salah input pembeli"},
        headers=auth_headers(client, ADMIN),
    )
    assert cancel.status_code == 200
    res = client.get(URL.format(created["id"]), headers=headers)
    assert res.status_code == 200
    assert "DIBATALKAN" in _text(res.content)


def test_many_lines_paginate(client: TestClient):
    headers = auth_headers(client, SISWA)
    from tests.test_faktur_keluaran import _line

    created = _create(client, headers, lines=[_line(item_name=f"Item {i}") for i in range(20)])
    _issue(client, created["id"], headers)
    res = client.get(URL.format(created["id"]), headers=headers)
    assert res.status_code == 200
    assert res.content.count(b"/Type /Page\n") >= 2 or res.content.count(b"/Type /Page") >= 3


def test_masukan_draft_409_and_other_siswa_404(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create_masukan(client, headers)
    url = f"/api/v1/faktur-masukan/{created['id']}/pdf"
    assert client.get(url, headers=headers).status_code == 409
    assert client.get(url, headers=auth_headers(client, OTHER_SISWA)).status_code == 404
