from fastapi.testclient import TestClient

from tests.conftest import auth_headers


def test_bp23_calculation_normal(client: TestClient):
    payload = {
        "tax_month": 1,
        "tax_year": 2026,
        "recipient_identity_number": "1234567890123456",
        "recipient_name": "Test BP23",
        "tax_object_code": "24-100-01",
        "gross_income": 10000000,
        "dpp_percent": 100,
        "rate_percent": 15
    }
    headers = auth_headers(client, "siswa1@smkn1-pku.local")
    res = client.post("/api/v1/bp23", json=payload, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["income_tax"] == 1500000 # 15% of 10M

def test_bp23_calculation_no_npwp(client: TestClient):
    payload = {
        "tax_month": 1,
        "tax_year": 2026,
        "recipient_identity_number": "0000000000000000",
        "recipient_name": "No NPWP BP23",
        "tax_object_code": "24-100-01",
        "gross_income": 10000000,
        "dpp_percent": 100,
        "rate_percent": 15
    }
    headers = auth_headers(client, "siswa1@smkn1-pku.local")
    res = client.post("/api/v1/bp23", json=payload, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["income_tax"] == 3000000 # 30% of 10M because no NPWP (000...)
