"""Layanan Mandiri Kode Billing tests (spt_id NULL, YAGNI scope)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

SISWA = "siswa1@smkn1-pku.local"
OTHER_SISWA = "siswa4@smkn1-pku.local"
ADMIN = "admin@smkn1-pku.local"

BASE = "/api/v1"


def _mandiri_payload(**overrides) -> dict:
    payload = {
        "kap_kjs": "411124-100",
        "masa_pajak": "052026",
        "amount": 1_500_000,
        "uraian": "Setoran mandiri PPh 23",
    }
    payload.update(overrides)
    return payload


def test_create_mandiri_valid_then_pay_without_spt(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    res = client.post(f"{BASE}/billings", json=_mandiri_payload(), headers=headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["spt_id"] is None
    assert body["status"] == "unpaid"
    assert body["kap_kjs"] == "411124-100"
    assert body["masa_pajak"] == "052026"
    assert body["uraian"] == "Setoran mandiri PPh 23"
    assert len(body["code"]) == 15 and body["code"].isdigit()

    pay = client.post(f"{BASE}/billings/{body['id']}/pay", headers=headers)
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"


def test_create_mandiri_rejects_unknown_kjs(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    res = client.post(
        f"{BASE}/billings", json=_mandiri_payload(kap_kjs="999999-999"), headers=headers
    )
    assert res.status_code == 422


def test_create_mandiri_rejects_future_period(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    res = client.post(
        f"{BASE}/billings", json=_mandiri_payload(masa_pajak="122099"), headers=headers
    )
    assert res.status_code == 422


def test_create_mandiri_rejects_bad_amount(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    res = client.post(f"{BASE}/billings", json=_mandiri_payload(amount=0), headers=headers)
    assert res.status_code == 422


def test_mandiri_hidden_from_other_siswa_and_pdf_200(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    created = client.post(f"{BASE}/billings", json=_mandiri_payload(), headers=headers).json()

    intruder = auth_headers(client, OTHER_SISWA)
    assert client.get(f"{BASE}/billings/{created['id']}", headers=intruder).status_code == 404
    assert (
        client.post(f"{BASE}/billings/{created['id']}/pay", headers=intruder).status_code
        == 404
    )

    pdf = client.get(f"{BASE}/billings/{created['id']}/pdf", headers=headers)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")


def test_admin_can_create_for_same_tenant_siswa(client: TestClient) -> None:
    admin_headers = auth_headers(client, ADMIN)
    siswa_headers = auth_headers(client, SISWA)
    me = client.get(f"{BASE}/auth/me", headers=siswa_headers).json()
    res = client.post(
        f"{BASE}/billings",
        json=_mandiri_payload(siswa_id=me["id"], uraian=None),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["siswa_id"] == me["id"]
    assert res.json()["uraian"] is None
