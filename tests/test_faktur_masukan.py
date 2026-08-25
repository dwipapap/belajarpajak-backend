from fastapi.testclient import TestClient

from tests.conftest import auth_headers
from tests.test_faktur_keluaran import _line as keluaran_line
from tests.test_faktur_keluaran import _payload as keluaran_payload

SISWA = "siswa1@smkn1-pku.local"
OTHER_SISWA = "siswa4@smkn1-pku.local"
GURU = "guru1@smkn1-pku.local"
ADMIN = "admin@smkn1-pku.local"
SUPERADMIN = "super@pajaksim.local"
PCR_ADMIN = "admin@pcr.local"


def _line(**overrides) -> dict:
    line = {
        "line_type": "barang",
        "item_code": "010000",
        "item_name": "Kertas HVS A4",
        "unit": "Paket",
        "unit_price": 1_000_000,
        "quantity": 1,
        "discount": 0,
        "use_dpp_other": False,
        "ppn_rate_percent": 12,
        "ppnbm_rate_percent": 0,
    }
    line.update(overrides)
    return line


def _payload(**overrides) -> dict:
    payload = {
        "transaction_code": "01",
        "invoice_date": "2026-07-18",
        "tax_month": 7,
        "tax_year": 2026,
        "buyer_identity_number": "0013457668062000",
        "buyer_name": "PT Pembeli Simulasi",
        "buyer_address": "JL POS PENGUMBEN RAYA NO.8",
        "seller_name": "PT Penjual Simulasi",
        "seller_npwp": "0023456789012000",
        "lines": [_line()],
    }
    payload.update(overrides)
    return payload


def _create(client: TestClient, headers: dict, **overrides) -> dict:
    res = client.post("/api/v1/faktur-masukan", json=_payload(**overrides), headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_create_normal_code_uses_full_dpp(client: TestClient):
    headers = auth_headers(client, SISWA)
    data = _create(client, headers)

    assert data["status"] == "draft"
    assert data["credit_status"] == "not_credited"
    assert data["total_dpp"] == 1_000_000
    assert data["total_dpp_other"] == 0
    assert data["total_ppn"] == 120_000


def test_transaction_code_04_forces_dpp_nilai_lain(client: TestClient):
    headers = auth_headers(client, SISWA)
    data = _create(client, headers, transaction_code="04")

    assert data["total_dpp"] == 1_000_000
    assert data["total_dpp_other"] == 916_667
    assert data["total_ppn"] == 110_000
    assert data["lines"][0]["use_dpp_other"] is True


def test_credit_changes_status_and_credited_at(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)

    res = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["credit_status"] == "credited"
    assert data["credited_at"] is not None

    again = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=headers)
    assert again.status_code == 409


def test_uncredit_reverts(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=headers)

    res = client.post(f"/api/v1/faktur-masukan/{created['id']}/uncredit", headers=headers)
    assert res.status_code == 200
    assert res.json()["credit_status"] == "not_credited"
    assert res.json()["credited_at"] is None

    again = client.post(f"/api/v1/faktur-masukan/{created['id']}/uncredit", headers=headers)
    assert again.status_code == 409


