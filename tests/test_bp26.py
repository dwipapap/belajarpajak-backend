"""Smoke tests for simulated e-Bupot BP26 workflows."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.routers.slips import MAX_IMPORT_BYTES, MAX_IMPORT_ROWS
from tests.conftest import auth_headers

ADMIN_B = "admin@pcr.local"
GURU_A = "guru1@smkn1-pku.local"
SISWA_A = "siswa1@smkn1-pku.local"


def _first_class_id(client: TestClient, headers: dict[str, str]) -> int:
    classes = client.get("/api/v1/classes", headers=headers).json()
    assert classes
    return classes[0]["id"]


def _bp26_payload(class_id: int) -> dict:
    return {
        "class_id": class_id,
        "tax_month": 7,
        "tax_year": 2026,
        "recipient_identity_number": "9990000000000001",
        "recipient_name": "JOHN FOREIGN DOE",
        "tax_type": "PPh 26",
        "tax_object_code": "27-100-01",
        "tax_object_name": "Dividen yang diterima WPLN",
        "tax_nature": "final",
        "tax_facility": "none",
        "gross_income": 10_000_000,
        "dpp_percent": "100.00",
        "rate_percent": "20.00",
        "kap_kjs": "411127-100",
        "document_type": "Bukti Pembayaran",
        "document_number": "INV/2026/026",
        "document_date": "2026-07-01",
        "document_nitku": "1471110802000001000001",
    }


def _import_xml(
    class_id: int,
    *,
    gross_income: str = "10000000",
    tax_nature: str = "final",
    treaty_rate: str = "",
) -> str:
    return f"""<?xml version='1.0' encoding='utf-8'?>
<Bp26List>
  <Bp26>
    <class_id>{class_id}</class_id>
    <tax_month>7</tax_month>
    <tax_year>2026</tax_year>
    <recipient_identity_number>9990000000000002</recipient_identity_number>
    <recipient_name>JANE FOREIGN DOE</recipient_name>
    <tax_type>PPh 26</tax_type>
    <tax_object_code>27-100-02</tax_object_code>
    <tax_object_name>Bunga yang diterima WPLN</tax_object_name>
    <tax_nature>{tax_nature}</tax_nature>
    <tax_facility>none</tax_facility>
    <gross_income>{gross_income}</gross_income>
    <dpp_percent>100</dpp_percent>
    <rate_percent>20</rate_percent>
    <negara_treaty>SGP</negara_treaty>
    <pasal_treaty>10</pasal_treaty>
    <nomor_skd>SKD-IMPORT-1</nomor_skd>
    <tarif_treaty_basis_points>{treaty_rate}</tarif_treaty_basis_points>
  </Bp26>
