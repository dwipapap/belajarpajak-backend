"""Integration tests for client feedback updates:
1. Manual PPN/DPP/DPP Nilai Lain/PPnBM on e-Faktur Keluaran
2. Guru Pajak Masukan broadcast to all class students
3. BP21 manual rate without SIM-TER
4. BPMP manual rate & manual income_tax
5. Guru self-authored PPh 21 (BP21 & BPMP) + shared guru-examples for students
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

GURU_A = "guru1@smkn1-pku.local"
SISWA_A = "siswa1@smkn1-pku.local"


def _first_class_id(client: TestClient, headers: dict[str, str]) -> int:
    classes = client.get("/api/v1/classes", headers=headers).json()
    assert classes
    return classes[0]["id"]


def test_faktur_keluaran_preserves_manual_tax_amounts(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    res = client.post(
        "/api/v1/faktur-keluaran",
        headers=headers,
        json={
            "class_id": class_id,
            "is_advance_payment": False,
            "is_settlement": False,
            "transaction_code": "01",
            "invoice_date": "2026-06-15",
            "invoice_kind": "normal",
            "tax_month": 6,
            "tax_year": 2026,
            "buyer_identity_type": "npwp",
            "buyer_identity_number": "1234567890123456",
            "buyer_country": "ID",
            "buyer_name": "PT Pembeli Manual",
            "lines": [
                {
                    "line_type": "barang",
                    "item_code": "000000",
                    "item_name": "Barang Manual",
                    "unit": "Unit",
                    "unit_price": 1_000_000,
                    "quantity": 1,
                    "discount": 0,
                    "dpp": 900_000,
                    "use_dpp_other": True,
                    "dpp_other": 825_000,
                    "ppn_rate_percent": 12,
                    "ppn": 99_000,
                    "ppnbm_rate_percent": 5,
                    "ppnbm": 45_000,
                }
            ],
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    line = body["lines"][0]
    assert line["total_price"] == 1_000_000
    assert line["dpp"] == 900_000
    assert line["dpp_other"] == 825_000
    assert line["ppn"] == 99_000
    assert line["ppnbm"] == 45_000
    assert body["total_dpp"] == 900_000
    assert body["total_dpp_other"] == 825_000
    assert body["total_ppn"] == 99_000
    assert body["total_ppnbm"] == 45_000


def test_bpmp_accepts_manual_rate_and_income_tax(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, headers)

    res = client.post(
        "/api/v1/bpmp",
        headers=headers,
        json={
            "class_id": class_id,
            "tax_month": 6,
            "tax_year": 2026,
            "recipient_identity_number": "1234567890123456",
            "recipient_name": "Pegawai Tetap Manual",
            "ptkp_status": "TK/0",
            "tax_type": "PPh 21",
            "tax_object_code": "21-100-01",
            "tax_nature": "non_final",
            "tax_facility": "none",
            "gross_income": 10_000_000,
            "dpp_percent": 100,
            "rate_percent": 2.5,
            "income_tax": 250_000,
            "kap_kjs": "411121-100",
            "document_type": "Bukti Pembayaran",
            "document_number": "PAY/2026/MANUAL-1",
            "document_date": "2026-06-28",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["rate_percent"] == 2.5
    assert body["income_tax"] == 250_000
    assert body["calculation_method"] == "manual"


def test_guru_can_create_issue_and_share_pph21_examples(client: TestClient) -> None:
    guru_headers = auth_headers(client, GURU_A)
    siswa_headers = auth_headers(client, SISWA_A)
    class_id = _first_class_id(client, guru_headers)

    created = client.post(
        "/api/v1/bp21",
        headers=guru_headers,
        json={
            "class_id": class_id,
            "tax_month": 6,
            "tax_year": 2026,
            "recipient_identity_number": "3273010101010099",
            "recipient_name": "Contoh Tenaga Ahli Guru",
            "ptkp_status": "TK/0",
            "tax_object_code": "21-100-07",
            "income_type": "Imbalan tenaga ahli",
            "tax_nature": "non_final",
            "tax_facility": "none",
            "gross_income": 10_000_000,
            "dpp_percent": 50,
            "rate_percent": 5,
            "kap_kjs": "411121-100",
            "document_type": "Bukti Pembayaran",
            "document_number": "CONTOH-GURU-01",
            "document_date": "2026-06-10",
        },
    )
    assert created.status_code == 201, created.text
    slip = created.json()
    assert slip["income_tax"] == 250_000
    assert slip["calculation_method"] == "manual"

    issued = client.post(
        f"/api/v1/bp21/{slip['id']}/issue",
        headers=guru_headers,
        json={"kode_otorisasi": "KO-GURU-123"},
    )
    assert issued.status_code == 200, issued.text

    # Guru sees it in mine_only=true
    mine = client.get("/api/v1/bp21?mine_only=true&size=50", headers=guru_headers).json()["items"]
    assert any(item["id"] == slip["id"] for item in mine)

    # Enrolled student sees it in /guru-examples and can download PDF, but cannot mutate it
    examples = client.get("/api/v1/bp21/guru-examples", headers=siswa_headers)
    assert examples.status_code == 200, examples.text
    assert any(item["id"] == slip["id"] for item in examples.json())

    pdf = client.get(f"/api/v1/bp21/{slip['id']}/pdf", headers=siswa_headers)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")

    forbidden_cancel = client.post(
        f"/api/v1/bp21/{slip['id']}/cancel",
        headers=siswa_headers,
        json={"reason": "coba ubah contoh guru"},
    )
    assert forbidden_cancel.status_code == 404
