"""BPNR (PPh 26 foreign recipient) form parity: foreign TIN, new fields, deemed neto."""

from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.models.enums import SlipStatus, SlipTaxFacility, SlipTaxNature, SlipType
from app.models.slip import WithholdingSlip
from app.services.slip_pdf import render_slip_pdf
from tests.conftest import auth_headers

SISWA_A = "siswa1@smkn1-pku.local"
BASE = "/api/v1"


def _class_id(client: TestClient, headers: dict[str, str]) -> int:
    return client.get(f"{BASE}/classes", headers=headers).json()[0]["id"]


def _bpnr_payload(class_id: int, **over) -> dict:
    data = {
        "class_id": class_id,
        "tax_month": 7,
        "tax_year": 2026,
        "recipient_identity_number": "SG-123/AB.9",
        "recipient_name": "ACME PTE LTD",
        "recipient_country": "Singapura",
        "recipient_birth_date": "1980-05-17",
        "recipient_birth_place": "Singapore",
        "recipient_passport_number": "E1234567X",
        "recipient_kitas_number": "2C71AB1234",
        "tax_type": "PPh 26",
        "tax_object_code": "27-100-01",
        "tax_nature": "final",
        "tax_facility": "none",
        "gross_income": 10_000_000,
        "dpp_percent": "100.00",
        "rate_percent": "20.00",
        "payment_method": "Transfer / Pemindahbukuan",
        "sp2d_number": "SP2D-001",
        "document_type": "Bukti Pembayaran",
        "document_number": "INV/2026/NR-1",
        "document_date": "2026-07-01",
    }
    data.update(over)
    return data


def test_bpnr_accepts_foreign_tin_and_round_trips_new_fields(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    created = client.post(
        f"{BASE}/bpnr", headers=headers, json=_bpnr_payload(_class_id(client, headers))
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["recipient_identity_number"] == "SG-123/AB.9"
    assert body["recipient_country"] == "Singapura"
    assert body["recipient_birth_date"] == "1980-05-17"
    assert body["recipient_birth_place"] == "Singapore"
    assert body["recipient_passport_number"] == "E1234567X"
    assert body["recipient_kitas_number"] == "2C71AB1234"
    assert body["payment_method"] == "Transfer / Pemindahbukuan"
    assert body["sp2d_number"] == "SP2D-001"

    fetched = client.get(f"{BASE}/bpnr/{body['id']}", headers=headers).json()
    assert fetched["recipient_country"] == "Singapura"

    patched = client.patch(
        f"{BASE}/bpnr/{body['id']}",
        headers=headers,
        json={"recipient_country": "Jepang", "sp2d_number": None, "payment_method": "Tunai"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["recipient_country"] == "Jepang"
    assert patched.json()["sp2d_number"] is None
    assert patched.json()["payment_method"] == "Tunai"


@pytest.mark.parametrize("tin", ["", "A" * 31, "AB 12", "AB#12", "AB_12"])
def test_bpnr_rejects_malformed_foreign_tin(client: TestClient, tin: str) -> None:
    headers = auth_headers(client, SISWA_A)
    resp = client.post(
        f"{BASE}/bpnr",
        headers=headers,
        json=_bpnr_payload(_class_id(client, headers), recipient_identity_number=tin),
    )
    assert resp.status_code == 422


def test_bppu_still_requires_16_digit_identity(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    payload = {
        "class_id": _class_id(client, headers),
        "tax_month": 7,
        "tax_year": 2026,
        "recipient_identity_number": "SG-123/AB.9",
        "recipient_name": "PT PENERIMA BPPU",
        "tax_type": "PPh 4(2)",
        "tax_object_code": "28-409-10",
        "tax_nature": "final",
        "tax_facility": "none",
        "gross_income": 10_000_000,
        "dpp_percent": "100.00",
        "rate_percent": "2.00",
    }
    resp = client.post(f"{BASE}/bppu", headers=headers, json=payload)
    assert resp.status_code == 422
    assert "16 digit" in resp.json()["detail"]


def test_bpnr_deemed_neto_affects_tax(client: TestClient) -> None:
    """tax = bruto x neto% x tarif%: 10.000.000 x 50% x 20% = 1.000.000."""
    headers = auth_headers(client, SISWA_A)
    cid = _class_id(client, headers)
    resp = client.post(
        f"{BASE}/bpnr", headers=headers, json=_bpnr_payload(cid, dpp_percent="50.00")
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dpp"] == 5_000_000
    assert body["income_tax"] == 1_000_000

    updated = client.patch(
        f"{BASE}/bpnr/{body['id']}", headers=headers, json={"dpp_percent": "25.00"}
    )
    assert updated.json()["dpp"] == 2_500_000
    assert updated.json()["income_tax"] == 500_000


def test_bpnr_pdf_renders_new_fields(tmp_path) -> None:
    slip = WithholdingSlip(
        slip_type=SlipType.bpnr,
        tenant_id=1,
        siswa_id=1,
        created_by_id=1,
        status=SlipStatus.issued,
        withholding_number="SIM-BPNR-1",
        tax_month=7,
        tax_year=2026,
        recipient_identity_number="SG-123/AB.9",
        recipient_name="ACME PTE LTD",
        recipient_country="Singapura",
        recipient_birth_date=dt.date(1980, 5, 17),
        recipient_passport_number="E1234567X",
        payment_method="Tunai",
        tax_object_code="27-100-01",
        tax_nature=SlipTaxNature.final,
        tax_facility=SlipTaxFacility.none,
        gross_income=10_000_000,
        dpp=5_000_000,
        dpp_rate_basis_points=5000,
        rate_basis_points=2000,
        income_tax=1_000_000,
        issued_at=dt.datetime(2026, 7, 10),
    )
    pdf = render_slip_pdf(slip, "BPNR", "SMK Contoh")
    assert pdf.startswith(b"%PDF")
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext unavailable")
    path = tmp_path / "x.pdf"
    path.write_bytes(pdf)
    text = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    ).stdout
    for needle in ("Singapura", "E1234567X", "Tunai", "NEGARA", "50"):
        assert needle in text
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True).stdout
    assert re.search(r"Pages:\s+1\b", info)
