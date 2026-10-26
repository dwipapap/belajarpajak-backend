"""Smoke tests for BPMP monthly slips, A1/A2 aggregates, and SPT PPh 21/26."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

SISWA_A = "siswa1@smkn1-pku.local"
ADMIN_B = "admin@pcr.local"
BASE = "/api/v1"

IDENTITY = "1234567890123456"


def _first_class_id(client: TestClient, headers: dict[str, str]) -> int:
    classes = client.get(f"{BASE}/classes", headers=headers).json()
    assert classes
    return classes[0]["id"]


def _bpmp_payload(class_id: int, *, month: int = 4, year: int = 2025) -> dict:
    """Monthly permanent-employee slip; TER table unseeded so explicit rate applies."""
    return {
        "class_id": class_id,
        "tax_month": month,
        "tax_year": year,
        "recipient_identity_number": IDENTITY,
        "recipient_name": "PEGAWAI TETAP A",
        "ptkp_status": "TK/0",
        "tax_type": "PPh 21",
        "tax_object_code": "21-100-01",
        "tax_object_name": "Gaji pegawai tetap",
        "tax_nature": "non_final",
        "tax_facility": "none",
        "gross_income": 10_000_000,
        "dpp_percent": "100.00",
        "rate_percent": "5.00",
        "kap_kjs": "411121-100",
        "document_type": "Bukti Pembayaran",
        "document_number": f"PAY/{year}/BPMP-{month}",
        "document_date": f"{year}-{month:02d}-28",
    }


def _issue_two_months(client: TestClient, headers: dict[str, str], class_id: int) -> None:
    for month in (3, 4):
        created = client.post(
            f"{BASE}/bpmp", headers=headers, json=_bpmp_payload(class_id, month=month)
        )
        assert created.status_code == 201, created.text
        issued = client.post(f"{BASE}/bpmp/{created.json()['id']}/issue", headers=headers)
        assert issued.status_code == 200, issued.text


def test_siswa_can_create_and_issue_bpmp_with_explicit_rate(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    created = client.post(f"{BASE}/bpmp", headers=headers, json=_bpmp_payload(class_id))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "draft"
    # TER table unseeded → falls back to dpp x explicit rate_percent.
    assert body["income_tax"] == 500_000
    assert body["withholding_number"] is None

    issued = client.post(f"{BASE}/bpmp/{body['id']}/issue", headers=headers)
    assert issued.status_code == 200, issued.text
    issued_body = issued.json()
    assert issued_body["status"] == "issued"
    assert issued_body["withholding_number"].startswith("SIM-BPMP-202504-")


def test_bpmp_types_are_isolated_despite_shared_table(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)
    created = client.post(f"{BASE}/bpmp", headers=headers, json=_bpmp_payload(class_id)).json()

    bp26_ids = {
        item["id"] for item in client.get(f"{BASE}/bp26?size=100", headers=headers).json()["items"]
    }
    assert created["id"] not in bp26_ids
    assert client.get(f"{BASE}/bp26/{created['id']}", headers=headers).status_code == 404


def test_other_tenant_admin_cannot_see_bpmp(client: TestClient) -> None:
    siswa_headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, siswa_headers)
    created = client.post(
        f"{BASE}/bpmp", headers=siswa_headers, json=_bpmp_payload(class_id)
    ).json()

    other_admin_headers = auth_headers(client, ADMIN_B)
    listing = client.get(f"{BASE}/bpmp?size=100", headers=other_admin_headers)
    assert listing.status_code == 200
    ids = {item["id"] for item in listing.json()["items"]}
    assert created["id"] not in ids


def test_a1_and_a2_aggregate_issued_monthly_slips(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)
    _issue_two_months(client, headers, class_id)

    for form in ("a1", "a2"):
        listing = client.get(f"{BASE}/{form}?tax_year=2025", headers=headers)
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["total"] >= 1
        item = next(x for x in body["items"] if x["recipient_identity_number"] == IDENTITY)
        assert item["form"] == form.upper()
        assert item["annual_gross"] >= 20_000_000
        assert item["annual_tax"] >= 1_000_000
        assert {3, 4} <= set(item["months_covered"])
        assert item["ter_category"] == "A"

        detail = client.get(f"{BASE}/{form}/{IDENTITY}?tax_year=2025", headers=headers)
        assert detail.status_code == 200, detail.text
        rows = detail.json()["rows"]
        assert len(rows) >= 2
        assert {row["tax_month"] for row in rows} >= {3, 4}
        assert sum(row["income_tax"] for row in rows) >= 1_000_000


def test_a1_detail_404_for_unknown_identity(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    res = client.get(f"{BASE}/a1/0000000000000000?tax_year=2025", headers=headers)
    assert res.status_code == 404


def test_pph_21_26_posting_aggregates_bpmp(client: TestClient) -> None:
    """SPT PPh 21/26: an issued BPMP slip lands in its pasal_21 KJS row.

    Return type ``pph_21_26`` is new, so period 04/2025 cannot collide with any
    other module's SPT even though the slips share the tenant namespace.
    """
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    created = client.post(f"{BASE}/bpmp", headers=headers, json=_bpmp_payload(class_id))
    assert created.status_code == 201, created.text
    issued = client.post(f"{BASE}/bpmp/{created.json()['id']}/issue", headers=headers)
    assert issued.status_code == 200, issued.text

    spt = client.post(
        f"{BASE}/spt",
        headers=headers,
        json={"return_type": "pph_21_26", "tax_month": 4, "tax_year": 2025},
    )
    if spt.status_code == 409:
        # Re-runnable against the persistent shared test DB (same get-or-create
        # pattern as the BPPU SPT test): post to the existing period SPT.
        existing = client.get(
            f"{BASE}/spt",
            headers=headers,
            params={
                "return_type": "pph_21_26",
                "tax_year": 2025,
                "tax_month": 4,
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
    assert body["total_income_tax"] >= 500_000
    assert body["amount_due"] == body["total_income_tax"]
    kjs_row = next(
        x for x in body["lines"] if x["section"] == "pasal_21" and x["code"] == "411121-100"
    )
    assert kjs_row["pemotongan"] >= 500_000
