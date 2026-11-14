"""SP (setor sendiri) and CY (digunggung) have no counterparty: DPP is entered
directly and PPh = DPP x tarif, without the no-NPWP x2 penalty."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import auth_headers

SISWA_A = "siswa1@smkn1-pku.local"
BASE = "/api/v1"


def _class_id(client: TestClient, headers: dict[str, str]) -> int:
    return client.get(f"{BASE}/classes", headers=headers).json()[0]["id"]


def _payload(class_id: int, code: str, rate: str, kap: str) -> dict:
    return {
        "class_id": class_id,
        "tax_month": 7,
        "tax_year": 2026,
        "tax_type": "PPh 23",
        "tax_object_code": code,
        "tax_object_name": "Objek uji",
        "tax_nature": "non_final",
        "tax_facility": "none",
        "dpp": 10_000_000,
        "rate_percent": rate,
        "kap_kjs": kap,
        # Sent by mistake: must be ignored for SP/CY.
        "recipient_identity_number": "1234567890123456",
        "recipient_name": "TIDAK DIPAKAI",
    }


@pytest.mark.parametrize(
    ("module", "code", "rate", "kap", "tax"),
    [
        ("sp", "28-403-02", "10.00", "411128-403", 1_000_000),
        ("cy", "27-102-03", "10.00", "411127-110", 1_000_000),
    ],
)
def test_sp_cy_without_counterparty(
    client: TestClient, module: str, code: str, rate: str, kap: str, tax: int
) -> None:
    headers = auth_headers(client, SISWA_A)
    created = client.post(
        f"{BASE}/{module}",
        headers=headers,
        json=_payload(_class_id(client, headers), code, rate, kap),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["recipient_identity_number"] is None
    assert body["recipient_name"] is None
    assert body["dpp"] == 10_000_000
    assert body["income_tax"] == tax
    assert body["calculation_method"] == "flat"

    updated = client.patch(
        f"{BASE}/{module}/{body['id']}", headers=headers, json={"dpp": 5_000_000}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["dpp"] == 5_000_000
    assert updated.json()["income_tax"] == tax // 2

    issued = client.post(
        f"{BASE}/{module}/{body['id']}/issue",
        headers=headers,
        json={"kode_otorisasi": "KO-SIM-123"},
    )
    assert issued.status_code == 200, issued.text
    assert client.get(f"{BASE}/{module}/{body['id']}/pdf", headers=headers).status_code == 200
    assert client.get(f"{BASE}/dokumen-saya?jenis=bukti_potong", headers=headers).status_code == 200


def test_bppu_still_requires_counterparty(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    payload = _payload(_class_id(client, headers), "24-101-01", "15.00", "411124-100")
    payload.pop("recipient_identity_number")
    payload.pop("recipient_name")
    resp = client.post(f"{BASE}/bppu", headers=headers, json=payload)
    assert resp.status_code == 422


def test_bppu_accepts_direct_dpp(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    payload = _payload(_class_id(client, headers), "24-101-01", "15.00", "411124-100")
    payload["tax_type"] = "PPh 23"
    created = client.post(f"{BASE}/bppu", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["recipient_name"] == "TIDAK DIPAKAI"  # BPPU keeps its counterparty
    assert body["dpp"] == 10_000_000
    assert body["dpp_percent"] == 100
    assert body["income_tax"] == 1_500_000

    updated = client.patch(f"{BASE}/bppu/{body['id']}", headers=headers, json={"dpp": 2_000_000})
    assert updated.status_code == 200, updated.text
    assert updated.json()["income_tax"] == 300_000
