"""Smoke tests for simulated e-Bupot BPPU (PPh Unifikasi WPDN/BUT) workflows."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

SISWA_A = "siswa1@smkn1-pku.local"
ADMIN_B = "admin@pcr.local"
BASE = "/api/v1"


def _first_class_id(client: TestClient, headers: dict[str, str]) -> int:
    classes = client.get(f"{BASE}/classes", headers=headers).json()
    assert classes
    return classes[0]["id"]


def _bppu_payload(class_id: int) -> dict:
    """PPh 4 ayat (2) jasa konstruksi example — lands in the SPT pasal_4_2 row."""
    return {
        "class_id": class_id,
        "tax_month": 7,
        "tax_year": 2026,
        "recipient_identity_number": "1234567890123456",
        "recipient_name": "PT PENERIMA BPPU",
        "tax_type": "PPh 4(2)",
        "tax_object_code": "28-100-01",
        "tax_object_name": "Jasa konstruksi",
        "tax_nature": "final",
        "tax_facility": "none",
        "gross_income": 10_000_000,
        "dpp_percent": "100.00",
        "rate_percent": "2.00",
        "kap_kjs": "411128-100",
        "document_type": "Bukti Pembayaran",
        "document_number": "INV/2026/BPPU-1",
        "document_date": "2026-07-01",
        "document_nitku": "1471110802000001000001",
    }


def _import_xml(class_id: int, *, gross_income: str = "10000000") -> str:
    return f"""<?xml version='1.0' encoding='utf-8'?>
<BppuList>
  <Bppu>
    <class_id>{class_id}</class_id>
    <tax_month>7</tax_month>
    <tax_year>2026</tax_year>
    <recipient_identity_number>1234567890123457</recipient_identity_number>
    <recipient_name>PT IMPOR BPPU</recipient_name>
    <tax_type>PPh 23</tax_type>
    <tax_object_code>24-100-01</tax_object_code>
    <tax_object_name>Sewa harta selain tanah/bangunan</tax_object_name>
    <tax_nature>non_final</tax_nature>
    <tax_facility>none</tax_facility>
    <gross_income>{gross_income}</gross_income>
    <dpp_percent>100</dpp_percent>
    <rate_percent>2</rate_percent>
    <kap_kjs>411124-100</kap_kjs>
  </Bppu>
