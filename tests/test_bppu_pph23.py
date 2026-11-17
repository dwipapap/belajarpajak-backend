"""PPh Pasal 23 slips are made through BPPU (the BP23 module was merged into it)."""

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

SISWA = "siswa1@smkn1-pku.local"


def _payload(**over) -> dict:
    return {
        "tax_month": 1,
        "tax_year": 2026,
        "recipient_identity_number": "1234567890123456",
        "recipient_name": "Test PPh23",
        "tax_object_code": "24-101-01",
        "gross_income": 10_000_000,
        "dpp_percent": 100,
        "rate_percent": 15,
        **over,
    }


def test_bppu_pph23_calculation_normal(client: TestClient):
    headers = auth_headers(client, SISWA)
    res = client.post("/api/v1/bppu", json=_payload(), headers=headers)
    assert res.status_code == 201, res.text
    assert res.json()["income_tax"] == 1_500_000  # 15% of 10M


def test_bppu_pph23_jasa_normal_2_percent(client: TestClient):
    headers = auth_headers(client, SISWA)
    res = client.post(
        "/api/v1/bppu",
        json=_payload(tax_object_code="24-104-03", rate_percent=2),
        headers=headers,
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["income_tax"] == 200_000
    assert data["kap_kjs"] == "411124-100"
    assert data["calculation_method"] == "flat"


def test_bppu_pph23_no_npwp_doubled(client: TestClient):
    headers = auth_headers(client, SISWA)
    res = client.post(
        "/api/v1/bppu",
        json=_payload(
            recipient_identity_number="0000000000000000",
            recipient_name="No NPWP PPh23",
            tax_object_code="24-104-03",
            rate_percent=2,
        ),
        headers=headers,
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["income_tax"] == 400_000  # 2% x2 = 4% of 10M
    assert data["calculation_method"] == "flat_x2"


def test_bppu_non_pasal_23_object_not_doubled(client: TestClient):
    """The x2 rule is for Pasal 23 objects only (28-409-10 is Pasal 4(2))."""
    headers = auth_headers(client, SISWA)
    res = client.post(
        "/api/v1/bppu",
        json=_payload(
            recipient_identity_number="0000000000000000",
            tax_object_code="28-409-10",
            rate_percent=2,
        ),
        headers=headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["calculation_method"] == "flat"


def test_bp23_endpoint_is_gone(client: TestClient):
    headers = auth_headers(client, SISWA)
    assert client.post("/api/v1/bp23", json=_payload(), headers=headers).status_code == 404


def test_bppu_pph23_posts_to_spt_unifikasi_pasal_23(client: TestClient):
    from tests.test_spt import _create_spt, _posting

    headers = auth_headers(client, SISWA)
    res = client.post(
        "/api/v1/bppu",
        json=_payload(tax_month=4, tax_object_code="24-104-03", rate_percent=2),
        headers=headers,
    )
    assert res.status_code == 201, res.text
    issued = client.post(
        f"/api/v1/bppu/{res.json()['id']}/issue",
        headers=headers,
        json={"kode_otorisasi": "KO-SIM-123"},
    )
    assert issued.status_code == 200, issued.text

    spt = _create_spt(client, headers, 4, return_type="pph_unifikasi")
    posted = _posting(client, headers, spt["id"])
    line = next(
        x for x in posted["lines"] if x["section"] == "pasal_23" and x["code"] == "411124-100"
    )
    assert line["pemotongan"] >= 200_000