def test_bulk_credit_partial_success(client: TestClient):
    headers = auth_headers(client, SISWA)
    valid = _create(client, headers)
    already = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{already['id']}/credit", headers=headers)

    other_headers = auth_headers(client, OTHER_SISWA)
    other = _create(client, other_headers)

    res = client.post(
        "/api/v1/faktur-masukan/bulk-credit",
        json={"ids": [valid["id"], already["id"], other["id"], 999_999]},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["credited"] == 1
    assert data["skipped"] == 3
    assert len(data["errors"]) == 3

    state = client.get(f"/api/v1/faktur-masukan/{valid['id']}", headers=headers).json()
    assert state["credit_status"] == "credited"


def test_bulk_uncredit_partial_success(client: TestClient):
    headers = auth_headers(client, SISWA)
    a = _create(client, headers)
    b = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{a['id']}/credit", headers=headers)
    client.post(f"/api/v1/faktur-masukan/{b['id']}/credit", headers=headers)

    res = client.post(
        "/api/v1/faktur-masukan/bulk-uncredit",
        json={"ids": [a["id"], 999_999]},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["uncredited"] == 1


def test_delete_only_draft_not_credited(client: TestClient):
    headers = auth_headers(client, SISWA)
    draft = _create(client, headers)
    removed = client.delete(f"/api/v1/faktur-masukan/{draft['id']}", headers=headers)
    assert removed.status_code == 204

    to_credit = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{to_credit['id']}/credit", headers=headers)
    denied = client.delete(f"/api/v1/faktur-masukan/{to_credit['id']}", headers=headers)
    assert denied.status_code == 409


def test_siswa_cannot_credit_another_siswa_invoice(client: TestClient):
    owner_headers = auth_headers(client, SISWA)
    created = _create(client, owner_headers)

    intruder_headers = auth_headers(client, OTHER_SISWA)
    res = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=intruder_headers)
    # Can_read fails -> 404 hide
    assert res.status_code == 404


def test_guru_can_review_but_siswa_cannot(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)
    classes = client.get("/api/v1/classes", headers=siswa_headers).json()
    created = _create(client, siswa_headers, class_id=classes[0]["id"])

    guru_headers = auth_headers(client, GURU)
    res = client.patch(
        f"/api/v1/faktur-masukan/{created['id']}/review",
        json={"score": 90, "teacher_feedback": "Kredit sudah tepat"},
        headers=guru_headers,
    )
    assert res.status_code == 200
    assert res.json()["score"] == 90

    denied = client.patch(
        f"/api/v1/faktur-masukan/{created['id']}/review",
        json={"score": 100},
        headers=siswa_headers,
    )
    assert denied.status_code == 403


def test_guru_cannot_credit(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)
    class_id = client.get("/api/v1/classes", headers=siswa_headers).json()[0]["id"]
    created = _create(client, siswa_headers, class_id=class_id)

    guru_headers = auth_headers(client, GURU)
    res = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=guru_headers)
    assert res.status_code == 403


def test_summary_counts(client: TestClient):
    headers = auth_headers(client, SISWA)
    before = client.get("/api/v1/faktur-masukan/summary", headers=headers).json()
    _create(client, headers)
    after = client.get("/api/v1/faktur-masukan/summary", headers=headers).json()
    assert after["total"] == before["total"] + 1


def test_source_invoice_copy(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)
    # Create and issue a keluaran source

    keluaran = client.post(
        "/api/v1/faktur-keluaran",
        json=keluaran_payload(transaction_code="01", lines=[keluaran_line()]),
        headers=siswa_headers,
    )
    assert keluaran.status_code == 201
    kid = keluaran.json()["id"]
    issued = client.post(f"/api/v1/faktur-keluaran/{kid}/issue", headers=siswa_headers)
    assert issued.status_code == 200
    source_number = issued.json()["invoice_number"]
    assert source_number is not None

    # Create masukan via source
    res = client.post(
        "/api/v1/faktur-masukan",
        json=_payload(source_invoice_id=kid, lines=[]),
        headers=siswa_headers,
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["source_invoice_id"] == kid
    assert data["invoice_number"] == source_number
    assert len(data["lines"]) == 1


def test_source_must_be_issued(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)

    keluaran = client.post("/api/v1/faktur-keluaran", json=keluaran_payload(), headers=siswa_headers)  # noqa: E501
    kid = keluaran.json()["id"]
    # Not issued yet
    res = client.post("/api/v1/faktur-masukan", json=_payload(source_invoice_id=kid), headers=siswa_headers)  # noqa: E501
    assert res.status_code == 409


def test_list_filters_by_credit_status(client: TestClient):
    headers = auth_headers(client, SISWA)
    a = _create(client, headers)
    b = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{b['id']}/credit", headers=headers)

    res = client.get("/api/v1/faktur-masukan?credit_status=credited", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert all(item["credit_status"] == "credited" for item in items)
    assert any(item["id"] == b["id"] for item in items)
    assert not any(item["id"] == a["id"] for item in items)


def test_inaccessible_mutations_return_404(client: TestClient):
    owner = auth_headers(client, SISWA)
    created = _create(client, owner)

    intruder = auth_headers(client, OTHER_SISWA)
    patch = client.patch(f"/api/v1/faktur-masukan/{created['id']}", json={"buyer_name": "Hack"}, headers=intruder)  # noqa: E501
    assert patch.status_code == 404

    delete = client.delete(f"/api/v1/faktur-masukan/{created['id']}", headers=intruder)
    assert delete.status_code == 404

    credit = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=intruder)
    assert credit.status_code == 404