</Bp26List>
"""


def test_siswa_can_create_and_issue_bp26(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    created = client.post("/api/v1/bp26", headers=headers, json=_bp26_payload(class_id))
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "draft"
    assert body["income_tax"] == 2_000_000
    assert body["withholding_number"] is None

    issued = client.post(f"/api/v1/bp26/{body['id']}/issue", headers=headers)
    assert issued.status_code == 200
    issued_body = issued.json()
    assert issued_body["status"] == "issued"
    assert issued_body["withholding_number"].startswith("BP26-202607-")
    assert issued_body["electronic_signature_status"] == "signed"


def test_bp26_treaty_overrides_non_final_rate(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)
    payload = _bp26_payload(class_id)
    payload["tax_nature"] = "non_final"
    payload["negara_treaty"] = "SGP"
    payload["pasal_treaty"] = "10"
    payload["nomor_skd"] = "SKD-2026-001"
    payload["tarif_treaty_basis_points"] = 1000

    created = client.post("/api/v1/bp26", headers=headers, json=payload)

    assert created.status_code == 201
    body = created.json()
    assert body["income_tax"] == 1_000_000
    assert body["negara_treaty"] == "SGP"


def test_bp26_final_ignores_treaty(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)
    payload = _bp26_payload(class_id)
    payload["negara_treaty"] = "SGP"
    payload["tarif_treaty_basis_points"] = 1000

    created = client.post("/api/v1/bp26", headers=headers, json=payload)

    assert created.status_code == 201
    assert created.json()["income_tax"] == 2_000_000


def test_bp26_update_treaty_recalculates_tax(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)
    payload = _bp26_payload(class_id)
    payload["tax_nature"] = "non_final"
    created = client.post("/api/v1/bp26", headers=headers, json=payload).json()

    updated = client.patch(
        f"/api/v1/bp26/{created['id']}",
        headers=headers,
        json={"negara_treaty": "JPN", "tarif_treaty_basis_points": 500},
    )

    assert updated.status_code == 200
    assert updated.json()["income_tax"] == 500_000


def test_siswa_can_delete_draft_but_not_issued(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    draft = client.post("/api/v1/bp26", headers=headers, json=_bp26_payload(class_id)).json()
    deleted = client.delete(f"/api/v1/bp26/{draft['id']}", headers=headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/bp26/{draft['id']}", headers=headers).status_code == 404

    issued = client.post("/api/v1/bp26", headers=headers, json=_bp26_payload(class_id)).json()
    client.post(f"/api/v1/bp26/{issued['id']}/issue", headers=headers)
    resp = client.delete(f"/api/v1/bp26/{issued['id']}", headers=headers)
    assert resp.status_code == 409


def test_cancel_issued_bp26_becomes_invalid(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    created = client.post("/api/v1/bp26", headers=headers, json=_bp26_payload(class_id)).json()

    # Draft cannot be cancelled, only issued slips can.
    early = client.post(f"/api/v1/bp26/{created['id']}/cancel", headers=headers, json={})
    assert early.status_code == 409

    client.post(f"/api/v1/bp26/{created['id']}/issue", headers=headers)
    cancelled = client.post(
        f"/api/v1/bp26/{created['id']}/cancel",
        headers=headers,
        json={"reason": "Salah input penerima"},
    )
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "invalid"
    assert body["invalid_reason"] == "Salah input penerima"


def test_import_xml_rejects_invalid_format(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    resp = client.post(
        "/api/v1/bp26/import-xml",
        headers=headers,
        files={"file": ("bad.xml", b"this is not xml at all", "application/xml")},
    )
    assert resp.status_code == 422
    assert "XML" in resp.json()["detail"]


def test_import_xml_valid_and_invalid_rows(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    ok = client.post(
        "/api/v1/bp26/import-xml",
        headers=headers,
        files={"file": ("ok.xml", _import_xml(class_id).encode(), "application/xml")},
    )
    assert ok.status_code == 200
    ok_body = ok.json()
    assert ok_body["imported"] == 1
    assert ok_body["failed"] == 0
    assert ok_body["results"][0]["id"] is not None

    bad = client.post(
        "/api/v1/bp26/import-xml",
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


def test_import_export_xml_keeps_treaty_fields(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)
    imported = client.post(
        "/api/v1/bp26/import-xml",
        headers=headers,
        files={
            "file": (
                "treaty.xml",
                _import_xml(class_id, tax_nature="non_final", treaty_rate="1000").encode(),
                "application/xml",
            )
        },
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1

    exported = client.get(
        "/api/v1/bp26/export-xml?tax_year=2026&tax_month=7",
        headers=headers,
    )

    assert exported.status_code == 200
    assert "<negara_treaty>SGP</negara_treaty>" in exported.text
    assert "<tarif_treaty_basis_points>1000</tarif_treaty_basis_points>" in exported.text


def test_import_xml_rejects_oversized_upload(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    resp = client.post(
        "/api/v1/bp26/import-xml",
        headers=headers,
        files={"file": ("huge.xml", b"x" * (MAX_IMPORT_BYTES + 1), "application/xml")},
    )
    assert resp.status_code == 413


def test_import_xml_rejects_too_many_rows(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    rows = "".join("<Bp26 />" for _ in range(MAX_IMPORT_ROWS + 1))
    resp = client.post(
        "/api/v1/bp26/import-xml",
        headers=headers,
        files={"file": ("many.xml", f"<Bp26List>{rows}</Bp26List>".encode(), "application/xml")},
    )
    assert resp.status_code == 422


def test_other_tenant_admin_cannot_see_bp26(client: TestClient) -> None:
    siswa_headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, siswa_headers)
    created = client.post(
        "/api/v1/bp26", headers=siswa_headers, json=_bp26_payload(class_id)
    ).json()

    other_admin_headers = auth_headers(client, ADMIN_B)
    listing = client.get("/api/v1/bp26?size=100", headers=other_admin_headers)
    assert listing.status_code == 200
    ids = {item["id"] for item in listing.json()["items"]}
    assert created["id"] not in ids


def test_guru_can_review_bp26_in_own_class(client: TestClient) -> None:
    siswa_headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, siswa_headers)
    created = client.post(
        "/api/v1/bp26", headers=siswa_headers, json=_bp26_payload(class_id)
    ).json()

    guru_headers = auth_headers(client, GURU_A)
    resp = client.patch(
        f"/api/v1/bp26/{created['id']}/review",
        headers=guru_headers,
        json={"score": 88, "teacher_feedback": "Tarif PPh 26 sudah benar."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 88
    assert body["teacher_feedback"] == "Tarif PPh 26 sudah benar."


def test_bulk_issue_and_export_csv(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    first = client.post("/api/v1/bp26", headers=headers, json=_bp26_payload(class_id)).json()
    second = client.post("/api/v1/bp26", headers=headers, json=_bp26_payload(class_id)).json()

    bulk = client.post(
        "/api/v1/bp26/bulk-issue",
        headers=headers,
        json={"ids": [first["id"], second["id"], 99_999_999]},
    )
    assert bulk.status_code == 200
    body = bulk.json()
    assert body["issued"] == 2
    assert body["failed"] == 1

    export = client.get(
        "/api/v1/bp26/export-csv?status=issued&tax_year=2026&tax_month=7",
        headers=headers,
    )
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert "Nomor Pemotongan" in export.text
    assert f"BP26-202607-{first['id']:06d}" in export.text


def test_slip_types_are_isolated_despite_shared_table(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)
    created = client.post("/api/v1/bp26", headers=headers, json=_bp26_payload(class_id)).json()

    bp21_ids = {
        item["id"] for item in client.get("/api/v1/bp21?size=100", headers=headers).json()["items"]
    }
    assert created["id"] not in bp21_ids
    assert client.get(f"/api/v1/bp21/{created['id']}", headers=headers).status_code == 404


def test_bp26_summary_counts_created_statuses_and_respects_tenant(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    other_headers = auth_headers(client, ADMIN_B)
    class_id = _first_class_id(client, headers)
    before = client.get("/api/v1/bp26/summary", headers=headers).json()
    other_before = client.get("/api/v1/bp26/summary", headers=other_headers).json()

    client.post("/api/v1/bp26", headers=headers, json=_bp26_payload(class_id))
    issued = client.post("/api/v1/bp26", headers=headers, json=_bp26_payload(class_id)).json()
    client.post(f"/api/v1/bp26/{issued['id']}/issue", headers=headers)

    after = client.get("/api/v1/bp26/summary", headers=headers).json()
    other_after = client.get("/api/v1/bp26/summary", headers=other_headers).json()
    assert after["draft"] >= before["draft"] + 1
    assert after["issued"] >= before["issued"] + 1
    assert after["total"] >= before["total"] + 2
    assert other_after == other_before