</BppuList>
"""


def test_siswa_can_create_and_issue_bppu(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    created = client.post(f"{BASE}/bppu", headers=headers, json=_bppu_payload(class_id))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "draft"
    assert body["income_tax"] == 200_000
    assert body["withholding_number"] is None

    issued = client.post(f"{BASE}/bppu/{body['id']}/issue", headers=headers)
    assert issued.status_code == 200, issued.text
    issued_body = issued.json()
    assert issued_body["status"] == "issued"
    assert issued_body["withholding_number"].startswith("BPPU-202607-")
    assert issued_body["electronic_signature_status"] == "signed"


def test_bppu_invalid_transition_rejected(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    created = client.post(f"{BASE}/bppu", headers=headers, json=_bppu_payload(class_id)).json()

    # Draft cannot be cancelled, only issued slips can.
    early = client.post(f"{BASE}/bppu/{created['id']}/cancel", headers=headers, json={})
    assert early.status_code == 409

    # Issued slips cannot be issued twice.
    client.post(f"{BASE}/bppu/{created['id']}/issue", headers=headers)
    again = client.post(f"{BASE}/bppu/{created['id']}/issue", headers=headers)
    assert again.status_code == 409


def test_bppu_import_export_xml_round_trip(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    imported = client.post(
        f"{BASE}/bppu/import-xml",
        headers=headers,
        files={"file": ("bppu.xml", _import_xml(class_id).encode(), "application/xml")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1

    exported = client.get(
        f"{BASE}/bppu/export-xml?tax_year=2026&tax_month=7",
        headers=headers,
    )
    assert exported.status_code == 200
    assert "<tax_object_code>24-100-01</tax_object_code>" in exported.text

    bad = client.post(
        f"{BASE}/bppu/import-xml",
        headers=headers,
        files={
            "file": (
                "bad_row.xml",
                _import_xml(class_id, gross_income="not-a-number").encode(),
                "application/xml",
            )
        },
    )
    assert bad.status_code == 200
    bad_body = bad.json()
    assert bad_body["imported"] == 0
    assert bad_body["failed"] == 1
    assert "gross_income" in bad_body["results"][0]["error"]


def test_bppu_bulk_issue_partial(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    first = client.post(f"{BASE}/bppu", headers=headers, json=_bppu_payload(class_id)).json()
    second = client.post(f"{BASE}/bppu", headers=headers, json=_bppu_payload(class_id)).json()

    bulk = client.post(
        f"{BASE}/bppu/bulk-issue",
        headers=headers,
        json={"ids": [first["id"], second["id"], 99_999_999]},
    )
    assert bulk.status_code == 200
    body = bulk.json()
    assert body["issued"] == 2
    assert body["failed"] == 1


def test_bppu_types_are_isolated_despite_shared_table(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)
    created = client.post(f"{BASE}/bppu", headers=headers, json=_bppu_payload(class_id)).json()

    bp26_ids = {
        item["id"] for item in client.get(f"{BASE}/bp26?size=100", headers=headers).json()["items"]
    }
    assert created["id"] not in bp26_ids
    assert client.get(f"{BASE}/bp26/{created['id']}", headers=headers).status_code == 404


def test_other_tenant_admin_cannot_see_bppu(client: TestClient) -> None:
    siswa_headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, siswa_headers)
    created = client.post(
        f"{BASE}/bppu", headers=siswa_headers, json=_bppu_payload(class_id)
    ).json()

    other_admin_headers = auth_headers(client, ADMIN_B)
    listing = client.get(f"{BASE}/bppu?size=100", headers=other_admin_headers)
    assert listing.status_code == 200
    ids = {item["id"] for item in listing.json()["items"]}
    assert created["id"] not in ids


def test_pph_posting_aggregates_bppu(client: TestClient) -> None:
    """PPh Unifikasi: a BPPU slip lands in its KAP-KJS row.

    Uses period 06/2025 — no other test module creates a pph_unifikasi SPT
    there (test_spt.py uses 02-03/2026; PPN uses 2025-12 only), and a past
    year never trips the future-period guard. Get-or-create keeps the test
    re-runnable against the persistent shared test DB.
    """
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    payload = _bppu_payload(class_id)
    payload["tax_month"] = 6
    payload["tax_year"] = 2025
    payload["document_number"] = "INV/2025/BPPU-SPT"
    res = client.post(f"{BASE}/bppu", headers=headers, json=payload)
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["income_tax"] == 200_000
    issued = client.post(f"{BASE}/bppu/{created['id']}/issue", headers=headers)
    assert issued.status_code == 200, issued.text

    spt = client.post(
        f"{BASE}/spt",
        headers=headers,
        json={"return_type": "pph_unifikasi", "tax_month": 6, "tax_year": 2025},
    )
    if spt.status_code == 409:
        existing = client.get(
            f"{BASE}/spt",
            headers=headers,
            params={
                "return_type": "pph_unifikasi",
                "tax_year": 2025,
                "tax_month": 6,
                "size": 100,
            },
        )
        assert existing.status_code == 200, existing.text
        spt_id = existing.json()["items"][0]["id"]
    else:
        assert spt.status_code == 201, spt.text
        spt_id = spt.json()["id"]

    posted = client.post(f"{BASE}/spt/{spt_id}/posting", headers=headers)
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["total_income_tax"] >= 200_000
    kjs_row = next(
        x for x in body["lines"] if x["section"] == "pasal_4_2" and x["code"] == "411128-100"
    )
    assert kjs_row["pemotongan"] >= 200_000
