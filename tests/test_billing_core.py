"""Billing core tests: A2 (ntpn/bpn) + A3 (kanal) + A4 (411618) + B4 (lines)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services import kjs as kjs_service
from tests.conftest import auth_headers

SISWA = "siswa1@smkn1-pku.local"

BASE = "/api/v1"


def _mandiri(**overrides) -> dict:
    payload = {
        "kap_kjs": "411124-100",
        "masa_pajak": "052026",
        "amount": 1_500_000,
    }
    payload.update(overrides)
    return payload


def test_pay_generates_ntpn_bpn_and_default_kanal(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    created = client.post(f"{BASE}/billings", json=_mandiri(), headers=headers).json()
    pay = client.post(f"{BASE}/billings/{created['id']}/pay", json={}, headers=headers)
    assert pay.status_code == 200, pay.text
    body = pay.json()
    assert body["status"] == "paid"
    assert body["ntpn"] is not None and len(body["ntpn"]) == 16 and body["ntpn"].isdigit()
    assert body["no_bpn"] == f"BPN{body['code']}"
    assert body["kanal"] == "teller"
    assert "kanal_label" in body and body["kanal_label"]
    assert body["ntpn"] == kjs_service.ntpn_for_code(body["code"])
    # re-fetch persists
    again = client.get(f"{BASE}/billings/{created['id']}", headers=headers).json()
    assert again["ntpn"] == body["ntpn"]


def test_pay_stores_kanal_mbanking(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    created = client.post(f"{BASE}/billings", json=_mandiri(), headers=headers).json()
    pay = client.post(
        f"{BASE}/billings/{created['id']}/pay", json={"kanal": "mbanking"}, headers=headers
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["kanal"] == "mbanking"


def test_pay_rejects_unknown_kanal(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    created = client.post(f"{BASE}/billings", json=_mandiri(), headers=headers).json()
    res = client.post(
        f"{BASE}/billings/{created['id']}/pay", json={"kanal": "ovo"}, headers=headers
    )
    assert res.status_code == 422


def test_mandiri_whitelist_411618_deposit(client: TestClient) -> None:
    assert kjs_service.is_mandiri_kjs("411618-100")
    headers = auth_headers(client, SISWA)
    res = client.post(f"{BASE}/billings", json=_mandiri(kap_kjs="411618-100"), headers=headers)
    assert res.status_code == 201, res.text
    assert res.json()["lines"] and res.json()["lines"][0]["kap_kjs"] == "411618-100"


def test_mandiri_creates_single_line_total_matches(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    res = client.post(f"{BASE}/billings", json=_mandiri(amount=750_000), headers=headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["lines"]) == 1
    assert sum(line["amount"] for line in body["lines"]) == body["amount"]


def test_billing_pdf_renders_200(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    created = client.post(f"{BASE}/billings", json=_mandiri(), headers=headers).json()
    client.post(f"{BASE}/billings/{created['id']}/pay", json={}, headers=headers)
    pdf = client.get(f"{BASE}/billings/{created['id']}/pdf", headers=headers)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
