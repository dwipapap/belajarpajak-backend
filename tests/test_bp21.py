"""Smoke tests for simulated e-Bupot BP21 workflows."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

ADMIN_B = "admin@pcr.local"
GURU_A = "guru1@smkn1-pku.local"
SISWA_A = "siswa1@smkn1-pku.local"


def _first_class_id(client: TestClient, headers: dict[str, str]) -> int:
    classes = client.get("/api/v1/classes", headers=headers).json()
    assert classes
    return classes[0]["id"]


def _bp21_payload(class_id: int) -> dict:
    return {
        "class_id": class_id,
        "tax_month": 7,
        "tax_year": 2026,
        "withholder_npwp": "1471110802000001",
        "withholder_name": "PT Simulasi Pajak Nusantara",
        "withholder_nitku": "1471110802000001000001",
        "recipient_identity_number": "3273010101010001",
        "recipient_name": "Siswa Simulasi BP21",
        "recipient_address": "Jl. Pendidikan No. 21",
        "recipient_nitku": "3273010101010001000000",
        "ptkp_status": "TK/0",
        "tax_object_code": "21-100-09",
        "income_type": "Honorarium tenaga ahli",
        "tax_nature": "non_final",
        "tax_facility": "none",
        "gross_income": 5_000_000,
        "dpp_percent": "100.00",
        "rate_percent": "5.00",
        "kap_kjs": "411121-100",
        "document_type": "Invoice",
        "document_number": "INV/2026/001",
        "document_date": "2026-07-01",
        "document_nitku": "1471110802000001000001",
    }


def test_siswa_can_create_and_issue_bp21(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    created = client.post("/api/v1/bp21", headers=headers, json=_bp21_payload(class_id))
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "draft"
    assert body["income_tax"] == 250_000
    assert body["withholding_number"] is None

    issued = client.post(f"/api/v1/bp21/{body['id']}/issue", headers=headers)
    assert issued.status_code == 200
    issued_body = issued.json()
    assert issued_body["status"] == "issued"
    assert issued_body["withholding_number"].startswith("BP21-202607-")
    assert issued_body["electronic_signature_status"] == "signed"


def test_siswa_cannot_invalidate_bp21(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)
    created = client.post("/api/v1/bp21", headers=headers, json=_bp21_payload(class_id)).json()

    resp = client.post(
        f"/api/v1/bp21/{created['id']}/invalidate",
        headers=headers,
        json={"invalid_reason": "Data tidak sesuai"},
    )
    assert resp.status_code == 403


def test_other_tenant_admin_cannot_see_bp21(client: TestClient) -> None:
    siswa_headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, siswa_headers)
    created = client.post(
        "/api/v1/bp21", headers=siswa_headers, json=_bp21_payload(class_id)
    ).json()

    other_admin_headers = auth_headers(client, ADMIN_B)
    listing = client.get("/api/v1/bp21?size=100", headers=other_admin_headers)
    assert listing.status_code == 200
    ids = {item["id"] for item in listing.json()["items"]}
    assert created["id"] not in ids


def test_guru_can_review_bp21_in_own_class(client: TestClient) -> None:
    siswa_headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, siswa_headers)
    created = client.post(
        "/api/v1/bp21", headers=siswa_headers, json=_bp21_payload(class_id)
    ).json()

    guru_headers = auth_headers(client, GURU_A)
    resp = client.patch(
        f"/api/v1/bp21/{created['id']}/review",
        headers=guru_headers,
        json={"score": 90, "teacher_feedback": "Perhitungan sudah sesuai."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 90
    assert body["teacher_feedback"] == "Perhitungan sudah sesuai."


def test_bp21_summary_counts_created_statuses_and_respects_tenant(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    other_headers = auth_headers(client, ADMIN_B)
    class_id = _first_class_id(client, headers)
    before = client.get("/api/v1/bp21/summary", headers=headers).json()
    other_before = client.get("/api/v1/bp21/summary", headers=other_headers).json()

    client.post("/api/v1/bp21", headers=headers, json=_bp21_payload(class_id))
    issued = client.post("/api/v1/bp21", headers=headers, json=_bp21_payload(class_id)).json()
    client.post(f"/api/v1/bp21/{issued['id']}/issue", headers=headers)

    after = client.get("/api/v1/bp21/summary", headers=headers).json()
    other_after = client.get("/api/v1/bp21/summary", headers=other_headers).json()
    assert after["draft"] >= before["draft"] + 1
    assert after["issued"] >= before["issued"] + 1
    assert after["total"] >= before["total"] + 2
    assert other_after == other_before
